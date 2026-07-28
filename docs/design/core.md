# Architecture

## Architectural boundaries

- The server owns orchestration, writes, configuration mutation, cron, events, and task state.
- Dataset plugins own provider fetch, transformation, validation, settings schemas and parsing, and
  operation behavior; they submit validated Arrow data and declarative mutations to core storage.
- Toolkit components are optional plugin-side helpers; the server does not depend on them.
- The registered schema and per-dataset database metadata are the read-side boundary. DataLoader
  never imports dataset maintenance plugins.
- DataLoader owns uniform query semantics, coverage checks, database locking, SQL generation, and
  Arrow results.
- The CLI is a thin HTTP client. It collects syntax and formats output; the server owns validation
  and policy. Its design lives in [ux/cli.md](ux/cli.md).
- The WebUI is a second thin client over the same HTTP API, served by the server itself. It adds no
  endpoints, lifecycle states, or policy of its own. Its design lives in [ux/webui.md](ux/webui.md).
- Core modules load concrete providers and datasets only through registered contracts. They never
  import concrete plugin modules or the optional toolkit package.

## Workspace and server

One server controls one workspace. It acquires a non-blocking `flock` on a workspace lock file and records its PID for information. Kernel lock release handles normal exit and crashes.

v1 supports Linux and macOS on local POSIX filesystems providing `flock`, signals, permissions, directory flushing, and same-filesystem atomic rename. Network filesystems and Windows are outside the v1 contract.

The server exposes a versioned localhost HTTP API on `127.0.0.1`. `findata-server init` creates the workspace with `0700` permissions and a cryptographically random bearer token in a `0600` file. API clients authenticate with that token in the `Authorization` header. The WebUI may instead exchange a one-time, loopback-only login code issued by the local CLI for a short-lived `HttpOnly`, `SameSite=Strict` session cookie. The token never appears in URLs, browser storage, or logs.

`findata-server status <workspace>` reads the workspace descriptor and verifies the authenticated
server response. `stop` requests graceful shutdown only from that verified server; it never hunts
for or blindly signals a PID. `restart` performs that stop, then starts a foreground replacement.

The server also serves the WebUI's static assets for non-API paths under the contract defined in
[ux/webui.md](ux/webui.md).

Filesystem creation has explicit ownership. Workspace initialization alone creates the workspace
root and workspace-level files; dataset registration alone creates a dataset directory, its gate,
and its initial database. Dataset names are single safe path components. Lock acquisition, lookup,
schema discovery, coverage inspection, preview, export, and every DataLoader read must not create or
repair directories or files. Unknown, malformed, or incomplete dataset storage fails without a
filesystem side effect.

The OS user is the trust boundary. The token protects callers that can reach localhost but cannot read the workspace; a malicious process with the same user's filesystem privileges is out of scope.

## Providers

Providers declare a stable ID, configuration schema, secret fields, rate limits, retry behavior, and local readiness validation. They may expose a lightweight authenticated readiness probe. Probes use the same rate limiter as tasks and return only sanitized diagnostics.

Provider plugins are discovered through the `findata.providers` entry-point group. Registration
rejects duplicate IDs and validates their configuration schema, secret declarations, limiter
parameters, readiness contract, and optional probe before dataset registration. Dataset plugins
refer to providers only by registered ID; an unknown provider makes dataset registration fail.
The plugin decoupling goals and invariants live in [plugins.md](plugins.md).

Missing provider configuration does not prevent server startup. A task using an unready provider is rejected before queueing. Credentials are resolved from literal protected configuration or environment-variable references at use time and are inherited by task processes from the server environment.

The server owns one token bucket per provider. A task obtains a permit through the TaskRunner before every external API request, including retries. The bucket limits average frequency, applies a safety discount, starts empty, refills continuously, and permits only a bounded burst.

## Dataset registration and database metadata

Dataset plugins are discovered through the `findata.datasets` entry-point group after providers
have been registered. Registration validates provider references, dependencies, operation/operand
schemas, optional settings schemas, database compatibility, and dependency cycles.

Every dataset exposes a parameterless `update` operation. Additional operations may include `complete` for explicit backfill and `refresh` for re-fetching strictly inside existing coverage. Datasets also declare plain read-side status queries; an uninitialized dataset reports that state without executing them.

A plugin may declare typed settings under `dataset.<dataset-name>.*`. The generic configuration API
stores values atomically but delegates schema validation, normalization, readiness, and meaning to
the owning plugin. Each declared setting is classified required or optional: a required setting
gates update readiness, and only unconfigured required settings produce client warnings. Core
configuration and CLI code never parses selectors, symbols, constituent
references, or another dataset-specific value. An operation receives one immutable settings
snapshot for its execution; changing a setting affects later submissions only.

Setting normalization is deterministic, local, and side-effect-free. A plugin may inspect a
committed revision of a declared dependency for validation, but configuration never performs a
provider request, fulfills a dependency, submits a task, or imports the dependency's plugin.
Unavailable validation data produces an actionable error naming the dataset operation that can
create it. Removing a recognized setting is allowed and may make `update` unready; it never deletes
published data.

v1 stores each dataset in its own DuckDB database. A new database contains the logical `data`
table, `_findata_coverage`, and one `_findata_metadata` row in `uninitialized` state. Its first
successful commit—including a legitimately empty result—changes it to `ready`. This distinguishes
absent data from a resolved empty result.

The metadata row contains at least:

- logical Arrow schema;
- primary, partition, secondary, and time keys where applicable;
- DuckDB storage compatibility and core storage-adapter versions;
- a monotonically increasing revision and opaque publication ID naming the current commit;
- initialization state and data-layout version;
- missing-data policy where applicable.

The storage-adapter version describes the metadata and table envelope understood by DataLoader.
Data-layout version separately describes the dataset's logical schema. Registration never silently
rewrites a ready database or migrates data.

Online data-layout migration is outside v1. An installed plugin or core reader incompatible with
committed data fails registration or reading with an explicit version error and leaves the workspace
unchanged. A future migration facility must define backup, rollback, interruption, database-file
replacement, and reader-compatibility behavior as one complete feature before it is introduced.

Dataset initialization is local and credential-free: registration creates a valid empty database
through a same-filesystem temporary file and atomic replacement. Dataset reset is an explicit
destructive operation, not recovery or migration. It rejects queued or active work for that dataset,
holds its task mutex and exclusive gate, replaces the database with a newly initialized one, and
leaves provider configuration, dataset settings, task history, and other datasets unchanged. The
human CLI requires confirmation; structured or non-interactive use requires an explicit `--yes`.

Canonical per-plugin contracts live in [dataset/index.md](dataset/index.md).

## Update timing, coverage, and settings

For each target interval, publication timing classifies data as:

- before-window: not due and pruned;
- inside-window: fetchable, but an empty result remains unresolved;
- after-window: fetchable, and an allowed empty result becomes resolved-empty.

Under `strict`, an after-window empty is a failure. Under `accept-empty`, it resolves the interval. `best-effort` offers no continuous-coverage guarantee.

Time-accumulating datasets using `strict` or `accept-empty` keep one continuous coverage interval per partition key. New resolved intervals must abut or overlap existing coverage. Coverage means that every due observation in the dataset's declared observation domain within that civil-time interval is resolved; a non-due weekend, holiday, or non-observation month does not create a gap or imply that a row exists. Complete-replacement datasets have no coverage record.

Each plugin defines how parameterless `update` selects its work. A complete-replacement dataset may
need no settings; another dataset may require plugin-defined symbols, selectors, or other values.
For a selector whose natural meaning is "what we already have", the default is the symbol or key
set represented by that dataset's committed coverage; the coverage table, not a duplicated plugin
inventory, is authoritative. A plugin whose provider supports date-only full-market retrieval may
instead declare `all` as its default selector. The plugin reports update readiness from its settings
and committed state and returns an actionable validation error when required configuration or
tracked state is missing. Resolving an update selector to no targets is a successful no-op, not a
readiness failure or a reason to reject cron. One-time operations use their explicit operands and
never mutate plugin settings implicitly.

## Transactional dataset storage

Solution B is the sole v1 storage architecture for every registered tabular dataset: one DuckDB
file per dataset, owned by the core storage adapter. Dataset structure changes the logical schema,
keys, coverage contract, and allowed mutation scopes, but never selects another storage engine,
private physical layout, or query implementation. Dataset plugins never open the database, issue
SQL, or choose a query engine. They provide validated Arrow batches plus a declarative mutation
scope such as complete replacement or key/time-range replacement.

This decision trades immutable historical views and file-format transparency for bounded storage,
transactional in-place maintenance, uniform querying, and simpler initialization and reset. Retained
whole-generation snapshots were rejected because repeated small updates can consume space in
proportion to live dataset size times publication count, even when only a small delta changed. A
database adds a pinned runtime dependency, opaque internal files, explicit connection discipline,
and benchmark obligations; the following contracts accept and contain those costs. Historical
revisions, backup/export, and cross-host database service are separate future features rather than
implicit side effects of ordinary updates.

This is a v1 tabular boundary, not a claim that DuckDB must store every future kind of data. A
fundamentally non-tabular dataset or a second storage backend requires an architectural revision and
core adapter contract; a dataset plugin cannot introduce either as a private exception.

The adapter stages provider work and validation before opening the database for writing. Under the
exclusive dataset gate it opens one read/write connection, begins a transaction, applies data and
matching coverage mutations, increments the revision, assigns a new opaque publication ID, commits,
closes the connection, and releases the gate. Acknowledgement occurs only after durable commit.
Data, coverage, readiness state, and revision therefore have one atomic commit point. A crash before
commit leaves the preceding revision; a crash after commit leaves the complete new revision.

Primary keys remain logical dataset contracts. The adapter proves non-nullness, schema equality,
and uniqueness for the resulting affected scope, but v1 does not require a persistent DuckDB
primary-key index. Plugins cannot supply arbitrary predicates: registered key and time fields
determine the allowed mutation scope. SQL identifiers come only from registered schemas and values
are bound parameters.

DuckDB native read/write access is single-process. Findata therefore adds a stricter cross-process
rule: every DataLoader connection is opened read-only while holding the shared dataset gate, and a
read/write connection is opened only after acquiring the exclusive gate. Connections never outlive
their gate. Different datasets may proceed concurrently because their files and gates are separate.

Committed historical revisions are not retained and v1 offers no storage time travel or rollback.
Normal storage is the current database plus DuckDB's bounded WAL and an in-flight checkpoint batch,
so persistent growth follows live data rather than publication count. The adapter owns checkpointing,
orphan temporary-file cleanup, database-size diagnostics, and a benchmarked policy for reclaiming
reusable space. Full file-copy compaction is never hidden inside an ordinary dataset operation.

## TaskRunner

### Execution

Each execution runs one predefined operation in its own process and task sandbox. It reports through an authenticated duplex localhost TCP channel whose unique token is valid only for that process lifetime.

Long executions divide work into deterministic, independently committable work items and group those
items into checkpoint batches targeting approximately one minute, a bounded request count, and
bounded staged bytes. Correctness fixes the smallest work item: a complete-table replacement is one
indivisible item, while a registered key/time-range replacement may be independently committable.
Limits are observed only between items, so an indivisible oversized item forms one oversized batch.
Provider requests by themselves are never publication boundaries. Each batch performs download,
transformation, and validation outside the write gate, then commits its accumulated mutations in one
transaction. A later failure preserves completed batches and loses at most the in-flight batch.
Progress distinguishes processed work from durably checkpointed work.

Execution records contain state, progress, logs, PID, and process start time. Separate handle records contain each submission's subscriber, execution, and public handle state. Active records persist; after completion, the newest 1,000 terminal handles per dataset and every execution they still reference are retained. Public task states and listing behavior are defined in the [tasks guide](../site/guide/tasks-and-events.md).

Each dataset operation exposes a pure planning entry point. Planning consumes normalized operands,
captured configuration, capabilities, coverage, publication time, and locally committed dependency
data and returns a serializable plan. It performs no provider request or mutation. Execution invokes
the same planner and revalidates its inputs before consuming the plan. Dry-run is an HTTP/CLI
projection of this entry point, not a task lifecycle state and not a retained task. Retry creates a
new handle and execution from a retained handle's normalized request; explain is a read-only
projection of retained state, logs, diagnostics, and dependency error chains.

The internal execution-state machine, message schema, framing, and persistence transactions belong in implementation specs before TaskRunner code is considered stable; they may not add or reinterpret public handle states.

### Cancellation and liveness

Blocking waits for rate permits, dependency completion, and the write gate are cancelable. Operations also check cancellation before each provider request, before transaction commit, and between checkpoint batches. Write-gate acquisition uses short timed attempts. Advisory `begin-write` and `end-write` messages improve graceful shutdown but never establish data safety.

Canceling a handle removes its subscription. A shared execution continues while another subscriber remains and is canceled only when none remain. When the last subscription is canceled, TaskRunner requests cooperative cancellation, waits five seconds, then kills a remaining process. No new checkpoint batch begins after cancellation is observed; an already committed batch remains visible and an in-flight database transaction may finish. A parent owns its triggered-task handles and releases them recursively when canceled or failed.

The TaskRunner monitors negotiated per-work-item liveness timeouts. A timeout records an event but does not kill the process automatically; the dataset mutex stays held until the user cancels or the task exits.

On server shutdown, the server stops accepting work, cancels all handles, waits five seconds, then kills remaining task processes. Database transactions remain crash-safe at every instruction.

Cross-submission coalescing for operations with a stable declared work identity is part of v1. It never merges handle identity, ownership, terminal status, or cancellation; it shares only the execution and its logs/progress. A time- or configuration-dependent operation without such an identity always receives a new execution.

### Dependency fulfillment

A plugin may query only datasets in its declared acyclic dependency set. A fetchable dependency declares a JSON schema for coverage requirements and a pure resolver from a validated requirement to one operation and operands. The server never infers maintenance work from an arbitrary query failure.

A parent submits a target dataset and coverage requirement. TaskRunner validates the edge and requirement, runs the resolved triggered task, notifies the parent, and lets it retry the query once. If the same requirement remains unsatisfied, the parent fails with the remaining intervals.

A waiting parent releases its global concurrency slot but retains its dataset mutex. Registration rejects cycles. Runtime enforces `max_trigger_depth`, configurable with a default of 8; a request exceeding it is rejected and the parent is notified immediately. Depth greater than 3 also records a warning so unnecessarily deep dependency structures remain visible.

### Queues and concurrency

One task execution holds the dataset mutex. Additional executions wait in that dataset's queue. Each per-dataset queue holds at most five executions, and global task concurrency is configurable. Before applying the queue limit, an eligible operation's validated operands have dynamic values resolved, defaults applied, and arrays canonicalized; its declared work identity is serialized with the dataset and operation name as the coalescing key. An identical queued or running execution gains a separate handle without consuming queue capacity. An ineligible or nonmatching submission to a full queue is rejected and recorded in the event log.

## crond and events

Every suggested dataset schedule appears as a disabled default job; automatic maintenance is opt-in. Workspace configuration owns enabled state and schedule/timezone overrides. Schedules are evaluated in their declared IANA timezone using the daylight-saving behavior defined in the [scheduling guide](../site/guide/scheduling.md). Market jobs should use the exchange timezone. Enabling and firing validate the operation, provider readiness, and plugin-reported update readiness. A failed precondition skips submission and records an actionable event.

Enabled jobs missed during downtime are recorded but not run automatically. The user decides whether to submit the corresponding update.

The event log is persistent and append-only. It records task failures, liveness escalations, queue rejections, dependency-depth warnings, and skipped or missed cron jobs. Acknowledgement appends a reference record instead of mutating the original event.

## Restart and recovery

After acquiring the workspace lock, a new server identifies orphaned task processes by PID plus process start time and kills them before accepting work. Running, waiting, and queued executions from the previous server, and their active handles, become `failed` with reason `server_interrupted`. Queued work is never resumed automatically.

Opening each dataset database performs DuckDB recovery before work is accepted. Findata removes only
its own abandoned pre-transaction temporary inputs; it never deletes DuckDB WAL files directly.
Acquiring each exclusive gate during recovery logs a warning naming the dataset it waits on and
fails with an error naming that dataset after a bounded wait (60 seconds), so a long-running reader
cannot hang server startup without a trace.
Committed data needs no application-level rollback because each checkpoint batch has one database
transaction commit point.

## DataLoader

DataLoader reads per-dataset DuckDB files directly and works without the server. Multiple instances
and processes may read concurrently when no writer owns that dataset; the shared/exclusive gate
enforces this rule without requiring the server.

DataLoader is the only supported read protocol for external processes, and importing it must not
pull in CLI, server, or other maintenance-side modules. Opening a dataset database directly with
`duckdb.connect` — even read-only — bypasses the gate, corrupts read/write ordering, and fails the
next startup recovery with a conflicting-lock error; DataLoader treats that error as the detector
for the violation and never retries it. Readers that cannot use DataLoader (non-Python,
third-party, offline) receive an explicit snapshot copy: `Workspace.export_snapshot` holds the
exclusive gate, checkpoints, and atomically copies the database to a WAL-free single file.

v1 has one core tabular reader: DuckDB. Plugins declare logical schema and keys but do not select or
implement a public query engine. DataLoader translates its uniform request into parameterized SQL
and returns Arrow tables or record batches.

DataLoader centrally owns:

- initialization and version errors;
- partition-key and half-open time-range selection;
- projection, conjunctive filters, ordering, and limits;
- optional resolved-coverage enforcement;
- eager `pyarrow.Table` and streamed `pyarrow.RecordBatch` results;
- shared-gate, read-only connection, and database-transaction lifetime.

The core `data` CLI is a presentation/export adapter over DataLoader, not a server API or task
operation; its contract and coverage-presentation rules live in [ux/cli.md](ux/cli.md).

An eager query holds a shared gate and read-only connection through Arrow-table materialization. A
batch iterator holds both until its context manager closes. A reader therefore observes one committed
database state for its complete query.

Alternative database engines, plugin-defined SQL, private reader adapters, and third-party storage
entry points are outside v1. A future storage backend must preserve central query semantics, Arrow
results, transactional data/coverage commits, and the DataLoader concurrency contract.

The public API and examples live in the [reading-data guide](../site/guide/reading-data.md) and the [DataLoader guide](../site/guide/dataloader.md). Reader-strategy verification lives in [TEST.md](../TEST.md#duckdb-storage-and-dataloader-contract-matrix).

## Configuration and security

Workspace configuration is the single source of truth for:

- display timezone;
- provider credentials and rate limits;
- plugin-declared dataset settings;
- cron enabled state and schedule overrides;
- HTTP port, task concurrency, and dependency-depth limits.

Secret values are stored only from stdin or as environment-variable references, are redacted from every read command, and never enter URLs or logs. Configuration mutations occur through the authenticated server API and are written atomically.

## CLI and WebUI

The CLI design — thin-client boundary, human output principles, and structured output contracts —
lives in [ux/cli.md](ux/cli.md). The WebUI design lives in [ux/webui.md](ux/webui.md).
