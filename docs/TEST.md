# Testing

This file owns findata's testing methodology and required verification. It tests contracts defined in [DESIGN.md](DESIGN.md), [DATASETS.md](DATASETS.md), [TOOLKITS.md](TOOLKITS.md), and [USER.md](USER.md) without redefining them.

## Principles

1. Test cheap, deterministic behavior before expensive or external behavior.
2. Test dependencies before their consumers, recursively.
3. Use provider mocks before real APIs.
4. Test observable outcomes, not merely that code paths ran.
5. Add a regression case whenever an escaped failure was not already covered.

## Test layers

### Unit tests

Cover small components and boundary cases, especially:

- publication-window classification;
- coverage interval arithmetic and continuity;
- request parsing, pruning, merging, and batching;
- operand and requirement schemas, including scalar-to-array coercion and half-open `today` ranges;
- logical Arrow field types, nullability, date normalization, and primary-key uniqueness;
- rate-limit accounting;
- cron/timezone calculation;
- manifest and configuration validation;
- plugin-setting schema validation, normalization, immutable task snapshots, and update readiness;
- query-filter translation;
- task and handle state transitions.

### Mocked plugin tests

Every dataset has a deterministic mock generator matching its schema and response shape. Tests cover:

- normal rows;
- legitimate and transient empty responses;
- provider errors and malformed payloads;
- rate limiting and retries;
- transformation and validation failures;
- partial backfills and reruns;
- dataset-owned index-selector parsing and month expansion, including failure rather than stale
  fallback for unavailable `@latest` coverage;
- provider index-catalog synchronization across declared markets, exact provider-ID preservation,
  unknown references, and the distinction between catalog membership and confirmed
  `index_weight` availability;

A provider-family harness may share envelope and failure simulation, but row generation remains dataset-specific.

### Integration tests

Exercise real component boundaries:

- plugin writer to manifest to core reader adapter;
- server to task process communication;
- dependency fulfillment and parent retry;
- provider limiter shared by concurrent tasks;
- CLI to authenticated HTTP API;
- generic configuration routing to the owning plugin, including atomic rejection without mutation;
- local setting validation through a declared dependency's committed DataLoader snapshot, with no
  provider call, task submission, plugin import, or implicit fulfillment;
- cron readiness derived by the plugin from an immutable settings snapshot;
- cron to TaskRunner submission;
- event persistence and acknowledgement;
- DataLoader reader/writer locking.

### Smoke tests

After a change appears complete, run a minimal representative operation and query its published result through the public DataLoader API.

### End-to-end tests

E2E tests use real user surfaces. CLI tests execute commands and inspect stdout, stderr, exit codes, and structured output. A future web UI must be tested by opening it and exercising the relevant controls.

The primary required E2E scenario is the workflow in [USER.md](USER.md#quick-start): configure a
mocked Tushare provider, synchronize its index catalog, backfill `tushare_daily_basic`, configure
the plugin's `update_symbols`, fulfill dependencies, query covered data, enable cron, inject a
failure, and verify that rerunning resumes unresolved intervals.

The reserved token `findata-mock` selects the deterministic Tushare mock without contacting the
provider. `findata-mock:fail=<api>@<call>` injects one terminal failure at the numbered call to that
mock API; for example, `findata-mock:fail=daily_basic@2` fails after the first daily-basic request
has been checkpointed. These values are testing controls, are never valid real credentials, and
must be identified as mock mode by provider status and readiness output.

Real-provider E2E tests are opt-in, use dedicated credentials, respect the provider limiter, and never run as the default test suite.

Each automated E2E run creates a unique clean workspace through the operating system's temporary-
directory facility, executes the quick start with that path substituted for `~/market-data`, and
cleans it afterward. On failure, the harness may preserve the workspace as a named test artifact.
Automated tests never read or write the repository's `workspaces/` directory; it is reserved for
manual, in-person verification.

Exit codes, stdout, stderr, structured records, and resulting workspace state are the primary E2E
evidence. PTY transcripts or terminal snapshots verify interactive presentation. Screenshots may
supplement a manual visual check, but are not the sole automated proof of correctness.

## CLI presentation matrix

CLI presentation tests cover:

- interactive TTY and redirected stdout and stderr independently;
- `--color auto`, `always`, and `never`, `NO_COLOR`, `TERM=dumb`, and a plain-text fallback;
- common and narrow terminal widths, long values, and identifiers that must remain copyable;
- tables, labeled detail views, empty results, warnings, actionable errors, and terminal summaries;
- delayed commands without spinner flicker, progress-line replacement, and cleanup after success,
  failure, cancellation, connection loss, and interruption;
- Ctrl-C while waiting or following detaching without canceling the server task;
- immediate task-acceptance output and faithful rendering of queued, waiting, running, and terminal
  state changes supplied by the server; and
- JSON as exactly one document and JSONL as one typed object per line, with no ANSI sequences,
  animation, readiness banner, or human commentary under any terminal configuration.

Identifier tests cover full and exact identifiers, the shortest valid unique prefix, a too-short
prefix, no match, and deliberately colliding retained prefixes. They verify `400`, `404`, and `409`
results as applicable and prove that ambiguity has no side effects. Resolution is also exercised
against concurrent retention changes, and task cancellation proves that a handle prefix cannot
resolve an execution identifier.

Human-formatting tests use semantic fields at unit boundaries: subsecond and multi-minute
durations, timezone offsets and date rollover, grouped counts, declared percentage precision,
scientific-notation thresholds, identifiers that look numeric, and exact decimal values. Matching
JSON and JSONL assertions prove that presentation leaves raw values and types unchanged.

Diagnostic tests cover fewer than, exactly, and more than ten distinct warning and error messages;
interleaved severities; exact repeats; and a terminal failure after the visible limit. PTY evidence
verifies the replaceable suppression line and cleanup. Redirected stderr verifies the single
suppression notice and final exact totals. JSONL assertions account for every logical occurrence,
including counts carried by aggregated records, and prove that human suppression loses no
structured diagnostic data.

Snapshot tests normalize nondeterministic timestamps, durations, paths, and identifiers but retain
their labels and shapes. Semantic assertions accompany snapshots so a cosmetically accepted update
cannot hide a missing status, result, or recovery instruction.

## Crash and concurrency matrix

Crash-safety claims require deterministic fault injection at least at:

1. task record creation before and after process spawn;
2. each publication staging and commit boundary;
3. manifest replacement before acknowledgement;
4. server death while a task is fetching, waiting, and committing;
5. startup orphan detection, including simulated PID reuse;
6. cleanup of abandoned staging data and unreachable publication snapshots.

Concurrency tests cover:

- multiple readers during publication;
- a batch iterator holding its snapshot while a writer waits;
- cancellation during rate-limit, dependency, and write-gate waits;
- multiple datasets sharing one provider limiter;
- queue limits, canonical coalescing keys, non-coalescing `update` submissions, independent handle states, and last-subscriber cancellation;
- shutdown with queued, waiting, and running work.

## Time and scheduling matrix

Test publication windows and cron schedules across:

- before, inside, and after a window;
- weekends, holidays, and calendar dependencies;
- IANA timezone differences;
- daylight-saving gaps, which must skip and warn, and repeated times, which must run once at the first occurrence;
- server downtime and missed enabled jobs;
- schedule overrides and resets.

## DataLoader contract matrix

Every supported reader strategy is tested for equivalent behavior across:

- projection, filters, ordering, and limits;
- eager tables and batch iteration;
- initialized, uninitialized, empty, and incompatible datasets;
- incompatible-layout reads leaving the workspace byte-for-byte unchanged;
- covered and missing intervals;
- observation-domain coverage, including closed market dates and resolved-empty trading dates;
- unsupported key/time queries;
- low-memory batch reads;
- reader-adapter errors without importing dataset plugins.

## Package-boundary checks

Automated import checks reject dependencies from core modules to `findata.toolkit`, built-in
dataset packages, built-in provider packages, or `findata.testing`. They also reject toolkit
imports of concrete datasets or providers and read-path imports of maintenance plugins. Positive
fixtures prove that entry-point discovery works and that a dataset plugin can use public core
contracts, its provider adapter, and selected toolkit components. Explicit mock mode is the only
runtime path allowed to load `findata.testing`.

## Test ordering

Use this default sequence:

1. schema and unit tests;
2. mocked provider and plugin tests;
3. storage/DataLoader integration tests;
4. TaskRunner and API integration tests;
5. smoke test;
6. mocked end-to-end story;
7. optional real-provider tests.
