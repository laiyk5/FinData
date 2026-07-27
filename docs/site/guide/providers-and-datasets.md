# Providers and datasets

## Providers

```bash
findata provider ls                 # list providers and local readiness
findata provider status findata-test/demo     # validate configuration without a network call
findata provider check findata-test/demo      # readiness probe through the rate limiter
```

Provider commands never display credentials. Configuration lives in
[Configuration](configuration.md).

## Datasets

```bash
findata dataset ls
findata dataset describe findata-test/demo_random   # schema, capabilities, settings, storage
findata dataset operations findata-test/demo_random
findata dataset operation findata-test/demo_random complete   # operand schema and per-operand help
findata dataset status findata-test/demo_random     # or: dataset status --all
```

`dataset describe` shows the static contract: provider readiness, capabilities,
dependencies, declared settings, storage, and status metadata. `dataset status` is the
runtime companion: initialization state, current publication, and a coverage summary
(number of covered keys and the overall resolved range).

### Available datasets

Run `findata dataset ls` after installing any plugin distribution to see what's
registered. The [Official plugins](../plugins/index.md) page lists available plugin families,
including the evaluation datasets that work without any API token.

To maintain datasets from your own provider, see
[Custom datasets and providers](custom-datasets/).

## Maintaining data

```bash
findata dataset update   findata-test/demo_random                          # parameterless, uses settings
findata dataset complete findata-test/demo_random --param tickers=AAPL \
    --param timerange=2026-07-01:2026-07-10              # explicit backfill
findata dataset refresh  findata-test/demo_random --param tickers=AAPL \
    --param timerange=2026-07-01:2026-07-10              # refetch inside coverage
findata dataset reset    findata-test/demo_random --yes                     # start over, keep settings
```

- `update` is parameterless: the plugin derives its work from the dataset's configured
  settings and committed state. A one-time `complete` or `refresh` never changes settings.
- `complete` backfills an explicit selection and time range; `refresh` refetches data
  strictly inside existing coverage.
- Operation commands are generated from each plugin's operand schema. Array operands use
  repeatable flags (`--param TICKER=VALUE`); half-open date ranges
  use `--param timerange=START:END`. The generic
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
excludes the current date when used as the end. For example, `2026-07-01:2026-07-10`
covers July 1 through July 9.

A scalar passed for an array operand is coerced to one element, so
`--param tickers=AAPL` is equivalent to
`{"tickers":["AAPL"]}` in structured operands. Repeated values are
deduplicated after validation. Empty or reversed ranges and empty required arrays are
rejected.
