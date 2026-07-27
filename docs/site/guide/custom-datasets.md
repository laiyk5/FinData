# Custom datasets and providers

findata is plugin-oriented: every dataset it maintains — including the built-in Tushare
datasets — is defined by a **dataset plugin** bound to a **provider plugin**, both
discovered through Python entry points. To manage your own dataset with findata, you
write a small Python package that declares the same contracts the built-in plugins use.
There is no configuration-file-only path; the extension API is code.

Mounting needs **no changes to findata itself**: installing your distribution is the
only step — the next server start discovers, validates, registers, and serves the
plugin, and uninstalling removes it. Official and third-party plugins go through the
exact same mechanism; the design goals and invariants behind this are recorded in
`docs/design/plugins.md` in the repository.

Once declared, your dataset gets the full operational surface for free: `dataset
ls/describe/status/operations`, `task run` with your operations, cron scheduling,
plugin-owned settings, Web UI forms, transactional commits with coverage tracking, and
safe concurrent reads through [DataLoader](dataloader.md).

!!! note "API stability"
    Everything on this page is the supported public API, typed and importable from one
    stop — `findata.plugins` (re-exporting the worker contracts from
    `findata.contracts`): `DatasetSpec`, `ProviderPlugin`, `DatasetPlugin`,
    `SettingSpec`, `ProviderRuntime`, `DatasetRuntime`, `OperationRequest`,
    `OperationReporter`, and `OperationWorker`. Discovery rejects a provider or dataset
    plugin whose runtime does not satisfy its protocol. The built-in Tushare
    implementation remains the reference example.

## Naming: the package namespace

A plugin's full name is `<package-namespace>/<local-name>`, for example
`findata-plugins/tushare_daily_basic`. The first component is the Python **namespace
package** ([PEP 420](https://packaging.python.org/guides/packaging-namespace-packages/))
that one publisher's distributions share. It is **derived from the module path** of the
entry point, not arbitrarily chosen:

- An entry point at module
  ``acme_finance.plugins.datasets.daily_bars`` → namespace ``acme-finance``
- The plugin's full name must be ``acme-finance/daily-bars``

Discovery validates this automatically — a plugin can never claim a namespace that
doesn't match its Python package. Within the namespace, the local name is your own
classification, at any depth; core treats it as an opaque path.

Each dataset plugin registers exactly one dataset, so the full name also addresses the
dataset everywhere: storage, snapshots, and configuration keys follow it
(``datasets/acme-finance/daily-bars/``,
``dataset.acme-finance/daily-bars.<setting>``).

A namespace package is organized by convention:

```
acme_finance/                  # PEP 420 — no __init__.py
  shared/                      # shared machinery, no entry points
  plugins/
    providers/
      mydatasrc/               # provider plugin distribution
        __init__.py
        provider.py
    datasets/
      daily_bars/              # dataset plugin distribution
        __init__.py
        operations.py
```

Each leaf under ``plugins/`` is an **independent distribution** that contributes a
subpackage into the shared namespace. Install one leaf or all of them — the namespace
(PEP 420) makes them appear as a coherent tree without any leaf depending on another.

Distribution names are free-form but should reflect the namespace and plugin type
for clarity — for example ``mycompany-provider-mydatasrc`` and
``mycompany-datasets-daily-bars``. The official ``findata-plugins`` family uses the
``findata-provider-*`` / ``findata-dataset-*`` convention; this is a project policy
for that namespace, not a framework-enforced rule.

Installed plugins mount automatically; a workspace can block yours via the
``plugins.blocked`` config key — unless another mounted plugin requires it, in which case
the block is ineffective and a warning is logged.

## Architecture in one paragraph

Your plugin fetches from the provider, transforms and validates rows, and submits Arrow
tables through the core transactional writer. **Core owns everything else**: database
creation, the per-dataset gate, transactions, coverage mutation, checkpointing, crash
recovery, scheduling, task management, and the CLI/Web UI/HTTP surface. Your plugin
**never opens DuckDB, never emits SQL, and never defines read semantics** — reads always
go through DataLoader.

## The three contract objects

### `DatasetSpec` — the dataset contract

```python
from findata.contracts import DatasetSpec

spec = DatasetSpec(
    name="acme/finance/daily_bars",      # full name: <author>/<free/path>
    api_name="daily_bars",               # your provider's API name, used in errors
    schema=my_arrow_schema,              # pyarrow.Schema of the logical table
    provider_fields=("symbol", "date", "close"),  # fields the provider must return
    primary_key=("symbol", "date"),
    partition_key="symbol",              # optional: enables keys= queries
    time_field="date",                   # optional: enables time_range + coverage
    missing_data_policy="strict",        # strict | accept-empty | best-effort
    normalize_rows=my_normalize,         # optional row dict -> row dict hook
)
```

Rules enforced by core: primary-key fields are non-null and unique per committed
revision; a missing declared provider field is an error; undeclared extra provider
fields are ignored. `time_field` makes the dataset coverage-tracked, which enables
`require_coverage` reads and the `data coverage` command.

### `ProviderPlugin` — the provider contract

```python
from findata.plugins import ProviderPlugin

plugin = ProviderPlugin(
    provider_id="myprovider",
    configuration_schema={               # JSON schema for provider.* config keys
        "type": "object",
        "properties": {"token": {"type": ["string", "object"]}},
    },
    secret_fields=("token",),            # redacted everywhere; never logged
    rate_limit=300,                      # shared limiter: 300 requests ...
    period=60,                           # ... per 60 seconds
    runtime=MyProviderRuntime(),         # see the runtime protocol below
)
```

plugin = DatasetPlugin(
    name="acme/finance/daily_bars",      # must equal spec.name
    provider="myprovider",               # must be a registered provider_id
    spec=spec,
    runtime=MyDatasetRuntime(),          # see the runtime protocols below
    operations=("update", "complete"),   # "update" is mandatory and parameterless
    dependencies=("finance/stock_basic",),  # namespace-relative; must stay acyclic
    schedule=("30 18 * * 1-5", "Asia/Shanghai"),  # optional suggested cron
    settings={
        "dataset.acme/finance/daily_bars.update_symbols": SettingSpec(
            schema={"type": "array", "items": {"type": "string"}},
            normalize=normalize_symbols, # (value, workspace) -> normalized value
            help="Symbols selected for parameterless update.",
            required=True,               # gates update readiness; clients warn
        ),
    },
)
```

Validation at load time rejects malformed or duplicate dataset names, plugin/spec name
mismatches, runtimes that do not satisfy `DatasetRuntime`, unknown providers, a missing
`update` operation, unknown dependencies, and dependency cycles. Dependency names may be
full names or namespace-relative (`"finance/stock_basic"` above resolves to
`acme/finance/stock_basic`); they declare **data** dependencies only — the packages
providing them are never imported.

## Registration: entry points

Publish both contracts from your own package's `pyproject.toml`:

```toml
[project.entry-points."findata.providers"]
myprovider = "acme_finance.provider:myprovider_plugin"

[project.entry-points."findata.datasets"]
daily_bars = "acme_finance_daily_bars:plugin"
```

Each entry point is the contract object itself or a zero-argument callable returning it.
`findata-server init` (and every server start) discovers entry points, validates them,
and creates or validates the dataset's storage in the workspace. Core never imports your
module directly — discovery crosses the boundary through entry points only.

## The runtime protocols

Behavior splits into two protocols, both in `findata.plugins`.

**`ProviderRuntime`** — provider scope only (reference:
`findata_plugins.plugins.providers.tushare.provider.TushareProviderRuntime`):

| method | called for |
| --- | --- |
| `ready(workspace, mode)` | provider readiness shown by `provider status` and cron gating |
| `is_mock(workspace, mode)` | whether the provider runs on mock data |
| `probe(workspace, *, today)` | optional authenticated readiness check (`provider check`) |

Configuration, credentials, transports, and rate limiting stay the provider plugin's
internal business.

**`DatasetRuntime`** — dataset scope, carried by every `DatasetPlugin`:

| method | called for |
| --- | --- |
| `operation_worker(workspace, *, mode, today, now)` | returns the pickle-safe worker callable executed in a task subprocess |
| `normalize_operation(operation, operands, *, today)` | canonicalize/validate operands; raises `OperandError` |
| `plan_operation(workspace, operation, operands, *, today)` | `--dry-run` plan: strategy, dependencies, estimates |
| `dataset_description(workspace, *, provider_ready)` | `dataset describe/status` payload |
| `operation_description(operation)` | operand JSON schema + per-operand help |
| `resolve_dependency(target, requirement)` | map a dependency requirement to `(dataset, operands)` for fulfillment |
| `update_ready(workspace)` | whether settings and committed state allow parameterless `update` |

## The operation worker

`operation_worker` returns a callable executed once per task in a subprocess:

```python
from findata.plugins import OperationReporter, OperationRequest

def worker(request: OperationRequest, context: OperationReporter) -> dict:
    # request: execution_id, dataset, operation, operands,
    #          configuration_revision, settings
    ...
```

- `settings` is one immutable snapshot of the plugin's settings; changing a setting
  affects later submissions only.
- `context` is the reporter (`OperationReporter` in `findata.contracts`): `log`,
  `stage`, `progress`, `diagnostic`, `checkpoint` (cooperative cancellation point),
  `waiting(reason)` / `running()`, `fulfill(dataset, requirement)` for dependencies,
  and `begin_subtask` / `end_subtask`.
- Fetch and transform **before** acquiring any write resources, and call the provider
  only through the shared limiter (`findata.toolkit.rate_limit.FileRateLimiter`), passing
  `checkpoint`/`waiting` so waits stay cancelable.

Commit through the core writer — never through DuckDB:

```python
from findata.storage import Coverage, DataMutation, Workspace

publisher = Workspace(workspace_path).publisher("acme/finance/daily_bars")
publisher.commit(
    [DataMutation.replace_range(table, partition=symbol, start=start, end=end)],
    coverage=[Coverage(symbol, start, end)],
)
```

- `DataMutation.complete(table)` replaces the whole table;
  `replace_primary_keys(table)` upserts by primary key;
  `replace_range(table, partition=..., start=..., end=...)` replaces one
  partition's half-open interval.
- A coverage-tracked dataset (one with `time_field`) must commit matching `Coverage`
  entries; a non-coverage dataset must not.
- Provider credentials never appear in logs, results, or task messages.

Reads your plugin needs during planning or normalization go through the public
[DataLoader](dataloader.md) on the committed state of a declared dependency — never by
importing that dependency's plugin or opening its database.

## Minimal walkthrough

This section builds a working provider and dataset from scratch — about 90 lines of
Python across two packages. The full source is in `plugins/demo/` in the repository,
under the `findata-test` namespace.

### 1. Directory layout

```
my_first_plugin/
  provider/
    pyproject.toml
    src/
      mycompany/                          # PEP 420 namespace — no __init__.py
        plugins/providers/myprovider/
          __init__.py
          provider.py
  datasets/
    hello/
      pyproject.toml
      src/
        mycompany/
          plugins/datasets/hello/
            __init__.py
            operations.py
```

### 2. The provider plugin

**`provider/pyproject.toml`**:
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mycompany-provider-myprovider"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["findata"]

[project.entry-points."findata.providers"]
myprovider = "mycompany.plugins.providers.myprovider:provider_plugin"

[tool.hatch.build.targets.wheel]
only-include = ["src/mycompany/plugins/providers/myprovider"]
sources = ["src"]
```

**`provider/src/mycompany/plugins/providers/myprovider/provider.py`**:
```python
from findata.plugins import ProviderPlugin, ProviderRuntime
from findata.storage import Workspace
from datetime import date

class AlwaysReadyRuntime(ProviderRuntime):
    def ready(self, workspace: Workspace, mode: str) -> bool:
        return True
    def is_mock(self, workspace: Workspace, mode: str) -> bool:
        return True
    def probe(self, workspace: Workspace, *, today: date) -> None:
        pass

def provider_plugin() -> ProviderPlugin:
    return ProviderPlugin(
        provider_id="mycompany/myprovider",
        configuration_schema={"type": "object", "properties": {}},
        secret_fields=(),
        rate_limit=1000,
        period=60,
        runtime=AlwaysReadyRuntime(),
    )
```

**`provider/src/mycompany/plugins/providers/myprovider/__init__.py`**:
```python
from mycompany.plugins.providers.myprovider.provider import (
    AlwaysReadyRuntime, provider_plugin,
)
```

### 3. The dataset plugin

**`datasets/hello/pyproject.toml`**:
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mycompany-datasets-hello"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "findata",
    "mycompany-provider-myprovider==0.1.0",
    "pyarrow>=15",
]

[project.entry-points."findata.datasets"]
hello = "mycompany.plugins.datasets.hello:hello_plugin"

[tool.hatch.build.targets.wheel]
only-include = ["src/mycompany/plugins/datasets/hello"]
sources = ["src"]
```

**`datasets/hello/src/mycompany/plugins/datasets/hello/__init__.py`** — the spec and
factory:
```python
import pyarrow as pa
from findata.contracts import DatasetSpec

FIELDS = ("name", "greeting", "count")

HELLO_SPEC = DatasetSpec(
    name="mycompany/hello",
    api_name="hello",
    schema=pa.schema([
        pa.field("name", pa.string(), nullable=False),
        pa.field("greeting", pa.string(), nullable=False),
        pa.field("count", pa.int64(), nullable=False),
    ]),
    provider_fields=FIELDS,
    primary_key=("name",),
    partition_key="name",
)

def hello_plugin():
    from findata.plugins import DatasetPlugin
    from mycompany.plugins.datasets.hello.operations import HelloRuntime
    return DatasetPlugin(
        name=HELLO_SPEC.name,
        provider="myprovider",
        spec=HELLO_SPEC,
        runtime=HelloRuntime(),
        operations=("update", "complete", "refresh"),
    )
```

**`datasets/hello/src/mycompany/plugins/datasets/hello/operations.py`** — the runtime:
```python
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from findata.contracts import OperandError, OperationReporter, OperationRequest
from findata.plugins import DatasetRuntime
from findata.storage import Workspace
from mycompany.plugins.datasets.hello import HELLO_SPEC, FIELDS

@dataclass(frozen=True, slots=True)
class Worker:
    workspace: Path
    def __call__(self, request: OperationRequest, context: OperationReporter) -> dict:
        rows = int(request["operands"].get("rows", 5))
        if rows < 1:
            raise OperandError("rows must be positive")
        table = HELLO_SPEC.table_from_response(
            FIELDS,
            [[f"user_{i:04d}", f"Hello, user {i}!", i * 100] for i in range(rows)],
        )
        from findata.storage import Workspace as WS
        pub = WS(self.workspace).publisher(HELLO_SPEC.name)
        return {"publication_id": pub.publish(table), "rows": rows}

class HelloRuntime:
    def operation_worker(self, workspace, *, mode, today, now):
        return Worker(workspace=workspace)
    def normalize_operation(self, op, operands, *, today):
        if op not in ("update", "complete", "refresh"):
            raise OperandError(f"unknown operation {op!r}")
        v = dict(operands)
        if "rows" in v:
            v["rows"] = int(v["rows"])
        return v
    def plan_operation(self, ws, op, operands, *, today):
        return {"dry_run": True, "dataset": HELLO_SPEC.name, "operation": op,
                "operands": self.normalize_operation(op, operands, today=today),
                "estimated_provider_requests": 0, "dependencies": [],
                "side_effects": False}
    def dataset_description(self, ws, *, provider_ready):
        from findata.loader import DataLoader, DatasetNotReadyError
        try:
            r = DataLoader(ws.root).dataset(HELLO_SPEC.name)
            return {"name": HELLO_SPEC.name, "provider": "mycompany/myprovider",
                    "provider_ready": provider_ready, "state": "ready",
                    "publication_id": r.publication_id, "operations": [
                        self.operation_description(n) for n in ("update","complete","refresh")]}
        except DatasetNotReadyError:
            return {"name": HELLO_SPEC.name, "provider": "mycompany/myprovider",
                    "provider_ready": provider_ready, "state": "uninitialized",
                    "publication_id": None, "operations": [
                        self.operation_description(n) for n in ("update","complete","refresh")]}
    def operation_description(self, op):
        return {"name": op, "help": "", "required": [], "properties": {
            "rows": {"type": "integer", "minimum": 1, "default": 5}}}
    def resolve_dependency(self, target, requirement):
        raise ValueError(f"{HELLO_SPEC.name} has no dependencies")
    def update_ready(self, ws):
        return True
```

### 4. Try it

```bash
# Install both packages, then:
findata-server init ~/my-workspace
findata-server start ~/my-workspace

findata task run mycompany/hello complete --param rows=3 --wait

python -c "
from findata import DataLoader
print(DataLoader('~/my-workspace').dataset('mycompany/hello').query())
"
```

The dataset is discovered, registered, and queryable through the same CLI and API as
everything else — no changes to findata required.

## Reference implementation

The built-in Tushare plugins are the canonical example of every rule on this page and
ship as separate distributions: `findata_plugins.shared` (provider contract, runtime,
transport, limiter, shared operation engine) and one `findata_plugins.plugins.datasets.tushare_<dataset>` package
per dataset (spec, operations, settings, dependencies). The contributor-facing rules —
what a plugin must declare and what it must never do — are owned by `docs/DEV.md` and
`docs/design/` in the repository.
