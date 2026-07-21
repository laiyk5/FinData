# Documents

findata uses six primary documents with exclusive ownership:

- **[DESIGN.md](DESIGN.md)** — architecture, canonical terminology, boundaries, invariants, and major decisions
- **[DEV.md](DEV.md)** — contributor workflow and implementation guidance
- **[TEST.md](TEST.md)** — testing methodology and required verification
- **[USER.md](USER.md)** — installation, workflows, CLI/DataLoader usage, and user-documentation principles
- **[DATASETS.md](DATASETS.md)** — canonical contracts for dataset plugins
- **[TOOLKITS.md](TOOLKITS.md)** — canonical contracts for reusable toolkit components

Every fact has one owning document. Other documents link to it rather than copying it. Development-time schemas and protocol specifications may live under `docs/specs/`; they may elaborate but never override this design.

# Design

## Purpose and primary story

findata serves two purposes:

1. maintain datasets through plugins that are easy to install and run;
2. provide one DataLoader interface so readers do not need to know physical storage layouts.

The primary v1 user is a quantitative researcher who backfills daily valuation data for an index universe, opts into recurring maintenance, and queries covered data safely from Python. The story is complete when provider readiness is diagnosable, dependency data is fulfilled deterministically, failed work is resumable, automatic maintenance is explicit, and DataLoader either returns covered data or identifies exact missing intervals. The executable workflow lives in [USER.md](USER.md#quick-start).

Features that do not materially support this story may be deferred from v1.

## Glossary

- **Workspace** — a dedicated local directory owned by exactly one server at a time
- **Provider** — a modular data source with shared configuration and rate limiting
- **Dataset** — a plugin defining how one logical dataset is maintained and described
- **Operation / operands** — a maintenance callable / its validated parameters
- **Task / checkpoint batch** — an operation executed in a process / a bounded unit committed in one database transaction
- **Task handle / subscription** — one submission's ID / its interest in a possibly shared execution
- **Triggered task** — a task submission made by another task to fulfill a declared dependency
- **TaskRunner** — the server component that queues, executes, and monitors tasks
- **Task mutex** — the per-dataset lock serializing task executions
- **Write gate** — the cross-process per-dataset read-write lock protecting publication and readers
- **crond** — the server component triggering enabled scheduled updates
- **Event log** — the persistent append-only record of events requiring attention
- **DataLoader** — the standalone uniform query interface over published workspace data
- **Toolkit** — opt-in reusable helpers imported by dataset plugins
- **Capabilities** — declared features or limits checked by reusable helpers
- **Publication window** — the provider-defined interval in which target data becomes available
- **Due** — data whose publication window has started
- **Resolved / unresolved** — due data with a final fetch result / data never fetched, failed, or transiently empty
- **Resolved-empty** — a due request whose empty result is recorded as final
- **Missing-data policy** — `strict`, `accept-empty`, or `best-effort`
- **Resolved coverage** — one continuous half-open `[start, end)` interval per partition key
- **Coverage record** — the materialized per-key resolved coverage
- **Dataset revision** — one committed, self-consistent database state containing data and matching coverage
- **Dataset setting** — a typed, plugin-owned configuration value used by that dataset's operations
- **Coverage requirement** — a target-dataset-defined declaration of data needed by a dependent task

## Architectural boundaries

- The server owns orchestration, writes, configuration mutation, cron, events, and task state.
- Dataset plugins own provider fetch, transformation, validation, settings schemas and parsing, and
  operation behavior; they submit validated Arrow data and declarative mutations to core storage.
- Toolkit components are optional plugin-side helpers; the server does not depend on them.
- The registered schema and per-dataset database metadata are the read-side boundary. DataLoader
  never imports dataset maintenance plugins.
- DataLoader owns uniform query semantics, coverage checks, database locking, SQL generation, and
  Arrow results.
- The CLI is a thin HTTP client. It collects syntax and formats output; the server owns validation and policy.
- Core modules load concrete providers and datasets only through registered contracts. They never
  import concrete plugin modules or the optional toolkit package.

## Workspace and server

One server controls one workspace. It acquires a non-blocking `flock` on a workspace lock file and records its PID for information. Kernel lock release handles normal exit and crashes.

v1 supports Linux and macOS on local POSIX filesystems providing `flock`, signals, permissions, directory flushing, and same-filesystem atomic rename. Network filesystems and Windows are outside the v1 contract.

The server exposes a versioned localhost HTTP API on `127.0.0.1`. `findata-server init` creates the workspace with `0700` permissions and a cryptographically random bearer token in a `0600` file. Every request, including streams, requires the token in the `Authorization` header. Tokens never appear in URLs or logs.

The OS user is the trust boundary. The token protects callers that can reach localhost but cannot read the workspace; a malicious process with the same user's filesystem privileges is out of scope.

## Providers

Providers declare a stable ID, configuration schema, secret fields, rate limits, retry behavior, and local readiness validation. They may expose a lightweight authenticated readiness probe. Probes use the same rate limiter as tasks and return only sanitized diagnostics.

Provider plugins are discovered through the `findata.providers` entry-point group. Registration
rejects duplicate IDs and validates their configuration schema, secret declarations, limiter
parameters, readiness contract, and optional probe before dataset registration. Dataset plugins
refer to providers only by registered ID; an unknown provider makes dataset registration fail.

Missing provider configuration does not prevent server startup. A task using an unready provider is rejected before queueing. Credentials are resolved from literal protected configuration or environment-variable references at use time and are inherited by task processes from the server environment.

The server owns one token bucket per provider. A task obtains a permit through the TaskRunner before every external API request, including retries. The bucket limits average frequency, applies a safety discount, starts empty, refills continuously, and permits only a bounded burst.

## Dataset registration and database metadata

Dataset plugins are discovered through the `findata.datasets` entry-point group after providers
have been registered. Registration validates provider references, dependencies, operation/operand
schemas, optional settings schemas, database compatibility, and dependency cycles.

Every dataset exposes a parameterless `update` operation. Additional operations may include `complete` for explicit backfill and `refresh` for re-fetching strictly inside existing coverage. Datasets also declare plain read-side status queries; an uninitialized dataset reports that state without executing them.

A plugin may declare typed settings under `dataset.<dataset-name>.*`. The generic configuration API
stores values atomically but delegates schema validation, normalization, readiness, and meaning to
the owning plugin. Core configuration and CLI code never parses selectors, symbols, constituent
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

Canonical per-plugin contracts live in [DATASETS.md](DATASETS.md).

## Update timing, coverage, and settings

For each target interval, publication timing classifies data as:

- before-window: not due and pruned;
- inside-window: fetchable, but an empty result remains unresolved;
- after-window: fetchable, and an allowed empty result becomes resolved-empty.

Under `strict`, an after-window empty is a failure. Under `accept-empty`, it resolves the interval. `best-effort` offers no continuous-coverage guarantee.

Time-accumulating datasets using `strict` or `accept-empty` keep one continuous coverage interval per partition key. New resolved intervals must abut or overlap existing coverage. Coverage means that every due observation in the dataset's declared observation domain within that civil-time interval is resolved; a non-due weekend, holiday, or non-observation month does not create a gap or imply that a row exists. Complete-replacement datasets have no coverage record.

Each plugin defines how parameterless `update` selects its work. A complete-replacement dataset may
need no settings; another dataset may require plugin-defined symbols, selectors, or other values.
The plugin reports update readiness from its settings and committed state and returns an actionable
validation error when required configuration or tracked state is missing. One-time operations use
their explicit operands and never mutate plugin settings implicitly.

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

Execution records contain state, progress, logs, PID, and process start time. Separate handle records contain each submission's subscriber, execution, and public handle state. Active records persist; after completion, the newest 1,000 terminal handles per dataset and every execution they still reference are retained. Public task states and listing behavior are defined in [USER.md](USER.md#task-lifecycle).

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

Every suggested dataset schedule appears as a disabled default job; automatic maintenance is opt-in. Workspace configuration owns enabled state and schedule/timezone overrides. Schedules are evaluated in their declared IANA timezone using the daylight-saving behavior defined in [USER.md](USER.md#cron). Market jobs should use the exchange timezone. Enabling and firing validate the operation, provider readiness, and plugin-reported update readiness. A failed precondition skips submission and records an actionable event.

Enabled jobs missed during downtime are recorded but not run automatically. The user decides whether to submit the corresponding update.

The event log is persistent and append-only. It records task failures, liveness escalations, queue rejections, dependency-depth warnings, and skipped or missed cron jobs. Acknowledgement appends a reference record instead of mutating the original event.

## Restart and recovery

After acquiring the workspace lock, a new server identifies orphaned task processes by PID plus process start time and kills them before accepting work. Running, waiting, and queued executions from the previous server, and their active handles, become `failed` with reason `server_interrupted`. Queued work is never resumed automatically.

Opening each dataset database performs DuckDB recovery before work is accepted. Findata removes only
its own abandoned pre-transaction temporary inputs; it never deletes DuckDB WAL files directly.
Committed data needs no application-level rollback because each checkpoint batch has one database
transaction commit point.

## DataLoader

DataLoader reads per-dataset DuckDB files directly and works without the server. Multiple instances
and processes may read concurrently when no writer owns that dataset; the shared/exclusive gate
enforces this rule without requiring the server.

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

An eager query holds a shared gate and read-only connection through Arrow-table materialization. A
batch iterator holds both until its context manager closes. A reader therefore observes one committed
database state for its complete query.

Alternative database engines, plugin-defined SQL, private reader adapters, and third-party storage
entry points are outside v1. A future storage backend must preserve central query semantics, Arrow
results, transactional data/coverage commits, and the DataLoader concurrency contract.

The public API and examples live in [USER.md](USER.md#dataloader). Reader-strategy verification lives in [TEST.md](TEST.md#dataloader-contract-matrix).

## Configuration and security

Workspace configuration is the single source of truth for:

- display timezone;
- provider credentials and rate limits;
- plugin-declared dataset settings;
- cron enabled state and schedule overrides;
- HTTP port, task concurrency, and dependency-depth limits.

Secret values are stored only from stdin or as environment-variable references, are redacted from every read command, and never enter URLs or logs. Configuration mutations occur through the authenticated server API and are written atomically.

## CLI principles

The server owns task state, progress meaning, warnings, and failure reasons. The CLI is a thin
HTTP client that renders those semantics; it does not infer task policy or redefine lifecycle
states. The server reports readiness only after workspace validation, recovery, plugin
registration, and socket binding succeed.

Human output is the default and favors concise tables, labeled detail views, explicit status
words, and actionable errors. Stable command results go to stdout; transient progress and
diagnostics go to stderr. Interactive decoration may improve presentation but must not carry
meaning by itself or alter execution, task persistence, or cancellation behavior.

Human task commands and event acknowledgement may use an unambiguous, server-resolved identifier
prefix. Exact identifiers take precedence, prefixes used for state-changing operations have a
minimum length, and every successful response returns the full resolved identifier. Prefix
matching does not apply to dataset, provider, publication, or execution identifiers.

Human rendering formats values according to declared field semantics: timestamps, durations,
counts, percentages, and generic measurements have distinct presentation rules. Scientific
notation is reserved for extreme generic measurements, not used as the default number format.
Presentation never changes the values or types emitted by JSON and JSONL.

Progress is transient, but warning and error diagnostics remain inspectable under the existing
task and event retention rules. A live human view keeps a bounded set visible and reports exact
additional counts without flooding the terminal; structured streaming preserves every logical
diagnostic occurrence. Detailed behavior is defined in [USER.md](USER.md#cli-behavior).

JSON and JSONL are stable, undecorated interfaces for scripts and tests. Structured output never
contains terminal control sequences, progress animation, readiness banners, or explanatory prose.
Detailed terminal behavior and examples are defined once in [USER.md](USER.md#cli-behavior), and
their verification belongs in [TEST.md](TEST.md#cli-presentation-matrix).

## v1 commitments and non-goals

The architecture contract is closed for v1 around atomic per-dataset database revisions,
checkpoint-batched transactions, subscriber-aware task coalescing, centralized DataLoader query
semantics, and the dataset contracts in [DATASETS.md](DATASETS.md).

Online data-layout migration, third-party reader engines, network filesystems, Windows, automatic
execution of missed cron jobs, and features unrelated to the primary story are v1 non-goals.
Concrete workspace, database-metadata, plugin, task-message, event, and HTTP schemas belong in
implementation specifications; they may choose mechanisms and encodings but may not change the
behavior or boundaries defined here.
