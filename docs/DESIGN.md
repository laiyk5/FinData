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
- **Task / subtask** — an operation executed in a process / an approximately one-minute end-to-end step inside it
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
- **Publication snapshot** — one immutable, self-consistent view of data and, when applicable, coverage
- **Maintenance universe** — the partition keys currently maintained by parameterless `update`
- **Coverage requirement** — a target-dataset-defined declaration of data needed by a dependent task

## Architectural boundaries

- The server owns orchestration, writes, configuration mutation, cron, events, and task state.
- Dataset plugins own provider fetch, transformation, validation, operation behavior, and staged writes.
- Toolkit components are optional plugin-side helpers; the server does not depend on them.
- Manifests are the read-side boundary. DataLoader never imports dataset maintenance plugins.
- DataLoader owns uniform query semantics, coverage checks, snapshot locking, Arrow results, reader adapters, and query-engine selection.
- The CLI is a thin HTTP client. It collects syntax and formats output; the server owns validation and policy.

## Workspace and server

One server controls one workspace. It acquires a non-blocking `flock` on a workspace lock file and records its PID for information. Kernel lock release handles normal exit and crashes.

v1 supports Linux and macOS on local POSIX filesystems providing `flock`, signals, permissions, directory flushing, and same-filesystem atomic rename. Network filesystems and Windows are outside the v1 contract.

The server exposes a versioned localhost HTTP API on `127.0.0.1`. `findata-server init` creates the workspace with `0700` permissions and a cryptographically random bearer token in a `0600` file. Every request, including streams, requires the token in the `Authorization` header. Tokens never appear in URLs or logs.

The OS user is the trust boundary. The token protects callers that can reach localhost but cannot read the workspace; a malicious process with the same user's filesystem privileges is out of scope.

## Providers

Providers declare a stable ID, configuration schema, secret fields, rate limits, retry behavior, and local readiness validation. They may expose a lightweight authenticated readiness probe. Probes use the same rate limiter as tasks and return only sanitized diagnostics.

Missing provider configuration does not prevent server startup. A task using an unready provider is rejected before queueing. Credentials are resolved from literal protected configuration or environment-variable references at use time and are inherited by task processes from the server environment.

The server owns one token bucket per provider. A task obtains a permit through the TaskRunner before every external API request, including retries. The bucket limits average frequency, applies a safety discount, starts empty, refills continuously, and permits only a bounded burst.

## Dataset registration and manifests

Dataset plugins are discovered through the `findata.datasets` entry-point group. Registration validates providers, dependencies, operation/operand schemas, storage strategy, manifest compatibility, and dependency cycles.

Every dataset exposes a parameterless `update` operation. Additional operations may include `complete` for explicit backfill and `refresh` for re-fetching strictly inside existing coverage. Datasets also declare plain read-side status queries; an uninitialized dataset reports that state without executing them.

A new dataset receives an `uninitialized` manifest with no publication snapshot. Its first successful publication—including a legitimately empty snapshot—changes it to `ready`. This distinguishes absent data from a resolved empty result.

A ready manifest contains at least:

- logical Arrow schema;
- primary, partition, secondary, and time keys where applicable;
- storage and reader strategy IDs and versions;
- publication snapshot identifier;
- initialization state and data-layout version;
- coverage location and missing-data policy where applicable.

Manifest schema version describes the envelope understood by DataLoader. Data-layout version separately describes the dataset's schema and physical organization. Registration never silently rewrites published manifests or migrates data.

Online data-layout migration is outside v1. An installed plugin or core reader incompatible with published data fails registration or reading with an explicit version error and leaves the workspace unchanged. A future migration facility must define backup, rollback, interruption, and reader-compatibility behavior as one complete feature before it is introduced.

Canonical per-plugin contracts live in [DATASETS.md](DATASETS.md).

## Update timing, coverage, and universe

For each target interval, publication timing classifies data as:

- before-window: not due and pruned;
- inside-window: fetchable, but an empty result remains unresolved;
- after-window: fetchable, and an allowed empty result becomes resolved-empty.

Under `strict`, an after-window empty is a failure. Under `accept-empty`, it resolves the interval. `best-effort` offers no continuous-coverage guarantee.

Time-accumulating datasets using `strict` or `accept-empty` keep one continuous coverage interval per partition key. New resolved intervals must abut or overlap existing coverage. Coverage means that every due observation in the dataset's declared observation domain within that civil-time interval is resolved; a non-due weekend, holiday, or non-observation month does not create a gap or imply that a row exists. Snapshot datasets have no coverage record.

A maintenance universe is either:

- **intrinsic** — the operation naturally fetches the complete dataset or has no configurable key set;
- **configured** — validated selectors are stored in workspace configuration and resolved for the latest due interval using only declared dependencies.

Keys leaving a configured universe remain queryable but stop extending. Newly selected keys begin at the latest due interval; historical data requires an explicit backfill. A configured dataset with an empty universe rejects `update`, and one-time operations never mutate the universe implicitly.

## Storage publication

Each dataset selects a supported declarative storage strategy suited to its update and query patterns. The matching plugin-side writer stages data; the corresponding core reader adapter interprets the manifest. A dataset never defines private public-query semantics.

Each successful subtask publishes one immutable snapshot. Data files and their matching coverage are one atomic unit: a reader sees either the complete preceding snapshot or the complete replacement, never a mixture. A reader that already selected a snapshot may continue using it until the read gate is released.

The writer stages and validates all replacement state on the dataset's filesystem before taking the exclusive write gate. Publication has one durable atomic commit point referenced by the manifest. Acknowledgement occurs only after the new snapshot and commit metadata are flushed. A crash before the commit point leaves the preceding snapshot published; a crash after it leaves the complete replacement published.

Whole-generation directories with hard links, content-addressed immutable files, or another equivalent representation may implement this contract. Namespace layout, deduplication, and garbage collection belong to implementation specifications and benchmarks. Recovery may remove abandoned staging state and unreachable snapshots; reachable snapshots are reclaimed only when the write gate proves that no reader is using them.

## TaskRunner

### Execution

Each execution runs one predefined operation in its own process and task sandbox. It reports through an authenticated duplex localhost TCP channel whose unique token is valid only for that process lifetime.

Long executions divide work into approximately one-minute subtasks. Each subtask performs download, transformation, validation, and publication for its slice. Completed publications are checkpoints; a later failure stops the execution without undoing earlier resolved work.

Execution records contain state, progress, logs, PID, and process start time. Separate handle records contain each submission's subscriber, execution, and public handle state. Active records persist; after completion, the newest 1,000 terminal handles per dataset and every execution they still reference are retained. Public task states and listing behavior are defined in [USER.md](USER.md#task-lifecycle).

The internal execution-state machine, message schema, framing, and persistence transactions belong in implementation specs before TaskRunner code is considered stable; they may not add or reinterpret public handle states.

### Cancellation and liveness

Blocking waits for rate permits, dependency completion, and the write gate are cancelable. Operations also check cancellation before each provider request, before publication, and between subtasks. Write-gate acquisition uses short timed attempts. Advisory `begin-write` and `end-write` messages improve graceful shutdown but never establish data safety.

Canceling a handle removes its subscription. A shared execution continues while another subscriber remains and is canceled only when none remain. When the last subscription is canceled, TaskRunner requests cooperative cancellation, waits five seconds, then kills a remaining process. No new subtask begins after cancellation is observed; an already committed checkpoint remains published and an in-flight atomic publication may finish. A parent owns its triggered-task handles and releases them recursively when canceled or failed.

The TaskRunner monitors negotiated per-subtask liveness timeouts. A timeout records an event but does not kill the process automatically; the dataset mutex stays held until the user cancels or the task exits.

On server shutdown, the server stops accepting work, cancels all handles, waits five seconds, then kills remaining task processes. Publication remains crash-safe at every instruction.

Cross-submission coalescing for operations with a stable declared work identity is part of v1. It never merges handle identity, ownership, terminal status, or cancellation; it shares only the execution and its logs/progress. A time- or configuration-dependent operation without such an identity always receives a new execution.

### Dependency fulfillment

A plugin may query only datasets in its declared acyclic dependency set. A fetchable dependency declares a JSON schema for coverage requirements and a pure resolver from a validated requirement to one operation and operands. The server never infers maintenance work from an arbitrary query failure.

A parent submits a target dataset and coverage requirement. TaskRunner validates the edge and requirement, runs the resolved triggered task, notifies the parent, and lets it retry the query once. If the same requirement remains unsatisfied, the parent fails with the remaining intervals.

A waiting parent releases its global concurrency slot but retains its dataset mutex. Registration rejects cycles. Runtime enforces `max_trigger_depth`, configurable with a default of 8; a request exceeding it is rejected and the parent is notified immediately. Depth greater than 3 also records a warning so unnecessarily deep dependency structures remain visible.

### Queues and concurrency

One task execution holds the dataset mutex. Additional executions wait in that dataset's queue. Each per-dataset queue holds at most five executions, and global task concurrency is configurable. Before applying the queue limit, an eligible operation's validated operands have dynamic values resolved, defaults applied, and arrays canonicalized; its declared work identity is serialized with the dataset and operation name as the coalescing key. An identical queued or running execution gains a separate handle without consuming queue capacity. An ineligible or nonmatching submission to a full queue is rejected and recorded in the event log.

## crond and events

Every suggested dataset schedule appears as a disabled default job; automatic maintenance is opt-in. Workspace configuration owns enabled state and schedule/timezone overrides. Schedules are evaluated in their declared IANA timezone using the daylight-saving behavior defined in [USER.md](USER.md#cron). Market jobs should use the exchange timezone. Enabling and firing validate the operation, provider readiness, and configured universe. A failed precondition skips submission and records an actionable event.

Enabled jobs missed during downtime are recorded but not run automatically. The user decides whether to submit the corresponding update.

The event log is persistent and append-only. It records task failures, liveness escalations, queue rejections, dependency-depth warnings, and skipped or missed cron jobs. Acknowledgement appends a reference record instead of mutating the original event.

## Restart and recovery

After acquiring the workspace lock, a new server identifies orphaned task processes by PID plus process start time and kills them before accepting work. Running, waiting, and queued executions from the previous server, and their active handles, become `failed` with reason `server_interrupted`. Queued work is never resumed automatically.

Recovery removes abandoned storage staging and unreachable snapshots. Published data needs no rollback because publication has one atomic commit point.

## DataLoader

DataLoader reads workspace manifests directly and works without the server. Multiple instances and processes may read concurrently.

v1 supports tabular datasets using a closed set of declarative core reader strategies. A plugin selects a strategy and writes its declared layout; it does not select or implement a public query engine. Each core reader adapter translates the uniform query request into an internal engine such as DuckDB or Arrow and returns Arrow record batches.

DataLoader centrally owns:

- initialization and version errors;
- partition-key and half-open time-range selection;
- projection, conjunctive filters, ordering, and limits;
- optional resolved-coverage enforcement;
- eager `pyarrow.Table` and streamed `pyarrow.RecordBatch` results;
- read-gate lifetime and publication-snapshot selection.

An eager query holds a shared gate through Arrow-table materialization. A batch iterator holds it until its context manager closes. A reader therefore stays on one publication snapshot for its complete query.

Novel layouts require a reusable plugin-side writer and compatible core reader adapter. A future third-party reader entry-point is deferred until a real unsupported storage family requires it; such an extension must still return Arrow batches and obey central query semantics.

The public API and examples live in [USER.md](USER.md#dataloader). Reader-strategy verification lives in [TEST.md](TEST.md#dataloader-contract-matrix).

## Configuration and security

Workspace configuration is the single source of truth for:

- display timezone;
- provider credentials and rate limits;
- dataset maintenance-universe selectors;
- cron enabled state and schedule overrides;
- HTTP port, task concurrency, and dependency-depth limits.

Secret values are stored only from stdin or as environment-variable references, are redacted from every read command, and never enter URLs or logs. Configuration mutations occur through the authenticated server API and are written atomically.

## v1 commitments and non-goals

The architecture contract is closed for v1 around atomic publication snapshots, subscriber-aware task coalescing, centralized DataLoader query semantics, and the dataset contracts in [DATASETS.md](DATASETS.md).

Online data-layout migration, third-party reader engines, network filesystems, Windows, automatic execution of missed cron jobs, and features unrelated to the primary story are v1 non-goals. Concrete workspace, manifest, plugin, task-message, event, and HTTP schemas belong in implementation specifications; they may choose mechanisms and encodings but may not change the behavior or boundaries defined here.
