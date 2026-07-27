# Providers and datasets

## Providers

```bash
findata provider ls                 # list providers and local readiness
findata provider status tushare     # validate configuration without a network call
findata provider check findata-plugins/tushare      # authenticated readiness probe through the rate limiter
```

Provider commands never display credentials. Configuration lives in
[Configuration](configuration.md).

## Datasets

```bash
findata dataset ls
findata dataset describe findata-plugins/tushare_daily_basic   # schema, capabilities, settings, storage
findata dataset operations findata-plugins/tushare_daily_basic
findata dataset operation findata-plugins/tushare_daily_basic complete   # operand schema and per-operand help
findata dataset status findata-plugins/tushare_daily_basic     # or: dataset status --all
```

`dataset describe` shows the static contract: provider readiness, capabilities,
dependencies, declared settings, storage, and status metadata. `dataset status` is the
runtime companion: initialization state, current publication, and a coverage summary
(number of covered keys and the overall resolved range).

### Built-in Tushare datasets

| dataset | contents |
| --- | --- |
| `findata-plugins/tushare_trade_cal` | exchange trade calendars (SSE, SZSE) |
| `findata-plugins/tushare_stock_basic` | listed-stock master data |
| `findata-plugins/tushare_index_basic` | index metadata |
| `findata-plugins/tushare_index_weight` | monthly index constituent weights |
| `findata-plugins/tushare_daily_basic` | daily valuation metrics (PE, PB, turnover, …) |

The canonical per-dataset contracts (schemas, publication windows, missing-data policy)
live in the repository under `docs/design/dataset/`; this table is an orientation aid.
To maintain datasets from your own provider, see
[Custom datasets and providers](custom-datasets.md).

## Maintaining data

```bash
findata dataset update   findata-plugins/tushare_daily_basic                      # parameterless, uses settings
findata dataset complete findata-plugins/tushare_daily_basic --symbols 600000.SH \
    --from 2026-01-01 --to 2026-07-01                             # explicit backfill
findata dataset refresh  findata-plugins/tushare_daily_basic --symbols 600000.SH \
    --from 2026-06-01 --to 2026-07-01                             # refetch inside coverage
findata dataset reset    findata-plugins/tushare_daily_basic --yes                # start over, keep settings
```

- `update` is parameterless: the plugin derives its work from the dataset's configured
  settings and committed state. A one-time `complete` or `refresh` never changes settings.
- `complete` backfills an explicit selection and time range; `refresh` refetches data
  strictly inside existing coverage.
- Operation commands are generated from each plugin's operand schema. Array operands use
  repeatable plural flags (`--symbols`, `--indexes`, `--exchanges`); half-open date ranges
  use `--timerange START:END` or `--from` plus `--to`. The generic
  `task run <dataset> <operation> --param key=value` form remains available for
  automation — see [Tasks and events](tasks-and-events.md).
- `reset` replaces one dataset with a new uninitialized database while preserving its
  settings and task history. Interactive human use asks for confirmation; structured or
  non-interactive use requires `--yes`. Reset is rejected while that dataset has queued or
  active work and never affects another dataset.

### Dry runs

`--dry-run` uses the same server-side operation planner as execution but submits no task,
performs no provider request, acquires no write gate, and changes nothing. It validates
normalized operands, reads current local state, reports dependencies, coverage expansion,
request strategy and estimated request/checkpoint counts when determinable, and marks
values unknown when required dependency data is absent. A later execution revalidates
mutable state, so a preview is informative rather than a reservation or guarantee.

### Operand conventions

Date ranges use `start:end` and are half-open: the start is included and the end is
excluded. Dates use `YYYY-MM-DD`; `today` is resolved once in the dataset timezone, so it
excludes the current date when used as the end. For example, `2026-06-01:2026-07-01`
covers all of June.

A scalar passed for an array operand is coerced to one element, so
`--param symbols=tushare:000300.SH` is equivalent to
`{"symbols":["tushare:000300.SH"]}` in structured operands. Repeated values are
deduplicated after validation. Empty or reversed ranges and empty required arrays are
rejected.

`tushare:000300.SH` preserves an exact provider index reference materialized in
`findata-plugins/tushare_index_basic`. The bare reference in `complete` means the historical constituent
union over that backfill range; `@latest` is a plugin-defined suffix for future updates.
Core findata configuration and CLI code treat both values as opaque strings.
