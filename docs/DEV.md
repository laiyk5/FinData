# Development guide

This file owns contributor workflow and implementation guidance. Architecture belongs to [DESIGN.md](DESIGN.md), dataset contracts to [DATASETS.md](DATASETS.md), toolkit contracts to [TOOLKITS.md](TOOLKITS.md), and verification policy to [TEST.md](TEST.md).

## Development principles

- Keep the user story working through small vertical slices.
- Put policy in the component that owns it; do not duplicate schema coercion, coverage rules, or query semantics across server, CLI, and plugins.
- Prefer declarative manifests and schemas at process and package boundaries.
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

Provider code never logs or returns credentials. Every external request, including readiness probes, goes through the shared provider limiter.

## Adding a dataset plugin

Before implementation, add its canonical entry to [DATASETS.md](DATASETS.md). Then define:

1. provider, logical Arrow schema, and keys;
2. capabilities and maintenance-universe mode;
3. publication window, schedule, and missing-data policy;
4. dependencies and optional fulfillment requirement schema;
5. operation entry points and operand JSON schemas;
6. supported storage strategy and DataLoader metadata;
7. coverage and status behavior;
8. mock generator and dataset-specific tests.

The plugin performs provider fetch, transformation, validation, and staged writing. It does not define public DataLoader query semantics or import itself on the read path.

## Adding a toolkit component

Start with a private implementation in one dataset. On second use:

1. document the common capability requirements;
2. extract a dataset-neutral interface;
3. document invariants and failure behavior in [TOOLKITS.md](TOOLKITS.md);
4. retain provider-specific limits as parameters rather than branches on dataset names;
5. add unit tests and integration tests for both consuming datasets.

Toolkit components are opt-in plugin helpers. Core reader adapters are not toolkit components.

## Storage and reader development

A supported physical layout has two coordinated halves:

- a plugin-side writer, usually supplied by the toolkit;
- a core reader adapter selected declaratively from the manifest.

The DataLoader owns filtering, ordering, coverage validation, locking, snapshot selection, Arrow results, and query-engine selection. A novel layout requires a reusable writer and compatible core reader adapter rather than a private dataset query engine.

The storage implementation may use whole-generation directories, content-addressed files, or another representation, but it must prove the atomic publication-snapshot and reader-lifetime guarantees in [DESIGN.md](DESIGN.md). Benchmark and garbage-collection choices are implementation gates, not competing architectural contracts.

Detailed workspace, manifest, plugin, task-message, and HTTP schemas should be placed under `docs/specs/` when implementation begins. Specs may elaborate an architectural contract but may not override [DESIGN.md](DESIGN.md).

## Documentation workflow

- Agree on a design shift before changing the canonical design.
- Update the owning document in the same change as the behavior it describes.
- Link to canonical statements instead of copying them.
- Keep human-readable documents concise; detailed machine contracts belong in schemas or development-time specs.
- Reference sections by name rather than number because numbering moves.
- Archive superseded implementation specs instead of leaving them to compete with current design.

## Coding-agent guidance

Keep `AGENTS.md` and `CLAUDE.md` short. Project design, development, testing, dataset, toolkit, and user knowledge belongs in the documents that own those subjects rather than in agent-specific instruction files.
