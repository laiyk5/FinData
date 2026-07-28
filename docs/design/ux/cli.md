# CLI design

This file owns the CLI design: the thin-client boundary, human output principles, and structured
output contracts. Architectural rules shared by the whole system live in [core.md](../core.md);
detailed terminal behavior and examples are defined once in the [CLI reference](../../site/reference/cli.md),
and their verification belongs in [TEST.md](../../TEST.md#cli-presentation-matrix).

## Thin-client boundary

The server owns task state, progress meaning, warnings, and failure reasons. The CLI is a thin
HTTP client that collects syntax and renders those semantics; it does not infer task policy or
redefine lifecycle states — the server owns validation and policy. The server reports readiness
only after workspace validation, recovery, plugin registration, and socket binding succeed.

The core `data` CLI is a presentation/export adapter over DataLoader, not a server API or task
operation. Schema discovery reads registered dataset metadata from the committed database; preview
materializes only its bounded result; coverage delegates to DataLoader's coverage table; export
consumes `iter_batches` and writes CSV, Parquet, Arrow IPC, or JSONL. It never imports a dataset
plugin, opens a write connection, submits maintenance, or infers that missing coverage should be
downloaded. File exports use a sibling temporary file and atomic rename, while stdout exports keep
stdout data-only and send diagnostics to stderr.

`findata web open` is a convenience login command, not a second authentication model: it reads the
workspace token locally, asks the authenticated server for a one-time browser code, and opens the
local WebUI. `findata-server status`, `stop`, and `restart` are workspace lifecycle commands. They
verify the server descriptor with an authenticated request before reporting state or requesting
graceful shutdown; they never search for or blindly signal a process.

Coverage presentation preserves the stored half-open start and end dates. An optional requested
half-open interval is compared with the same central coverage record to expose completeness and
exact gaps without initiating maintenance. The human renderer treats dates as first-class table
cells and sends output taller than an interactive terminal through the user's pager. Paging is a
presentation concern only: structured, redirected, and export stdout remain deterministic streams.

## Human output principles

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
task and event retention rules. Retained task logs record lifecycle transitions and plugin
fetch, plan, and commit summaries with timestamps, so the history of a running or finished task
stays reviewable. A live human view keeps a bounded set visible and reports exact
additional counts without flooding the terminal; structured streaming preserves every logical
diagnostic occurrence. Detailed behavior is defined in the [CLI reference](../../site/reference/cli.md).

## Structured output

JSON and JSONL are stable, undecorated interfaces for scripts and tests. Structured output never
contains terminal control sequences, progress animation, readiness banners, or explanatory prose.
