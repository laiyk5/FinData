# Custom datasets and providers

findata discovers plugins through Python entry points. To manage your own dataset, you
write a small Python package that declares the same contracts the built-in plugins use
— no changes to findata itself.

Your plugin fetches from a provider, transforms and validates rows, and submits Arrow
tables through the core transactional writer. **Core owns everything else**: database
creation, transactions, coverage tracking, checkpointing, crash recovery, task
management, cron scheduling, the CLI, and the Web UI. Your plugin **never opens DuckDB,
never emits SQL, and never defines read semantics** — reads always go through
[DataLoader](dataloader.md).

---

## Get started in two minutes

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

## How it works

### Naming: the package namespace

A plugin's full name is `<package-namespace>/<local-name>` — for example
`findata-plugins/tushare_daily_basic`. The namespace is the **Python package name**
([PEP 420](https://packaging.python.org/guides/packaging-namespace-packages/)),
derived from the entry point's module path. Discovery rejects a plugin whose full
name doesn't match its package, so a plugin can never impersonate another namespace.

A PEP 420 namespace package is organized by convention:

```
mycompany/                    # PEP 420 — no __init__.py
  plugins/
    providers/hello/          # provider distribution (optional)
    datasets/hello/           # dataset distribution
```

Each leaf is an independent distribution. Install one or all — the namespace makes
them appear as a coherent tree.

### The three contract objects

**`DatasetSpec`** — the logical schema and provider contract:

```python
from findata.sdk import DatasetSpec

spec = DatasetSpec(
    name="mycompany/hello",               # full name
    api_name="hello",                     # provider API name (for error messages)
    schema=pa.schema([ ... ]),            # pyarrow.Schema of the table
    provider_fields=("name", "greeting"),  # fields the provider must return
    primary_key=("name",),                # must be non-null and unique
    partition_key="name",                 # enables keys= queries
    time_field="trade_date",              # enables time_range + coverage tracking
)
```

**`ProviderPlugin`** — declares a data source with configuration schema and rate
limits. The scaffold creates an always-ready one; real providers require credentials:

```python
from findata.sdk import ProviderPlugin

ProviderPlugin(
    provider_id="mycompany/myprovider",
    configuration_schema={"type": "object", "properties": {"token": ...}},
    secret_fields=("token",),
    rate_limit=300, period=60,
    runtime=MyProviderRuntime(),
)
```

**`DatasetPlugin`** — binds a spec to a runtime and declares operations:

```python
from findata.sdk import DatasetPlugin, SettingSpec

DatasetPlugin(
    name=spec.name,
    provider="myprovider",
    spec=spec,
    runtime=MyRuntime(),
    operations=("update", "complete", "refresh"),  # "update" is mandatory
    settings={
        "dataset.mycompany/hello.update_symbols": SettingSpec(
            schema={"type": "array", "items": {"type": "string"}},
            normalize=normalize_symbols,
            help="Symbols to maintain.",
            required=True,
        ),
    },
)
```

### Entry points

Publish the contracts from `pyproject.toml`:

```toml
[project.entry-points."findata.providers"]
myprovider = "mycompany.plugins.providers.myprovider:provider_plugin"

[project.entry-points."findata.datasets"]
hello = "mycompany.plugins.datasets.hello:hello_plugin"
```

### Runtime protocols

**`ProviderRuntime`** — three methods:

| Method | Called for |
|---|---|
| `ready(workspace, mode)` | provider readiness shown by `provider status` |
| `is_mock(workspace, mode)` | whether the provider returns mock data |
| `probe(workspace, *, today)` | optional authenticated readiness check (`provider check`) |

**`DatasetRuntime`** — seven methods. Use `DatasetRuntimeBase` (as the scaffold does)
to get sensible defaults for all but `operation_worker`:

| Method | Called for |
|---|---|
| `operation_worker(...)` | **must override** — returns the pickle-safe callable executed in a task subprocess |
| `normalize_operation(op, operands, *, today)` | canonicalize/validate operands (default: pass-through) |
| `plan_operation(workspace, op, operands, *, today)` | `--dry-run` preview |
| `dataset_description(workspace, *, provider_ready)` | `dataset describe/status` payload |
| `operation_description(operation)` | operand JSON schema + help |
| `resolve_dependency(target, requirement)` | map a dependency to a fulfilling operation |
| `update_ready(workspace)` | whether parameterless `update` can proceed (default: `True`) |

---

## Testing your plugin

The `findata.testing` module provides helpers:

```python
from findata.testing import (
    RecordingReporter,        # capture log/diagnostic/progress calls
    FakeDatasetRuntime,       # configurable runtime for registration tests
    make_provider_plugin,     # build a minimal ProviderPlugin
    make_dataset_plugin,      # build a minimal DatasetPlugin
    create_test_workspace,    # temp workspace context manager
)
```

Example:

```python
from findata.testing import create_test_workspace, make_dataset_plugin

with create_test_workspace(plugins=[my_plugin]) as ws:
    assert my_plugin.name in ws.datasets_root.rglob("dataset.duckdb")
```

---

## Reference implementation

The built-in Tushare plugins are the canonical example of every rule on this page.
Their source lives in `plugins/tushare/` in the repository under the
`findata_plugins` namespace package.
