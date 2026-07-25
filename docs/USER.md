# User documentation

This file is the canonical user-facing guide for findata. Architecture and invariants live in [design/core.md](design/core.md); individual dataset contracts live in [design/dataset/index.md](design/dataset/index.md).

## Installation

findata v1 requires Python 3.11 or newer on Linux or macOS and a local POSIX filesystem. From a source checkout, install the package and its Arrow dependency with:

```bash
python -m pip install .
```

This installs the `findata` and `findata-server` commands. Use a virtual environment when isolation from other Python packages is desired.

## Quick start

The representative workflow below configures Tushare, backfills CSI 300 daily valuation data, and enables recurring updates.

```bash
# Terminal 1
findata-server init ~/market-data
findata-server start ~/market-data

# Terminal 2
cd ~/market-data
# Paste the token and press Enter; it is not placed in shell history.
findata config set provider.tushare.token --stdin
findata provider check tushare

findata task run tushare_index_basic complete \
  --param indexes=tushare:000300.SH \
  --wait
findata task run tushare_daily_basic complete \
  --param symbols=tushare:000300.SH \
  --param timerange=2026-06-29:2026-07-04 \
  --follow

findata config set dataset.tushare_daily_basic.update_symbols \
  --value-json '["tushare:000300.SH@latest"]'
findata cron enable tushare_daily_basic
```

The half-open sample backfill uses the historical union of CSI 300 constituents over its requested
range. Resolution starts with the latest weight snapshot effective at the range start and includes
later snapshots inside the range; a month without a new snapshot continues the preceding membership.
Rerunning a failed backfill skips resolved historical coverage, refreshes an intersecting current
month, and resumes its remaining intervals.
The separate `update_symbols` setting belongs to `tushare_daily_basic`; its plugin parses the
constituent selector and uses it only for later parameterless `update` operations. Recurring updates
therefore resolve the constituent month containing each latest due trading date.

Within the Tushare plugins, `tushare:000300.SH` preserves an exact provider index reference
materialized in `tushare_index_basic`. The bare reference in `complete` means the historical
constituent union over that backfill range; `@latest` is a plugin-defined suffix for future updates.
Core findata configuration and CLI code treat both values as opaque strings.

For another Tushare index, obtain its exact `ts_code`, materialize it with
`tushare_index_basic complete`, and use the same plugin-owned `tushare:<ts_code>` form. This tracks
only the requested reference. Metadata presence identifies the provider object but does not
guarantee index-weight permission or historical coverage.

## Workspace selection

`findata-server init <workspace>` creates the workspace marker and API credential. `findata-server start <workspace>` runs in the foreground; a service manager may supervise it.

The client resolves its workspace in this order:

1. global `--workspace <path>`;
2. `FINDATA_WORKSPACE`;
3. the nearest directory, starting at the current directory and walking through its parents, that contains a workspace marker.

If no workspace is found, the client exits with an error suggesting `findata-server init <path>`.

Each registered dataset owns one internal DuckDB file. These files are implementation state: users
query them through DataLoader and must not open them read/write, remove WAL files, or copy a live
database as a backup. Findata retains only the current committed dataset revision; routine updates
do not create historical storage copies. Dataset initialization is local and does not contact a
provider.

## Web UI

While `findata-server start` is running, the server also serves a browser UI at its listening
address (default `http://127.0.0.1:8765/`). Open it in a browser and paste the workspace token
when prompted; `findata-server token <workspace>` prints it (it is also the `<workspace>/token`
file). The token is held only in the browser session and is
sent as an `Authorization` header, never in a URL.

The WebUI is a thin client over the same HTTP API as the CLI and covers the same operational
surface: system status, datasets (describe, status, schema-driven operation forms with dry-run
and submit, confirmed reset), tasks (list, live status and logs, cancel, retry, explain),
providers (status and authenticated check), cron (enable, disable, schedule editing, reset),
events (filtering and acknowledgement), and configuration (list, set, unset — secrets can be
entered but are never displayed). It follows live work by polling; no work is submitted
implicitly, and every mutation corresponds to the CLI command documented in this guide.

Reading committed data (`data schema`, `data preview`, `data coverage`, `data export`) remains a
CLI and DataLoader workflow and is intentionally not part of the WebUI.

## CLI behavior

Operational commands support `--format human|json|jsonl`; `jsonl` is used for streams. Stdout contains command results and stderr contains diagnostics.

Human output is the default. Collection commands use compact tables, detail commands use labeled
fields, and an empty result says what was not found rather than printing an empty JSON value. Human
errors state what failed, include relevant context, and suggest a recovery or inspection command
when one exists. Tracebacks are reserved for an explicit debug mode.

`--color auto|always|never` controls human styling and defaults to `auto`. Automatic styling is used
only when the destination stream is an interactive terminal and is disabled when `NO_COLOR` is set
or `TERM=dumb`. Status always has a textual or symbolic indicator, so color is never its only
meaning. Structured formats never contain color or other terminal control sequences, including
when `--color always` is also supplied.

JSON emits exactly one JSON document. JSONL emits one complete object per event or record with a
stable `type` field. Neither format includes spinners, readiness banners, explanatory prose, or
other human decoration. On failure, the selected structured format is also used for the diagnostic
written to stderr, and the documented nonzero exit code remains authoritative.

Because following is a stream, `--follow --format json` is rejected with exit code `2`; use
`--format jsonl` instead. A non-following `--wait --format json` emits only its terminal task object.

Exit codes are:

- `0` — success;
- `1` — operational failure or a failed or canceled task when waiting;
- `2` — invalid CLI usage;
- `130` — the user interrupted a wait or follow; the accepted server task remains running.

Task submission is asynchronous by default. The CLI reports acceptance and the task ID as soon as
the server accepts it. `--wait` waits for the terminal result; `--follow` streams logs and implies
`--wait`. Without waiting, success means that the task was accepted. A log follow prints existing
logs, continues with new entries, and exits when the task reaches a terminal state.

While waiting in human mode, the CLI renders the server's semantic stage and progress on stderr.
For work lasting longer than approximately 250 milliseconds, an interactive terminal may use a
spinner or replace a progress line in place. Redirected diagnostics use ordinary newline-delimited
updates. A terminal summary removes any transient animation and reports status, elapsed time, task
ID, and available result identifiers. Waiting states name their reported reason, such as a rate
permit, dependency, or write gate; the CLI does not invent progress the server did not report.
When available, the live region also reports provider requests, fetched rows, committed checkpoints,
elapsed time, and a conservative ETA. Unknown values are omitted rather than guessed. `--no-progress`
disables the live region, `--quiet` suppresses nonterminal human output, and `--verbose` includes
dependency and request-planning detail. `--quiet` and `--verbose` cannot be combined. Structured
output is unaffected by verbosity flags.

Pressing Ctrl-C while waiting or following detaches the client and leaves the accepted server task
running. Use `findata task cancel <id>` when cancellation is intended. A temporary connection loss
is reported clearly, and an error includes the task-status or log command needed to inspect work
that may still be running.

When `findata-server start` runs in the foreground, it prints a concise readiness report containing
the version, resolved workspace, listening address, and a credential-free provider summary. It
prints readiness only after startup recovery and initialization have succeeded. Redirected or
service-managed output uses one plain log record rather than an interactive banner, and server
output never reveals API credentials or provider secrets.

### Identifier prefixes

Commands that address a task handle (`task status`, `task logs`, and `task cancel`) and `events ack`
accept either the full identifier or a lowercase hexadecimal prefix of at least eight characters.
An exact identifier always wins. A prefix must identify exactly one retained resource; no match is
reported as not found, and multiple matches are reported as ambiguous with no action performed.
Success output always includes the full resolved identifier. Task commands resolve handle
identifiers only, never the internal execution identifier shared by coalesced tasks. Dataset,
provider, publication, and execution identifiers must be supplied in full.

### Human value formatting

Human output uses the declared meaning of a field rather than guessing from its Python type or
name:

- timestamps use ISO 8601 in the configured display timezone and include the UTC offset;
- elapsed durations use an adaptive unit such as `240 ms`, `3.2 s`, or `2 min 5 s`;
- integer counts use ASCII thousands grouping, such as `12,500`;
- percentages and domain measurements use their declared precision and unit; and
- generic finite decimals use a concise fixed representation, switching to scientific notation
  only when their absolute value is at least `1e9` or is nonzero and below `1e-4`.

Identifiers, symbols, calendar dates, monetary or other exact decimals, and schema-declared text
are not passed through generic numeric formatting. JSON and JSONL retain the original values and
types; display timezone, grouping, units, and precision are human-presentation concerns only.

### Live diagnostics

While waiting or following in human mode, progress remains transient. The first ten distinct
warning or error diagnostics remain visible as ordinary lines. Exact repeats may be combined with
an occurrence count. Further distinct diagnostics are suppressed from the live human view: an
interactive terminal shows a replaceable line with exact additional warning and error counts,
while redirected stderr prints one suppression notice followed by a final count summary. A
terminal failure is always printed even when the visible limit has already been reached.

The final summary reports total warning and error occurrences and names `findata task logs <id>`
or `findata events ls` when retained details are available. Each diagnostic has a severity, stable
code, message, optional context, and occurrence count. JSONL represents every logical occurrence;
an aggregated record is lossless only when its count preserves the total. JSON and JSONL do not
apply the human visibility limit.

Help, version, and shell-completion generation are not subject to structured output and do not require a workspace. Dynamic completion is best-effort and falls back to static command completion if a workspace or server is unavailable.
Click provides the command hierarchy, validation, and help pages; invoking help or version from an
embedded Python caller returns normally rather than terminating the host process.

### Operand conventions

Dataset-specific operands are defined in [design/dataset/index.md](design/dataset/index.md). `findata dataset operation
<dataset> <operation>` and `findata dataset describe <dataset>` show per-operand help alongside
the operand schema. CLI date ranges use `start:end` and are half-open: the start is included and the end is excluded. Dates use `YYYY-MM-DD`; `today` is resolved once in the dataset timezone to the current date, so it excludes the current date when used as the end. For example, `2026-06-01:2026-07-01` covers all of June.

A scalar passed for an array operand is coerced to one element, so
`--param symbols=tushare:000300.SH` is equivalent to
`{"symbols":["tushare:000300.SH"]}` in structured operands. Repeated values are deduplicated after
validation. Empty or reversed ranges and empty required arrays are rejected.

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

Canceling one coalesced handle makes that handle `canceled` immediately while another subscriber's handle continues. Canceling the final handle requests cooperative cancellation; after five seconds TaskRunner terminates a process that has not exited, and the handle then becomes `canceled` regardless of the process exit code. Completed transaction checkpoints are not rolled back, and a database transaction already committing may complete. Cancellation of an already terminal handle is a no-op reported as such.

## Command reference

### Tasks

- `task run <dataset> [operation] [--param key=value ... | --params JSON|@file|-] [--wait|--follow]`
  - `operation` defaults to `update`.
  - Repeated `--param` pairs, inline JSON, JSON from `@file`, and JSON from stdin (`-`) are mutually exclusive input forms.
  - The CLI collects input; the server applies schema coercion and defaults, validates the complete operands, and returns field-level errors.
- `task ls [--all] [--dataset NAME] [--status STATUS]` — list handles; the default view contains every nonterminal handle and the 50 most recent terminal handles. `--all` means all retained handles, up to the newest 1,000 terminal handles per dataset. `STATUS` is one of the public lifecycle states above.
- `task status <id>` — show status and progress. If work was coalesced, indicate whether other requesters remain.
- `task logs <id> [--follow]` — print the retained task log; `-f` aliases `--follow`.
  - Human output renders one line per record as `HH:MM:SS message` in the display timezone.
  - The retained log records what a task actually did: lifecycle transitions (`waiting: <reason>`, `running`), dependency requests and their fulfillment or failure, the terminal state (`succeeded`, `failed: <error>`, `canceled`), and dataset plugin activity — a plan summary per operation, one fetch line per provider request with parameters and returned row counts, checkpoint commits with row and coverage totals, and a final completion summary.
  - JSONL emits one typed record per line: `{"type":"log","time":<epoch seconds>,"message":...}` for log lines and `{"type":"task.diagnostic",...}` for diagnostics. Progress and stage updates are transient and not part of the retained log.
- `task cancel <id>` — cancel this request and report whether shared execution continued for another requester.
- `task watch <id>` — follow a retained task's progress and logs without submitting work.
- `task retry <id> [--wait|--follow]` — submit a new handle using the retained task's normalized
  dataset, operation, and operands. Configuration is snapshotted again; the old record is unchanged.
- `task explain <id>` — show the current or terminal reason, dependency-failure chain, diagnostics,
  and concrete inspection or retry commands without changing task state.

### Datasets

- `dataset ls`
- `dataset describe <name>` — show provider readiness, capabilities, dependencies, declared
  settings, storage, and status metadata.
- `dataset operations <name>`
- `dataset operation <name> <operation>` — show the operand schema, required operands, and
  per-operand help; `dataset describe <name>` shows the same operation-level and operand help
  alongside the static contract.
- `dataset status <name>` / `dataset status --all` — show committed maintenance state:
  provider and update readiness, initialization state, current publication, and a coverage
  summary (number of covered keys and the overall resolved range). This is the runtime
  companion to `dataset describe`, which shows the static contract.
- `dataset reset <name> [--yes]` — replace one dataset with a new uninitialized database while
  preserving its settings and task history. Human interactive mode requires confirmation;
  structured or non-interactive use requires `--yes`. Reset is rejected while that dataset has
  queued or active work and never affects another dataset.
- `dataset update|complete|refresh <name> [operation operands] [--wait|--follow] [--dry-run]` —
  ergonomic operation commands generated from the plugin's operation schema. Array operands use
  repeatable plural flags such as `--symbols`; half-open date ranges use either `--timerange` or
  `--from` plus `--to`. The generic `task run` form remains available for automation.

`--dry-run` uses the same server-side operation planner as execution but submits no task, performs
no provider request, acquires no write gate, and changes no data, coverage, configuration, events,
or task history. It validates normalized operands, reads current local state, reports dependencies,
coverage expansion, request strategy and estimated request/checkpoint counts when determinable, and
marks values unknown when required dependency data is absent. A later execution revalidates mutable
state, so a preview is informative rather than a reservation or guarantee.

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

Automatic maintenance is opt-in. A job must have a ready provider and its dataset plugin must report
that its settings and committed state are sufficient for `update`.

Cron expressions are evaluated in the job's IANA timezone. A local wall time that does not exist because of a daylight-saving jump is skipped and records a warning event. A wall time that occurs twice runs once, at its first occurrence. Jobs missed while the server is down record a missed-job event after restart and are not submitted automatically.

### Events and system status

- `system status` — show server liveness, running tasks, and per-dataset queue lengths.
- `events ls [--unread] [--since DURATION] [--severity LEVEL]`, where `LEVEL` is `info`, `warning`, or `error`
- `events ack <id>` / `events ack --all`

Events include task failures, queue rejections, liveness escalations, and skipped or missed cron jobs.

### Configuration

- `config ls` / `config get [key]` — secret values are always redacted.
- `config set <key> <value>` — set a non-secret value.
- `config set <key> --value-json JSON|@file|-` — set a typed JSON value.
- `config set <key> --stdin` — store a literal secret without placing it in shell history.
- `config set <key> --env <variable>` — store an environment-variable reference; recommended for provider tokens.
- `config unset <key>`

v1 intentionally has no command for revealing a stored secret.

Keys under `dataset.<dataset-name>.*` are owned by that dataset plugin. Core findata transports and
stores the value but does not interpret it. The plugin declares its setting names and schemas,
normalizes values, reports update readiness, and provides setting-specific help through
`dataset describe`. Unknown dataset settings and invalid values are rejected before configuration
is changed; the error for a registered dataset lists its declared setting keys.

Declared keys can be discovered without knowing them in advance: shell completion for
`config set|get|unset` suggests declared keys alongside already-set ones, `dataset describe
<dataset>` shows that dataset's declared settings with help and whether each is configured, and
the HTTP API exposes every declared core, provider, and dataset key at `GET /v1/config/keys`
(with `key`, `help`, `schema`, `configured`, and `secret` per item).

### Completion

`completion <bash|zsh|fish>` generates a shell-completion script. Generating it does not activate
completion; the current shell must source it. Add the matching line to the shell startup file, or
run it once to enable completion in the current session:

```bash
# zsh: add to ~/.zshrc
eval "$(findata completion zsh)"

# bash: add to ~/.bashrc
eval "$(findata completion bash)"

# fish: add to ~/.config/fish/config.fish
findata completion fish | source
```

After reloading the shell, `findata <Tab>` completes command families and
`findata data coverage <Tab>` completes registered local datasets. The completion script obtains dynamic dataset, operation, and operand candidates when the resolved workspace and server are available.
Completion uses a credentialed hidden CLI query rather than putting a workspace token in the shell
script. It completes dataset/provider names, operations, configuration keys, retained task IDs, and
schema-declared operand flags, and falls back to static commands when no server is available. The
protocol does not depend on a shell preserving a trailing empty argument.

## Discovering, previewing, and exporting committed data

After maintaining a dataset, a user can inspect and export its committed revision without starting
the server and without learning the internal DuckDB layout:

```bash
findata data schema tushare_daily_basic

findata data preview tushare_daily_basic \
  --keys 600000.SH \
  --from 2026-01-01 --to 2026-07-01 \
  --columns ts_code,trade_date,close,pe,pb

findata data coverage tushare_daily_basic \
  --keys 600000.SH \
  --from 2026-01-01 --to 2026-07-01

findata data export tushare_daily_basic \
  --keys 600000.SH \
  --from 2026-01-01 --to 2026-07-01 \
  --columns ts_code,trade_date,close,pe,pb \
  --output-format parquet \
  --output daily-basic.parquet
```

This is a read-only workflow. It creates no task, performs no provider request, changes no dataset
or configuration, and never opens an internal database for writing. A concurrent writer may delay
the read briefly; the result always observes one committed publication.

- `data schema <dataset>` reports the Arrow fields, nullability, primary key, partition key, time
  field, publication ID, and whether coverage is supported.
- `data preview <dataset>` prints at most 20 rows by default; `--limit` changes the bound. It accepts
  repeatable `--keys`, `--from/--to`, and comma-separated or repeatable `--columns`.
- `data coverage <dataset> [--keys KEY ...]` reports committed half-open coverage intervals. With
  `--from/--to`, it compares that requested interval with each selected key and reports whether it
  is complete, the committed interval, and every missing half-open interval. Both boundaries must
  be supplied together.
- `data export <dataset> --output PATH|- --output-format csv|parquet|arrow|jsonl` streams committed
  batches instead of materializing the full result. `--batch-size` controls the batch bound.

When both keys and a time range are supplied, preview and export require complete coverage by
default. `--allow-partial` explicitly opts into a partial result and reports that policy in the
export summary. `--require-coverage` may be used for clarity but cannot be combined with
`--allow-partial`. Coverage validation errors name every missing interval and suggest the matching
`dataset complete ...` command; they never trigger that command automatically.

CSV and JSONL may be written to `--output -`. Parquet and Arrow IPC require a file or a binary stdout
stream. A filesystem export refuses to replace an existing file unless `--force` is supplied and
publishes the completed export with an atomic rename, so a failed export does not leave a file that
looks complete. Diagnostics and a file-export summary go to stderr; stdout contains only exported
records when `--output -` is used.

Long human-readable results shown in an interactive terminal are automatically sent through the
pager selected by `$PAGER` (normally `less`) instead of flooding the terminal. Paging is never used
for redirected output, `--format json`, `--format jsonl`, or export data written to stdout. Set `PAGER=cat` when
interactive paging is not wanted.

## DataLoader

The DataLoader reads each dataset's DuckDB database directly and does not require the server
process. It coordinates with writers through the dataset gate, so a query may briefly wait for a
transaction on the same dataset; different datasets remain independent.

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

The iterator yields `pyarrow.RecordBatch` values and holds its shared gate, read-only connection,
and committed database view until the context manager closes.

An uninitialized dataset raises `DatasetNotReadyError`; an unsupported storage-adapter,
DuckDB-storage, or data-layout version raises `IncompatibleDatasetError` without modifying the
workspace. With `require_coverage=True`, a coverage-tracked dataset verifies explicit `keys` and
`time_range` and raises `CoverageError(dataset, missing_intervals)` when due observations are
unresolved. Non-observation dates such as a daily dataset's closed market days do not appear as gaps.
Best-effort and non-coverage-tracked datasets do not support this option.
`dataset.coverage(keys=None)` returns the coverage table when available.

## User-documentation principles

- User-visible behavior and syntax are documented here once and linked from other documents.
- Quick starts must be executed as written before every release.
- Examples should use normal user operations rather than internal shortcuts.
- User documentation may lag unreleased implementation work, but must match every released version.
- Secrets, internal storage paths, and unstable implementation details must not appear in examples.
