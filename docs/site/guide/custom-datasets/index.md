# Custom datasets and providers

findata discovers plugins through Python entry points. To manage your own dataset, you
write a small Python package that declares the same contracts the built-in plugins use
— no changes to findata itself.

Your plugin fetches from a provider, transforms and validates rows, and submits Arrow
tables through the core transactional writer. **Core owns everything else**: database
creation, transactions, coverage tracking, checkpointing, crash recovery, task
management, cron scheduling, the CLI, and the Web UI. Your plugin **never opens DuckDB,
never emits SQL, and never defines read semantics** — reads always go through
[DataLoader](../dataloader.md).

---

## Quick start

Use the scaffold command to generate a complete plugin family:

```bash
findata plugin scaffold mycompany hello
```

This creates:

```
mycompany/
  provider/pyproject.toml
  provider/src/mycompany/plugins/providers/hello/__init__.py
  provider/src/mycompany/plugins/providers/hello/provider.py   # always-ready provider
  datasets/hello/pyproject.toml
  datasets/hello/src/mycompany/plugins/datasets/hello/__init__.py
  datasets/hello/src/mycompany/plugins/datasets/hello/operations.py  # <-- your data logic
  umbrella/pyproject.toml
```

The provider is always-ready (no credentials). The dataset generates placeholder rows
using `DatasetRuntimeBase` — you override just `operation_worker`.

### Install and run

```bash
pip install -e ./mycompany/provider ./mycompany/datasets/hello
findata-server init ~/my-workspace
findata-server start ~/my-workspace --provider-mode mock
```

In another terminal:

```bash
findata plugin check hello
findata task run mycompany/hello complete --param rows=3 --wait
```

```python
from findata import DataLoader
print(DataLoader("~/my-workspace").dataset("mycompany/hello").query())
```

### Add your data logic

Edit `mycompany/datasets/hello/src/mycompany/plugins/datasets/hello/operations.py`.
The scaffold generates a `Worker.__call__` method with placeholder comments — replace
the `_fetch_data` method with your actual data source call.

The general pattern:

```python
class Worker:
    def __call__(self, request, context):
        # 1. Fetch — call your data source
        rows = self._fetch_data(request["operands"])

        # 2. Transform and validate through the spec contract
        table = SPEC.table_from_response(FIELDS, [
            [row.get(f) for f in FIELDS] for row in rows
        ])

        # 3. Publish atomically
        pub = Workspace(self.workspace).publisher(SPEC.name)
        return {"publication_id": pub.publish(table), "rows": len(rows)}

    def _fetch_data(self, operands):
        # Replace with your actual fetch logic
        ...
```

For a coverage-tracked dataset (one with a `time_field`), pass `Coverage` to publish:

```python
from findata.storage import Coverage
pub.publish(table, coverage=[Coverage(key=ticker, start=start, end=end)])
```

---

## Next steps

- [SDK reference](sdk-reference.md) — naming, contracts, entry points, runtime protocols
- [How to test your plugin](how-to/test.md) — testing with `findata.testing`
