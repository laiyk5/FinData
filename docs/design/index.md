# findata design

## Documents

findata's primary documents have exclusive ownership. The canonical design documents live under
`docs/design/`:

- **[index.md](index.md)** (this file) — document map, purpose, glossary, and v1 commitments
- **[core.md](core.md)** — architecture, canonical terminology, boundaries, invariants, and major decisions
- **[plugins.md](plugins.md)** — plugin architecture goals, decoupling invariants, and lifecycle
- **[ux/cli.md](ux/cli.md)** — CLI design: thin-client boundary, human output, and structured output
- **[ux/webui.md](ux/webui.md)** — WebUI design: thin-client boundary, serving, pages, polling, and presentation policies
- **[toolkit/index.md](toolkit/index.md)** — canonical contracts for reusable toolkit components
- **[dataset/index.md](dataset/index.md)** — canonical contracts for dataset plugins

alongside:

- **[DEV.md](../DEV.md)** — contributor workflow and implementation guidance
- **[TEST.md](../TEST.md)** — testing methodology and required verification
- **[User documentation](../site/index.md)** — installation, workflows, CLI/DataLoader usage (published site; sources in `docs/site/`)

Every fact has one owning document. Other documents link to it rather than copying it. Development-time schemas and protocol specifications may live under `docs/specs/`; they may elaborate but never override this design.

## Purpose and primary story

findata serves two purposes:

1. maintain datasets through plugins that are easy to install and run;
2. provide one DataLoader interface so readers do not need to know physical storage layouts.

The primary v1 user is a quantitative researcher who backfills daily valuation data for an index universe, opts into recurring maintenance, and queries covered data safely from Python. The story is complete when provider readiness is diagnosable, dependency data is fulfilled deterministically, failed work is resumable, automatic maintenance is explicit, and DataLoader either returns covered data or identifies exact missing intervals. The executable workflow lives in the [quick start](../site/get-started/quickstart.md).

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
- **WebUI** — the browser-based thin client served by the server over the same HTTP API as the CLI
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

## v1 commitments and non-goals

The architecture contract is closed for v1 around atomic per-dataset database revisions,
checkpoint-batched transactions, subscriber-aware task coalescing, centralized DataLoader query
semantics, and the dataset contracts in [dataset/index.md](dataset/index.md).

Online data-layout migration, third-party reader engines, network filesystems, Windows, automatic
execution of missed cron jobs, and features unrelated to the primary story are v1 non-goals.
Concrete workspace, database-metadata, plugin, task-message, event, and HTTP schemas belong in
implementation specifications; they may choose mechanisms and encodings but may not change the
behavior or boundaries defined here.
