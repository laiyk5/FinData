# Custom datasets and providers

findata is plugin-oriented: every dataset it maintains — including the built-in Tushare
datasets — is defined by a **dataset plugin** bound to a **provider plugin**, both
discovered through Python entry points. To manage your own dataset with findata, you
write a small Python package that declares the same contracts the built-in plugins use.
There is no configuration-file-only path; the extension API is code.

Once declared, your dataset gets the full operational surface for free: `dataset
ls/describe/status/operations`, `task run` with your operations, cron scheduling,
plugin-owned settings, Web UI forms, transactional commits with coverage tracking, and
safe concurrent reads through [DataLoader](dataloader.md).

!!! note "API stability"
    Everything on this page is the supported public API, typed and importable from one
    stop — `findata.plugins` (re-exporting the worker contracts from
    `findata.contracts`): `DatasetSpec`, `ProviderPlugin`, `DatasetPlugin`,
    `SettingSpec`, `ProviderRuntime`, `OperationRequest`, `OperationReporter`, and
    `OperationWorker`. Discovery rejects a provider whose runtime does not satisfy the
    `ProviderRuntime` protocol. The built-in Tushare implementation remains the
    reference example.

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
    name="mydaily",
    api_name="mydaily",                  # your provider's API name, used in errors
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

### `DatasetPlugin` — binding and operations

```python
from findata.plugins import DatasetPlugin, SettingSpec

plugin = DatasetPlugin(
    name="mydaily",                      # must equal spec.name
    provider="myprovider",               # must be a registered provider_id
    spec=spec,
    operations=("update", "complete"),   # "update" is mandatory and parameterless
    dependencies=(),                     # other dataset names; must stay acyclic
    settings={
        "update_symbols": SettingSpec(
            schema={"type": "array", "items": {"type": "string"}},
            normalize=normalize_symbols, # (value, workspace) -> normalized value
            help="Symbols selected for parameterless update.",
            required=True,               # gates update readiness; clients warn
        ),
    },
)
```

Validation at load time rejects duplicate dataset names, plugin/spec name mismatches,
unknown providers, a missing `update` operation, unknown dependencies, and dependency
cycles.

## Registration: entry points

Publish both contracts from your own package's `pyproject.toml`:

```toml
[project.entry-points."findata.providers"]
myprovider = "my_package.providers:myprovider_plugin"

[project.entry-points."findata.datasets"]
mydaily = "my_package.datasets:mydaily_plugin"
```

Each entry point is the contract object itself or a zero-argument callable returning it.
`findata-server init` (and every server start) discovers entry points, validates them,
and creates or validates the dataset's storage in the workspace. Core never imports your
module directly — discovery crosses the boundary through entry points only.

## The provider runtime protocol

Your `runtime` object implements the `ProviderRuntime` protocol from `findata.plugins`
(inherit from it, as `findata.providers.tushare.TushareProviderRuntime` does — it is the
reference implementation). The server calls these methods:

| method | called for |
| --- | --- |
| `operation_worker(workspace, *, mode, today, now)` | returns the pickle-safe worker callable executed in a task subprocess |
| `normalize_operation(dataset, operation, operands, *, today)` | canonicalize/validate operands; raises `OperandError` |
| `plan_operation(workspace, dataset, operation, operands, *, today)` | `--dry-run` plan: strategy, dependencies, estimates |
| `dataset_description(workspace, dataset, *, provider_ready)` | `dataset describe/status` payload |
| `operation_description(dataset, operation)` | operand JSON schema + per-operand help |
| `resolve_dependency(parent, target, requirement)` | map a dependency requirement to `(dataset, operands)` for fulfillment |
| `ready(workspace, mode)` | provider readiness shown by `provider status` and cron gating |
| `is_mock(workspace, mode)` | whether the provider runs on mock data |
| `probe(workspace, *, today)` | optional authenticated readiness check (`provider check`) |

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

publisher = Workspace(workspace_path).publisher("mydaily")
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

## Reference implementation

The built-in Tushare plugins are the canonical example of every rule on this page:
`findata.providers.tushare` (provider contract, runtime, transport, limiter) and
`findata.datasets.tushare` (dataset specs, operations, settings, dependencies). The
contributor-facing rules — what a plugin must declare and what it must never do — are
owned by `docs/DEV.md` and `docs/design/` in the repository.
