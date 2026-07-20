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
- index-selector month expansion, including failure rather than stale fallback for unavailable `@latest` coverage.

A provider-family harness may share envelope and failure simulation, but row generation remains dataset-specific.

### Integration tests

Exercise real component boundaries:

- plugin writer to manifest to core reader adapter;
- server to task process communication;
- dependency fulfillment and parent retry;
- provider limiter shared by concurrent tasks;
- CLI to authenticated HTTP API;
- cron to TaskRunner submission;
- event persistence and acknowledgement;
- DataLoader reader/writer locking.

### Smoke tests

After a change appears complete, run a minimal representative operation and query its published result through the public DataLoader API.

### End-to-end tests

E2E tests use real user surfaces. CLI tests execute commands and inspect stdout, stderr, exit codes, and structured output. A future web UI must be tested by opening it and exercising the relevant controls.

The primary required E2E scenario is the workflow in [USER.md](USER.md#quick-start): configure a mocked Tushare provider, set a CSI 300 universe, backfill `tushare_daily_basic`, fulfill dependencies, query covered data, enable cron, inject a failure, and verify that rerunning resumes unresolved intervals.

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

## Test ordering

Use this default sequence:

1. schema and unit tests;
2. mocked provider and plugin tests;
3. storage/DataLoader integration tests;
4. TaskRunner and API integration tests;
5. smoke test;
6. mocked end-to-end story;
7. optional real-provider tests.
