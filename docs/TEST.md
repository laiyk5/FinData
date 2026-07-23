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
- database metadata, storage-version, and configuration validation;
- provider-then-dataset entry-point discovery, duplicate provider IDs, malformed provider
  contracts, and dataset references to unknown providers;
- plugin-setting schema validation, normalization, immutable task snapshots, and update readiness;
- parameterized SQL translation with identifiers restricted to registered schemas;
- checkpoint-batch request, byte, and duration boundaries, including an oversized indivisible
  complete replacement that must not be split;
- task and handle state transitions.

### Mocked plugin tests

Every dataset has a deterministic mock generator matching its schema and response shape. Tests cover:

- normal rows;
- legitimate and transient empty responses;
- provider errors and malformed payloads;
- rate limiting and retries;
- transformation and validation failures;
- partial backfills and reruns;
- dataset-owned index-selector parsing and as-of snapshot resolution without look-ahead bias;
- per-index metadata materialization and refresh, exact provider-ID preservation, unknown
  references, rejection of empty or mismatched responses, no implicit market enumeration, and the
  distinction between metadata presence and confirmed `index_weight` availability;
- `tushare_index_basic.update` refreshing only materialized references and
  `tushare_index_weight.complete` never mutating `update_indexes`;

A provider-family harness may share envelope and failure simulation, but row generation remains dataset-specific.

### Integration tests

Exercise real component boundaries:

- plugin Arrow mutation to core DuckDB transaction to DataLoader Arrow result;
- server to task process communication;
- dependency fulfillment and parent retry;
- provider limiter shared by concurrent tasks;
- CLI to authenticated HTTP API;
- generic configuration routing to the owning plugin, including atomic rejection without mutation;
- local setting validation through a declared dependency's committed DataLoader revision, with no
  provider call, task submission, plugin import, or implicit fulfillment;
- cron readiness derived by the plugin from an immutable settings snapshot;
- cron to TaskRunner submission;
- event persistence and acknowledgement;
- DataLoader shared-reader/exclusive-writer locking and connection lifetime;
- dataset initialization and confirmed reset, including rejection during queued or active work.

### Smoke tests

After a change appears complete, run a minimal representative operation and query its published result through the public DataLoader API.

### End-to-end tests

E2E tests use real user surfaces. CLI tests execute commands and inspect stdout, stderr, exit codes, and structured output. A future web UI must be tested by opening it and exercising the relevant controls.

The primary required E2E scenario is the workflow in [USER.md](USER.md#quick-start): configure a
mocked Tushare provider, materialize one exact index reference, backfill `tushare_daily_basic`,
configure the plugin's `update_symbols`, fulfill dependencies, query covered data, enable cron,
inject a failure, and verify that rerunning resumes unresolved intervals.

The reserved token `findata-mock` selects the deterministic Tushare mock without contacting the
provider. `findata-mock:fail=<api>@<call>` injects one terminal failure at the numbered call to that
mock API. Recovery scenarios set a deterministic test-only checkpoint-batch cap so the injected
failure occurs after at least one committed batch; a provider-request boundary alone never implies
a commit. These values are testing controls, are never valid real credentials, and must be
identified as mock mode by provider status and readiness output.

Real-provider E2E tests are opt-in, use dedicated credentials, respect the provider limiter, and never run as the default test suite.

The Tushare real-provider gate accepts `TUSHARE_API_TOKEN` or `TUSHARE_API_KEY` only when
`FINDATA_ALLOW_REAL_API=1` records the human approval. Run it serially with fail-fast behavior. Its
bounded v1 smoke plan first makes one `trade_cal` canary request, then exact-code requests for
`stock_basic`, `index_basic`, one month of `index_weight`, and one symbol-date of `daily_basic`.
Only after those contracts pass may it run the isolated plugin/storage workflow: two one-day trade
calendar requests, one exact index metadata request, and one index-month weight request. The normal
maximum is nine provider requests, the HTTP client disables automatic retries, and the suite never
expands index constituents, downloads a full market, or writes the user's workspace.

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
- Rich live-progress construction with `transient=True`, exactly one live task, and `stop()` before
  any persistent diagnostic or terminal summary;
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

### CLI matrix coverage map

Each matrix cell names its covering tests; extend the map in the same change that adds or alters
presentation behavior. Pure parsing, formatting, and transport-error cells live in
`tests/test_cli_units.py` and run without a server.

- TTY and redirected streams, color modes, `NO_COLOR`, `TERM=dumb`, plain-text fallback:
  `test_cli_presentation.py` color and pager tests.
- Widths, long values, copyable identifiers:
  `test_empty_and_narrow_human_views_remain_readable`.
- Tables, labeled details, empty results, actionable errors, terminal summaries:
  `test_human_collections_and_details_are_not_raw_json`, `test_cli_snapshots.py`.
- Delayed commands, progress replacement, cleanup on success, failure, cancellation, connection
  loss, and interruption: `test_cli_pty.py`, `wait_reports_acceptance_and_ctrl_c_detaches`,
  `logs_follow_ctrl_c_detaches`, and `test_cli_units.py` error-mapping tests including
  `test_connection_loss_while_waiting_names_the_inspection_command`.
- Rich live-progress construction and fallback:
  `test_interactive_progress_uses_one_transient_rich_live_task`,
  `rich_rendering_failure_falls_back_to_plain_text`,
  `persistent_follow_log_stops_live_progress_first`, and the PTY live-region transcript.
- JSON as one document and JSONL as typed records under any terminal configuration:
  `json_wait_is_exactly_one_document`, `jsonl_follow_emits_only_typed_records`,
  `json_error_is_structured_and_json_follow_is_rejected`.
- Identifier prefixes, ambiguity without side effects, and handle-only resolution:
  `test_public_contract.py` and `test_server_cli.py` prefix tests.
- Human value formatting and unchanged JSON/JSONL values:
  `semantic_values_are_humanized_without_changing_json` and `test_cli_units.py`
  `HumanFormattingTests`.
- Diagnostic visibility limits, suppression, and lossless structured output:
  `redirected_human_diagnostics_are_bounded_with_exact_totals`,
  `jsonl_diagnostics_keep_every_logical_occurrence`, and the PTY suppression transcript.
- Normalized snapshots with semantic assertions: `test_cli_snapshots.py`.
- Workflow, completion, quiet/verbose/no-progress, dry-run, retry, explain, and watch coverage:
  `test_server_cli.py`, `test_public_contract.py` completion tests, and `test_data_cli.py`.
- Read-side commands leaving no filesystem trace:
  `unknown_dataset_reads_never_create_directories` and `test_data_cli.py` side-effect tests.

CLI workflow tests cover schema-derived dataset operation flags and their generic `task run`
equivalence; side-effect-free dry runs with complete and incomplete local dependencies; retry using
normalized historical operands and current configuration; read-only explanation of nested failures;
watch attachment; dynamic completion and offline fallback; quiet, verbose, and no-progress policies;
and progress metrics for provider requests, rows, checkpoints, elapsed time, and ETA. Tests assert
that dry-run creates no task record, provider request, event, configuration revision, dataset
revision, or publication.

Click migration tests invoke root and nested help through the embedded `main()` function and prove
that help and version requests neither require a workspace nor raise `SystemExit`. Parity tests retain
existing option positions, exit codes, injected streams, structured errors, completion candidates,
and HTTP request bodies. No test may rely on Click's global process streams when explicit streams are
injected.

The command tree is traversed recursively to require a purpose statement on every public family and
command and explanatory help on every positional argument and option. Representative nested help
tests verify that the command purpose, Arguments section, option descriptions, accepted forms, and
defaults are visible without a workspace.

Task-process tests allow for cold interpreter and dependency imports from an isolated installed
wheel. The production authenticated-channel launch budget is at least 30 seconds; focused test waits
must not impose a shorter five-second deadline that can expire under multi-version release load.
The installed quick-start E2E permits up to 120 seconds for a multi-dependency CLI task; this is a
failure ceiling, not an expected duration, and prevents cold imports from masquerading as a hang.

Completion tests exercise both forms emitted by shells at a token boundary: an explicit empty
current word and no trailing word. Root completion must return command families, an exact family
must return its actions, and `data coverage` must return registered dataset names with or without a
running server. Generated bash, zsh, and fish scripts remain sourceable shell integration, while
documentation makes activation an explicit user step.

Data CLI tests cover schema metadata, bounded preview, supported and unsupported coverage, strict
and explicitly partial reads, column and key selection, empty results, and actionable missing-range
errors. Coverage tests verify stored dates remain visible in human tables and requested ranges report
completeness and exact half-open gaps. Presentation tests use TTY and non-TTY streams to prove long
human output pages only when interactive; structured, redirected, and export stdout never page.
Export tests read outputs back with Arrow, exercise multiple record batches, verify CSV,
Parquet, Arrow IPC, and JSONL schemas and row values, reject accidental overwrite, and prove atomic
cleanup after failure. Spies assert no HTTP client, task record, provider transport, write gate,
dataset revision, or publication change. Stdout exports assert byte-for-byte data-only output with
all diagnostics on stderr.

Side-effect tests invoke schema, preview, coverage, export, and each DataLoader read against unknown
and path-traversing dataset names. They compare the workspace tree before and after failure and prove
that no dataset directory, gate, DuckDB file, or export target appears. A missing registered gate is
an error rather than something a read repairs. Registration tests separately prove that only valid
single-component dataset names may create dataset storage.

## Crash and concurrency matrix

Crash-safety claims require deterministic fault injection at least at:

1. task record creation before and after process spawn;
2. before and after DuckDB transaction begin, data mutation, coverage mutation, metadata mutation,
   durable commit, and acknowledgement;
3. abandonment of a validated but uncommitted checkpoint batch;
4. server death while a task is fetching, waiting, and committing;
5. startup orphan detection, including simulated PID reuse;
6. DuckDB WAL recovery and cleanup of Findata-owned temporary inputs without direct WAL deletion;
7. atomic dataset initialization and reset-file replacement.

Concurrency tests cover:

- multiple read-only connections to one dataset and independent access to different datasets;
- a batch iterator holding its shared gate and database view while a writer waits;
- proof that no read/write connection exists outside the exclusive gate and no read-only connection
  outlives the shared gate;
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

## DuckDB storage and DataLoader contract matrix

The core DuckDB adapter and DataLoader are tested across:

- projection, filters, ordering, and limits;
- eager tables and batch iteration;
- initialized, uninitialized, empty, and incompatible datasets;
- incompatible-layout reads leaving the workspace byte-for-byte unchanged;
- covered and missing intervals;
- observation-domain coverage, including closed market dates and resolved-empty trading dates;
- unsupported key/time queries;
- low-memory batch reads;
- storage and SQL errors without importing dataset plugins;
- complete replacement, key/time-range replacement, coverage-only commits, and logical primary-key
  duplicate rejection;
- proof that only independently committable work items may cross transaction boundaries;
- publication ID and revision changes exactly once per committed checkpoint batch;
- no retained historical database revisions after ordinary updates.

Storage benchmarks use representative scaled market data and record initial-load throughput, daily
append and bounded-refresh write amplification, common query latency, Arrow streaming memory,
reader-block duration during commit, database/WAL peak and steady sizes, checkpoint latency, reset,
and crash recovery. Acceptance thresholds belong in the v1 implementation specification before
coding is declared complete. A full-copy compaction observed during an ordinary update fails the
gate regardless of elapsed time.

## Package-boundary checks

Automated import checks reject dependencies from core modules to `findata.toolkit`, built-in
dataset packages, built-in provider packages, or `findata.testing`. They also reject toolkit
imports of concrete datasets or providers and read-path imports of maintenance plugins. Positive
fixtures prove provider-then-dataset entry-point discovery and that a dataset plugin can use public
core contracts, its provider adapter, and selected toolkit components. Explicit mock mode is the
only runtime path allowed to load `findata.testing`.

Packaging tests assert the pinned Hatchling backend and explicit `src/findata` wheel selection.
The release-readiness gate builds an sdist and then a wheel from that sdist, installs the wheel into
an isolated environment, invokes both console scripts, and verifies that every declared
`findata.datasets` and `findata.providers` entry point remains discoverable.

## Test ordering

Use this default sequence:

1. schema and unit tests;
2. mocked provider and plugin tests;
3. storage/DataLoader integration tests;
4. TaskRunner and API integration tests;
5. smoke test;
6. mocked end-to-end story;
7. optional real-provider tests.
