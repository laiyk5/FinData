# Development guide

This file owns contributor workflow and implementation guidance. Architecture belongs to [DESIGN.md](DESIGN.md), dataset contracts to [DATASETS.md](DATASETS.md), toolkit contracts to [TOOLKITS.md](TOOLKITS.md), and verification policy to [TEST.md](TEST.md).

## Development principles

- Keep the user story working through small vertical slices.
- Put policy in the component that owns it; do not duplicate schema coercion, coverage rules, or query semantics across server, CLI, and plugins.
- Prefer declarative contracts and schemas at process and package boundaries.
- Promote a dataset-private helper into the toolkit only when a second dataset needs it.
- Preserve user data and unrelated workspace changes while developing.
- When a failure escapes existing tests, add a regression case.

## Adding a provider

Define and document:

1. stable provider ID;
2. configuration schema and secret fields;
3. rate-limit parameters;
4. retry and sanitized error behavior;
5. local readiness validation;
6. an optional lightweight authenticated readiness probe;
7. mock behavior for success, empties, rate limiting, and failures.

Publish the provider contract through the `findata.providers` entry-point group. Core discovery
loads and validates that contract before loading dataset entry points; it must not import the
provider's concrete module directly. Registration tests cover duplicate IDs, malformed schemas,
invalid limiter parameters, and datasets referring to an unregistered provider.

If a provider exposes instrument-reference metadata, preserve its identifiers as opaque values,
record how explicitly requested references are materialized and refreshed, and keep
endpoint-capability checks separate from metadata presence. Do not implement cross-provider
identity by transforming codes or matching names. Reference lookup belongs to provider-specific
dataset plugins, not to core findata; do not enumerate a provider's markets unless a dataset
contract explicitly requires it.

Provider code never logs or returns credentials. Every external request, including readiness probes, goes through the shared provider limiter.

## Adding a dataset plugin

Before implementation, add its canonical entry to [DATASETS.md](DATASETS.md). Then define:

1. provider, logical Arrow schema, and keys;
2. capabilities and any typed plugin settings, including defaults, normalization, help, and update-readiness rules;
3. publication window, schedule, and missing-data policy;
4. dependencies and optional fulfillment requirement schema;
5. operation entry points and operand JSON schemas;
6. declarative database mutation scope and DataLoader metadata;
7. coverage and status behavior;
8. mock generator and dataset-specific tests.

The plugin performs provider fetch, transformation, and validation, then submits Arrow data through
the core transactional writer. It never opens DuckDB, emits SQL, defines public DataLoader query
semantics, or imports itself on the read path.

Dataset settings use keys under `dataset.<dataset-name>.*`. The generic configuration service
routes such a key and candidate JSON value to the registered owner plugin, then commits the returned
normalized value atomically. Unknown keys or invalid values leave configuration unchanged. Core
configuration, CLI, cron, and task code never parses a symbol, selector, provider reference, or
other dataset-specific value. Each task receives one immutable snapshot of its plugin settings and
their revision; the plugin alone derives parameterless `update` work and readiness from those
settings and committed dataset state.
Normalization must be deterministic and side-effect-free. It may read the committed data of a
declared dependency through the public DataLoader, but must not call a provider, fulfill the
dependency, submit a task, or import that dependency's plugin. If required validation data is not
initialized, return an error with the ordinary dataset operation the user should run first.

## Module organization and import boundaries

Use the following package-level separation as the codebase grows:

- core services, public contracts, the DataLoader, server, CLI, transactional storage adapter, tasks,
  configuration, cron, and events live outside concrete provider and dataset packages;
- built-in concrete dataset plugins live under `findata.datasets.<provider>`;
- built-in provider transports and clients live under `findata.providers.<provider>`;
- reusable opt-in plugin helpers live under `findata.toolkit`; and
- provider mocks and test-only helpers live under `findata.testing` and are loaded only by an
  explicitly selected mock adapter.

Core modules must not import `findata.toolkit`, a concrete dataset package, or a concrete provider
package. Discovery crosses that boundary through entry points and declared contracts. A dataset
plugin may import public core contracts, its provider adapter, and selected toolkit components. A
toolkit component may import public core contracts but never a concrete dataset or provider. Keep
dataset-specific setting schemas, parsers, selector syntax, and orchestration inside its plugin
package even when they use a toolkit resolver after parsing.

## Adding a toolkit component

Start with a private implementation in one dataset. On second use:

1. document the common capability requirements;
2. extract a dataset-neutral interface;
3. document invariants and failure behavior in [TOOLKITS.md](TOOLKITS.md);
4. retain provider-specific limits as parameters rather than branches on dataset names;
5. add unit tests and integration tests for both consuming datasets.

Toolkit components are opt-in plugin helpers. The core DuckDB adapter is not a toolkit component.

## Storage and reader development

Every v1 tabular dataset uses Solution B: one DuckDB file and one cross-process gate per dataset.
Plugins cannot select a storage engine or private reader. Core owns database creation,
metadata tables, parameterized SQL, transactions, coverage mutation, revision assignment, Arrow
results, checkpointing, and recovery. Plugins provide only registered mutation scopes and validated
Arrow input.

Never keep a read/write DuckDB connection open outside the exclusive gate. DataLoader opens a
read-only connection only after acquiring the shared gate and closes it before releasing that gate;
batch readers retain both for their context lifetime. Provider calls and transformation occur before
exclusive-gate acquisition so database commits remain bounded.

The writer accepts complete replacement and registered key/time-range replacement, not plugin SQL.
Validate the resulting affected scope for logical primary-key uniqueness. A plugin exposes only
deterministic, independently committable work items; a complete-table replacement is necessarily one
indivisible item. Benchmark whether a
persistent DuckDB primary-key index helps each data shape before introducing one; it is not a v1
contract. Transaction batches target about one minute and explicit request-count and staged-byte
limits, observed only between indivisible work items, so failure replay, WAL growth, memory, and
reader blocking remain bounded without weakening replacement atomicity.

Use DuckDB APIs for recovery and WAL/checkpoint handling; never unlink a WAL manually. Dataset reset
holds the task mutex after rejecting queued or active work, creates and closes a valid empty temporary
database, acquires the exclusive gate, atomically replaces the old database on the same filesystem,
and flushes the containing directory. v1 does not require full-copy compaction; if introduced later,
it must be an explicit maintenance action rather than an incidental side effect of update.

Before implementation is considered complete, benchmark initial load, daily append, bounded refresh,
common DataLoader queries, Arrow streaming, reader/writer blocking, WAL and steady disk growth,
checkpoint latency, reset, crash recovery, and wheel installation with the pinned DuckDB version.

Detailed workspace, database metadata, plugin, task-message, and HTTP schemas belong under
`docs/specs/`. Specs may elaborate an architectural contract but may not override
[DESIGN.md](DESIGN.md).

## Documentation workflow

- Agree on a design shift before changing the canonical design.
- Update the owning document in the same change as the behavior it describes.
- Link to canonical statements instead of copying them.
- Keep human-readable documents concise; detailed machine contracts belong in schemas or development-time specs.
- Reference sections by name rather than number because numbering moves.
- Archive superseded implementation specs instead of leaving them to compete with current design.

## Coding-agent guidance

Keep `AGENTS.md` and `CLAUDE.md` short. Project design, development, testing, dataset, toolkit, and user knowledge belongs in the documents that own those subjects rather than in agent-specific instruction files.

## Environments

Use `uv` for dependency resolution and the project development environment. Keep its lockfile in
version control and use locked environments in CI. Development and automated tests must not mutate
user data outside their allocated temporary workspace. Normal operating-system temporary
directories and tool caches are allowed; the repository's `workspaces/` directory is reserved for
manual verification and must never be used by automated tests.

Use Nox as the multi-environment runner. Its sessions must create clean environments for each
supported Python version and verify:

- build and installation from the produced wheel, not only an editable source tree;
- invocation of the installed `findata` and `findata-server` entry points;
- the default test suite and static checks; and
- the mocked end-to-end quick start in an isolated temporary workspace.

The supported-version matrix must be explicit in the Nox configuration and CI rather than inferred
from whichever interpreters happen to be installed locally. Add that configuration before treating
the multi-version installation gate as complete.

## Git branch workflow

`main` and `dev` are the only long-lived branches:

- `main` contains releasable code. It accepts release merges from `dev` and narrowly scoped urgent
  fixes; incomplete development does not land there.
- `dev` is the integration branch for the next release. Completed feature and maintenance work is
  merged into it only after its required tests pass.
- `feature/<short-name>` branches contain one new feature or coherent design change. They branch
  from the current `dev`, follow test-driven development, and merge back into `dev`; they never
  merge directly into `main`.

A release merges the tested `dev` state into `main`, records the release version, and tags the
resulting `main` commit. An urgent production fix starts from `main`, stays limited to the fix and
its regression test, and returns to `main` through review when available. Every such fix is then
merged from the fixed `main` into `dev` immediately so later releases cannot reintroduce the defect.

Passing the implementation, documentation, and verification gates makes a version release-ready;
it does not authorize a release. A human must explicitly confirm each release before `dev` is
merged into `main`, the final release version is assigned, or a release tag or artifact is created
or published. In particular, an unreleased v1 remains development work until that confirmation is
given.

Do not rewrite shared `main` or `dev` history. Keep unrelated changes out of feature and fix commits,
and do not merge while required tests or documentation updates are incomplete.

## CLI presentation development

Keep presentation separate from command execution. Commands produce semantic result or event
objects; centralized human, JSON, and JSONL renderers decide how to display them. A shared terminal
capability detector owns TTY, width, Unicode, color, and `NO_COLOR` handling. Command handlers must
not embed ANSI sequences, draw their own tables, or reinterpret server lifecycle states.

The human renderer provides reusable table, labeled-detail, status, progress, empty-state, and
error views. It may shorten identifiers for display only when the full identifier remains available
for copying. Rich owns the interactive live-progress region; command and presentation code must not
manually compose cursor movement or erase-line sequences. The progress renderer writes only to
stderr, uses a transient Rich display only on an interactive terminal, and stops that display before
printing a diagnostic, detachment notice, error, or terminal summary. Redirected human output keeps
the existing newline-delimited plain-text fallback. Rich never renders JSON or JSONL. Rendering
failures must not change task execution or corrupt a structured result.

Dataset operation shortcuts and shell completion are generated from provider/plugin metadata; core
CLI code must not contain a dataset-name switch or parse selector syntax. The generic task transport
remains canonical. Quiet, verbose, and no-progress modes are presentation policies and must not
alter requests, task execution, or structured records.

Identifier-prefix resolution belongs to the server-side resource store, not the CLI. The CLI sends
the operand unchanged. Under the same lock used to access retained resources, the resolver gives an
exact match precedence, validates the minimum prefix length, and returns either one full identifier
or a typed invalid, not-found, or ambiguous result. State-changing handlers act only after that
atomic resolution. Task routes search handle identifiers only, so cancellation cannot target a
shared execution record.

Human value formatting uses semantic field descriptors or explicit presentation view models.
Formatters for timestamps, durations, counts, percentages, exact decimals, and measurements must
not mutate transport DTOs or affect JSON and JSONL serialization. Do not infer semantics from a
generic numeric type, a substring in a field name, or the process locale.

Task diagnostics use a typed semantic object with severity, stable code, message, optional context,
and occurrence count. Persist it through the existing task-log or event path before presentation.
The human follow renderer owns the bounded visible set, repeat aggregation, suppression counters,
and terminal cleanup; the JSONL renderer emits all diagnostic information without applying that
limit. Terminal failures bypass human suppression. These rules add no second diagnostic store or
new retention policy.

The JSON renderer emits one complete JSON document. The JSONL renderer emits one complete object
per event or record, including a stable `type` discriminator. Their field schemas belong in the
HTTP/CLI implementation specifications under `docs/specs/`; presentation-only changes must not
silently change those schemas.
