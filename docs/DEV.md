# Development guide

This file owns contributor workflow and implementation guidance. Architecture belongs to [design/core.md](design/core.md), dataset contracts to [design/dataset/index.md](design/dataset/index.md), toolkit contracts to [design/toolkit/index.md](design/toolkit/index.md), and verification policy to [TEST.md](TEST.md).

## Development principles

- Keep the user story working through small vertical slices.
- Put policy in the component that owns it; do not duplicate schema coercion, coverage rules, or query semantics across server, CLI, and plugins.
- Prefer declarative contracts and schemas at process and package boundaries.
- Promote a dataset-private helper into the toolkit only when a second dataset needs it.
- Preserve user data and unrelated workspace changes while developing.
- When a failure escapes existing tests, add a regression case.

## Adding a provider

The decoupling goals and invariants for plugins live in [design/plugins.md](design/plugins.md); this
section is the implementation checklist. Define and document:

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

The provider's runtime object must satisfy the `findata.plugins.ProviderRuntime` protocol;
discovery rejects an incomplete runtime. The task-process contracts handed to its operation
worker — `OperationRequest`, `OperationReporter`, and `OperationWorker` — are public contracts
in `findata.contracts`, re-exported from `findata.plugins`, and the built-in Tushare runtime is
the reference implementation.

If a provider exposes instrument-reference metadata, preserve its identifiers as opaque values,
record how explicitly requested references are materialized and refreshed, and keep
endpoint-capability checks separate from metadata presence. Do not implement cross-provider
identity by transforming codes or matching names. Reference lookup belongs to provider-specific
dataset plugins, not to core findata; do not enumerate a provider's markets unless a dataset
contract explicitly requires it.

Provider code never logs or returns credentials. Every external request, including readiness probes, goes through the shared provider limiter.

## Adding a dataset plugin

Before implementation, add its canonical entry to [design/dataset/index.md](design/dataset/index.md). Then define:

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

The repository is a uv workspace:

- core services, public contracts, the DataLoader, server, CLI, transactional storage adapter, tasks,
  configuration, cron, and events live in the `findata` distribution under `src/findata`;
- each official provider family is its own plugin distribution under `plugins/<family>/`
  (for example `plugins/tushare/` ships `findata-tushare` with the import package
  `findata_tushare`, containing its provider adapter, dataset plugins, and mock/test helpers);
- reusable opt-in plugin helpers live under `findata.toolkit` in the core distribution.

Core modules must not import `findata.toolkit` or any plugin distribution. Discovery crosses that
boundary through entry points and declared contracts. A dataset plugin may import public core
contracts, its provider adapter, and selected toolkit components; a plugin distribution never
imports another plugin distribution. A toolkit component may import public core contracts but
never a concrete plugin. Keep dataset-specific setting schemas, parsers, selector syntax, and
orchestration inside its plugin package even when they use a toolkit resolver after parsing.
Plugin mocks and test-only helpers live in the plugin's own testing module and are loaded only
by an explicitly selected mock mode.

findata depends on each official plugin distribution by default so a plain install works out of
the box; a plugin distribution deliberately does not declare the reverse dependency, avoiding
circular metadata. Adding a new provider family means adding a new `plugins/<family>/` workspace
member with its own entry points — never editing core. The nox wheel gate builds and installs
every distribution.

## Adding a toolkit component

Start with a private implementation in one dataset. On second use:

1. document the common capability requirements;
2. extract a dataset-neutral interface;
3. document invariants and failure behavior in [design/toolkit/index.md](design/toolkit/index.md);
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

Directory creation belongs to the component that initializes the owned resource: `Workspace.init`
for workspace state and dataset registration for a dataset directory, gate, and database. Generic
lock helpers and read paths must never call `mkdir`, touch a lock, or let DuckDB create a database.
Resolve dataset names as one path component before filesystem access, and check required registered
artifacts before acquiring a read gate.
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
[design/core.md](design/core.md).

## Documentation workflow

- Agree on a design shift before changing the canonical design.
- Update the owning document in the same change as the behavior it describes.
- Link to canonical statements instead of copying them.
- Keep human-readable documents concise; detailed machine contracts belong in schemas or development-time specs.
- Reference sections by name rather than number because numbering moves.
- Archive superseded implementation specs instead of leaving them to compete with current design.

### User documentation site

User-facing documentation lives in `docs/site/` and is published with MkDocs Material to
<https://laiyk5.github.io/FinData/> by the `docs` GitHub Actions workflow; `docs/USER.md`
is only a redirect stub. Build it locally with `nox -s docs` (equivalently
`uv run --group docs mkdocs build --strict`); the strict build is the gate for broken
links and nav entries. The workflow deploys on pushes to `dev` until the first release,
then only on pushes to `main` (flip the marked line in `.github/workflows/docs.yml`).

- User-visible behavior and syntax are documented once in `docs/site/` and linked from other documents.
- The quick start (`docs/site/get-started/quickstart.md`) must be executed as written before every release.
- Examples should use normal user operations rather than internal shortcuts.
- User documentation may lag unreleased implementation work, but must match every released version.
- Secrets, internal storage paths, and unstable implementation details must not appear in examples.

## Coding-agent guidance

Keep `AGENTS.md` and `CLAUDE.md` short. Project design, development, testing, dataset, toolkit, and user knowledge belongs in the documents that own those subjects rather than in agent-specific instruction files.

## Environments

Use `uv` for dependency resolution and the project development environment. Keep its lockfile in
version control and use locked environments in CI. The documentation toolchain is the `docs`
dependency group in `pyproject.toml`; sync it with `uv sync --group docs`. Development and automated tests must not mutate
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

## WebUI development

The WebUI source lives in `web/` (Vite, React, TypeScript) and builds into
`src/findata/webui/`, which the server serves for non-`/v1` paths. The bundle is git-ignored but
is shipped in the sdist and wheel through Hatchling `artifacts`, so a release build must run the
UI build before packaging. UI work requires Node.js and npm; Python-only changes do not.

```bash
cd web
npm ci
npm run dev        # Vite dev server; proxies /v1 to http://127.0.0.1:8765
npm run typecheck  # tsc --noEmit
npm test           # vitest unit tests
npm run build      # production bundle into src/findata/webui/
```

`nox -s webui` runs typecheck, unit tests, and the production build in one gate.

The WebUI is a thin client: it must not add validation, lifecycle states, or policy that the
server does not already expose. Operation forms are generated from server operation schemas, and
live updates use polling only — do not introduce SSE, WebSockets, or new `/v1` endpoints for UI
convenience. Static assets are served without the token by design (see
[design/core.md](design/core.md#workspace-and-server)); never place secrets or workspace-specific values in
the bundle.

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

Remote Git operations have a separate authorization boundary. Without explicit human permission,
do not perform any operation that uploads data or changes remote state. This includes ordinary or
forced pushes, pushing branches or tags, deleting or renaming remote refs, creating or updating pull
requests or releases, and publishing build artifacts. A general request to work on, release, sync,
or replace a repository is not permission for an individual remote mutation: immediately before
execution, identify the remote, exact refs, operation, and whether history will be rewritten, then
obtain human approval for that operation. Read-only inspection such as `git remote -v`,
`git ls-remote`, and `git fetch` may be used to prepare that review but does not authorize a later
upload. Force pushes require an explicit force-push approval and should use `--force-with-lease`
against a freshly inspected expected remote commit whenever the intended replacement permits it.

Branch upload permission is scoped by ref. `main` and `dev` may be uploaded only when the human
authorization names them or otherwise unambiguously covers them. Every other local branch,
including every `feature/*`, fix, experiment, backup, or audit branch, is local-only by default and
must not be uploaded unless a human explicitly names that branch or an exact set of such branches.
Permission to push `main`, push `dev`, initialize a remote, or synchronize a repository never
implicitly authorizes uploading any additional branch.

## Packaging and builds

Hatchling is the sole PEP 517 build backend. Its version is pinned in `build-system.requires`, and
the wheel target explicitly packages `src/findata`; do not add setuptools-specific configuration.
Project scripts and plugin entry points remain standard `[project]` metadata and must be preserved
identically in both the sdist and wheel. A build-tool change must verify a clean sdist-to-wheel build,
wheel contents, console scripts, and dataset/provider plugin discovery before it is merged.

## CLI presentation development

Click owns command grouping, argument and option parsing, validation, help, and the embeddable
command invocation boundary. Command callbacks return semantic invocation objects to the existing
execution layer; they do not perform HTTP requests or render results. `main()` invokes Click without
standalone process exits and preserves injected stdin, stdout, stderr, and environment mappings for
tests and embedding. Live plugin metadata may extend completion and operation help without importing
dataset implementations into the core CLI.

Every public command and command family must carry a concise purpose statement. Every positional
argument and option must explain its meaning, accepted form, and important default or side effect;
a metavariable alone is not documentation. The command tree has a structural test that rejects an
undocumented command or parameter, and representative nested `--help` output is tested as a user
contract. Hidden machine-protocol commands are the only exception.

When an operand has a finite or discoverable candidate set, new CLI work should provide shell
completion wherever practical. Static command and option candidates come from the Click contract;
registered datasets, providers, operations, settings, and retained identifiers should use local or
server metadata without triggering writes or provider calls. Completion remains best-effort and
must degrade to safe static or local candidates when the workspace or server is unavailable.

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
