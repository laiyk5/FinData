# SDK reference

## Naming: the package namespace

A plugin's full name is `<package-namespace>/<local-name>` — for example
`findata-test/demo_random`. The namespace is the **Python package name**
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

## The three contract objects

### `DatasetSpec` — the dataset contract

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

### `ProviderPlugin` — the provider contract

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

### `DatasetPlugin` — binds a spec to a runtime

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

## Entry points

Publish the contracts from `pyproject.toml`:

```toml
[project.entry-points."findata.providers"]
myprovider = "mycompany.plugins.providers.myprovider:provider_plugin"

[project.entry-points."findata.datasets"]
hello = "mycompany.plugins.datasets.hello:hello_plugin"
```

## Runtime protocols

### `ProviderRuntime` — three methods

| Method | Called for |
|---|---|
| `ready(workspace, mode)` | provider readiness shown by `provider status` |
| `is_mock(workspace, mode)` | whether the provider returns mock data |
| `probe(workspace, *, today)` | optional authenticated readiness check (`provider check`) |

### `DatasetRuntime` — seven methods

Use `DatasetRuntimeBase` (as the scaffold does) to get sensible defaults for all but
`operation_worker`:

| Method | Default | When to override |
|---|---|---|
| `operation_worker(...)` | **None (must override)** | Always — returns the pickle-safe callable executed in a task subprocess |
| `normalize_operation(op, operands, *, today)` | Pass-through (returns operands as-is) | When your operation accepts specific named operands |
| `plan_operation(...)` | Generic dry-run with no estimates | When you want to show request counts or strategy |
| `dataset_description(...)` | Reads storage state via DataLoader | Only if you need custom description fields |
| `operation_description(operation)` | Returns {"name", "help", "required", "properties"} | Only if you want per-operation help text |
| `resolve_dependency(target, requirement)` | Raises ValueError ("no dependencies") | Only if your dataset has data dependencies |
| `update_ready(workspace)` | Returns `True` | Only if update requires configured settings |

**In practice:** A simple dataset only overrides `operation_worker`. The scaffold does exactly
that — the other six methods come from `DatasetRuntimeBase` with sensible defaults.

## Reference implementation

The demo plugins under `plugins/demo/` in the repository are a complete working
reference for every rule on this page. The official Tushare plugins under
`plugins/tushare/` are a more complex example featuring real API integration.
