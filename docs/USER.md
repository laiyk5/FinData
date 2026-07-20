# User documentation

This file is the canonical user-facing guide for findata. Architecture and invariants live in [DESIGN.md](DESIGN.md); individual dataset contracts live in [DATASETS.md](DATASETS.md).

## Quick start

The representative workflow below configures Tushare, backfills CSI 300 daily valuation data, and enables recurring updates.

```bash
# Terminal 1
export TUSHARE_API_TOKEN=...
findata-server init ~/market-data
findata-server start ~/market-data

# Terminal 2
cd ~/market-data
findata config set provider.tushare.token --env TUSHARE_API_TOKEN
findata provider check tushare

findata dataset universe set tushare_daily_basic CSI300@latest
findata task run tushare_daily_basic complete \
  --param symbols=CSI300 \
  --param timerange=2020-01-01:today \
  --follow

findata cron enable tushare_daily_basic
```

The half-open backfill ends before today and uses the historical union of CSI 300 constituents over its requested range. Recurring `update` operations catch subsequent due dates and require the constituent month containing each latest due trading date. Rerunning a failed backfill skips resolved coverage and resumes its remaining intervals.

## Workspace selection

`findata-server init <workspace>` creates the workspace marker and API credential. `findata-server start <workspace>` runs in the foreground; a service manager may supervise it.

The client resolves its workspace in this order:

1. global `--workspace <path>`;
2. `FINDATA_WORKSPACE`;
3. the nearest directory, starting at the current directory and walking through its parents, that contains a workspace marker.

If no workspace is found, the client exits with an error suggesting `findata-server init <path>`.

## CLI behavior

Operational commands support `--format human|json|jsonl`; `--json` is shorthand for `--format json`, and `jsonl` is used for streams. Stdout contains command results and stderr contains diagnostics.

Exit codes are:

- `0` — success;
- `1` — operational failure or a failed or canceled task when waiting;
- `2` — invalid CLI usage.

Task submission is asynchronous by default. `--wait` waits for the terminal result; `--follow` streams logs and implies `--wait`. Without waiting, success means that the task was accepted. A log follow prints existing logs, continues with new entries, and exits when the task reaches a terminal state.

Help, version, and shell-completion generation are not subject to structured output and do not require a workspace. Dynamic completion is best-effort and falls back to static command completion if a workspace or server is unavailable.

### Operand conventions

Dataset-specific operands are defined in [DATASETS.md](DATASETS.md). CLI date ranges use `start:end` and are half-open: the start is included and the end is excluded. Dates use `YYYY-MM-DD`; `today` is resolved once in the dataset timezone to the current date, so it excludes the current date when used as the end. For example, `2026-06-01:2026-07-01` covers all of June.

A scalar passed for an array operand is coerced to one element, so `--param symbols=CSI300` is equivalent to `{"symbols":["CSI300"]}` in structured operands. Repeated values are deduplicated after validation. Empty or reversed ranges and empty required arrays are rejected.

### Task lifecycle

Every task ID names the submitting handle, even when several handles share one coalesced execution. Public handle states are:

| state | meaning |
| --- | --- |
| `queued` | accepted and waiting for execution capacity or the dataset mutex |
| `running` | executing provider, transformation, validation, or publication work |
| `waiting` | paused for a rate permit, dependency, or write gate |
| `canceling` | the last subscription was canceled and its execution has not exited yet |
| `succeeded` | terminal; all required work completed |
| `failed` | terminal; work stopped with an error or was interrupted by server restart |
| `canceled` | terminal; this handle's subscription was canceled |

Canceling one coalesced handle makes that handle `canceled` immediately while another subscriber's handle continues. Canceling the final handle requests cooperative cancellation; after five seconds TaskRunner terminates a process that has not exited, and the handle then becomes `canceled` regardless of the process exit code. Completed publication checkpoints are not rolled back, and a publication already at its atomic commit may complete. Cancellation of an already terminal handle is a no-op reported as such.

## Command reference

### Tasks

- `task run <dataset> [operation] [--param key=value ... | --params JSON|@file|-] [--wait|--follow]`
  - `operation` defaults to `update`.
  - Repeated `--param` pairs, inline JSON, JSON from `@file`, and JSON from stdin (`-`) are mutually exclusive input forms.
  - The CLI collects input; the server applies schema coercion and defaults, validates the complete operands, and returns field-level errors.
- `task ls [--all] [--dataset NAME] [--status STATUS]` — list handles; the default view contains every nonterminal handle and the 50 most recent terminal handles. `--all` means all retained handles, up to the newest 1,000 terminal handles per dataset. `STATUS` is one of the public lifecycle states above.
- `task status <id>` — show status and progress. If work was coalesced, indicate whether other requesters remain.
- `task logs <id> [--follow]` — print logs; `-f` aliases `--follow`.
- `task cancel <id>` — cancel this request and report whether shared execution continued for another requester.

### Datasets

- `dataset ls`
- `dataset describe <name>` — show provider readiness, capabilities, dependencies, universe, timing, storage, and status metadata.
- `dataset operations <name>`
- `dataset operation <name> <operation>` — show operand schema, defaults, syntax, and examples.
- `dataset status <name>` / `dataset status --all`
- `dataset universe <name>` — show configured selectors.
- `dataset universe set <name> <selector>...` — replace selectors after validation; this does not fetch data.
- `dataset universe clear <name>` — clear selectors and prevent a configured-universe `update` from running.

### Providers

- `provider ls` — list providers and local readiness.
- `provider status <name>` — validate configuration and environment references without a network call.
- `provider check <name>` — run the provider's optional authenticated readiness probe through its rate limiter.

Provider commands never display credentials.

### Cron

- `cron ls` — show schedules, enabled state, schedule source, last run, and next run.
- `cron enable <dataset>` / `cron disable <dataset>`
- `cron set <dataset> --expression CRON --timezone IANA_ZONE`
- `cron reset <dataset>` — restore the plugin's suggested schedule without changing enabled state.

Automatic maintenance is opt-in. A job must have a ready provider and, when required, a nonempty maintenance universe.

Cron expressions are evaluated in the job's IANA timezone. A local wall time that does not exist because of a daylight-saving jump is skipped and records a warning event. A wall time that occurs twice runs once, at its first occurrence. Jobs missed while the server is down record a missed-job event after restart and are not submitted automatically.

### Events and system status

- `system status` — show server liveness, running tasks, and per-dataset queue lengths.
- `events ls [--unread] [--since DURATION] [--severity LEVEL]`, where `LEVEL` is `info`, `warning`, or `error`
- `events ack <id>` / `events ack --all`

Events include task failures, queue rejections, liveness escalations, and skipped or missed cron jobs.

### Configuration

- `config ls` / `config get [key]` — secret values are always redacted.
- `config set <key> <value>` — set a non-secret value.
- `config set <key> --stdin` — store a literal secret without placing it in shell history.
- `config set <key> --env <variable>` — store an environment-variable reference; recommended for provider tokens.
- `config unset <key>`

v1 intentionally has no command for revealing a stored secret.

### Completion

`completion <bash|zsh|fish>` generates a shell-completion script. The installed script obtains dynamic dataset, operation, and operand candidates when the resolved workspace and server are available.

## DataLoader

The DataLoader reads a workspace directly and does not require the server process.

```python
from pathlib import Path

from findata import DataLoader

loader = DataLoader(Path("~/market-data").expanduser())
dataset = loader.dataset("tushare_daily_basic")

table = dataset.query(
    keys=["000001.SZ", "600000.SH"],
    time_range=("2025-01-01", "2026-01-01"),
    columns=["ts_code", "trade_date", "pe", "pb"],
    filters=[("pe", ">", 0)],
    order_by=["trade_date", "ts_code"],
    require_coverage=True,
)
```

`query` returns a `pyarrow.Table`. `keys` addresses the dataset's declared partition key, and `time_range` is half-open `[start, end)` over its declared time field.

Filters are `(column, operator, value)` tuples combined with AND. Supported operators are `=`, `!=`, `<`, `<=`, `>`, `>=`, `in`, and `not in`. Ordering accepts column names or `(column, "asc"|"desc")` pairs.

For low-memory reads:

```python
with dataset.iter_batches(...) as batches:
    for batch in batches:
        ...
```

The iterator yields `pyarrow.RecordBatch` values and holds its read snapshot until the context manager closes.

An uninitialized dataset raises `DatasetNotReadyError`; a manifest or data-layout version unsupported by the installed core raises `IncompatibleDatasetError` without modifying the workspace. With `require_coverage=True`, a coverage-tracked dataset verifies explicit `keys` and `time_range` and raises `CoverageError(dataset, missing_intervals)` when due observations are unresolved. Non-observation dates such as a daily dataset's closed market days do not appear as gaps. Best-effort and non-coverage-tracked datasets do not support this option. `dataset.coverage(keys=None)` returns the coverage table when available.

## User-documentation principles

- User-visible behavior and syntax are documented here once and linked from other documents.
- Quick starts must be executed as written before every release.
- Examples should use normal user operations rather than internal shortcuts.
- User documentation may lag unreleased implementation work, but must match every released version.
- Secrets, internal storage paths, and unstable implementation details must not appear in examples.
