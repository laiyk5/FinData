# Toolkit components

This file is the canonical catalog of reusable, opt-in components used by dataset plugins. [DESIGN.md](DESIGN.md) owns the architectural boundaries; [DEV.md](DEV.md) explains how to add or promote a component; [DATASETS.md](DATASETS.md) records which components each dataset uses.

A mechanism is promoted into the toolkit only when a second dataset needs it. Before that, it remains private to its first dataset plugin.

Every component is documented as:

- purpose;
- requirements and capabilities;
- provided interface;
- invariants and failure behavior;
- example.

Toolkit components are plugin-side helpers. DuckDB storage, transactions, SQL generation, and
DataLoader belong to core and are not toolkit components.

Toolkit implementations live under the `findata.toolkit` package. Core modules never import that
package. Dataset plugins opt into individual toolkit components and remain responsible for adapting
their public operands and settings to dataset-neutral toolkit inputs. Toolkit code may depend on
stable public core contracts, but never on a concrete dataset or provider implementation.

## Checkpoint-batch planner

- **purpose**: group provider work into bounded, resumable transactional checkpoints rather than
  committing once per provider request
- **requires**: deterministic, independently committable work items, a declarative core mutation
  scope, and request-count, staged-byte, and approximate-duration limits; a complete-table
  replacement is one indivisible work item
- **interface**: yield ordered batches of validated Arrow inputs and matching coverage deltas
- **invariant**: a work item appears in exactly one batch; limits are observed only between items,
  so one oversized indivisible item forms one oversized batch; processed progress and committed
  progress remain distinguishable
- **failure behavior**: failure discards only the uncommitted batch; a rerun reconstructs it from
  committed coverage without relying on private database state
- **boundary**: the helper never opens DuckDB, issues SQL, manages transactions, or changes the
  dataset gate; those are core storage responsibilities

## Coverage tracker

- **purpose**: remember resolved-empty intervals, enable coverage pruning, and keep coverage/status queries inexpensive
- **requires**: a `time-accumulating` dataset with `strict` or `accept-empty` missing-data policy
- **interface**: submit a coverage delta beside its data mutation so core commits both in one
  transaction; expose committed coverage through DataLoader
- **invariant**: each partition key has one continuous half-open civil-time interval; every due observation in the dataset's declared observation domain inside it is resolved, while non-observation dates require no row
- **failure behavior**: a committed revision never exposes coverage newer than its matching data

## Publication-window pruning

- **purpose**: determine whether requested data is due and whether an empty result is transient or final
- **requires**: a declared publication window
- **interface**: given a target range and the current time, return:
  - `not-due` — prune the interval
  - `due-transient` — fetch, but do not resolve an empty response
  - `due` — fetch and resolve an allowed empty response

## Request optimizer

- **purpose**: minimize provider calls when request rate is the bottleneck
- **requires**:
  - an API indexable by symbol set and/or time range
  - declared `symbol_set_cap` and `row_limit`
  - optional `full_market_fetch` with a declared conservative market-row bound
  - coverage tracking for coverage pruning
  - a declared trading-calendar dependency for trading-day pruning
- **interface**: parse, prune, and merge
  1. Parse into per-symbol, time-ranged requests.
  2. Remove not-due and resolved ranges. Continuous coverage leaves at most two residual ranges per symbol.
  3. Build only request shapes with conservative row estimates within every declared provider limit.
  4. Compare bounded-symbol, split-symbol, and, when a safe market-size bound is declared, full-market/date-by-date shapes.
- **correctness invariants**:
  - the committed target pairs exactly equal the unpruned symbol/observation union; a provider
    request may retrieve a superset only when the operation filters it before commit
  - no request exceeds `symbol_set_cap`, `row_limit`, or another declared provider limit; inability to construct a safe plan is an error before any provider request
  - identical normalized inputs, capabilities, coverage, calendar, publication time, and row bounds produce the same ordered plan
- **objective and tie-breaking**: minimize provider-call count, then estimated returned rows, then choose the lexicographically ordered serialized plan
- **boundary**: assigning merged requests to checkpoint batches belongs to the operation, not the
  optimizer
- **example**: for `tushare_daily_basic`, the symbol cap is one and the row limit is 6000; calendar pruning removes closed dates, and long per-symbol ranges split before their conservative row estimate reaches the limit

## Constituent-set resolver

- **purpose**: resolve a dataset plugin's already-parsed request for index constituents
- **requires**: a constituent dataset declared in `dependencies`
- **interface**: accept a semantic request containing the dependency key and one of:
  - `latest`, resolved as the most recent snapshot effective by a target endpoint; or
  - `range-union`, resolved as the snapshot effective at range start plus changes inside the range
- **boundary**: the toolkit never parses a CLI string, configuration value, provider reference, or
  friendly alias. The consuming dataset plugin owns those syntaxes, validates them against any
  provider reference metadata it uses, and passes the exact dependency key to the resolver.
- **identity rule**: preserve the exact dependency key supplied by the plugin; never infer or
  transform it
- **temporal rule**: for snapshot-backed membership, the declared effective-date column is
  authoritative; a storage partition or request bucket never changes the snapshot's effective date
- **fulfillment**: ask the plugin-supplied callback to establish dependency coverage before reading;
  snapshot-backed callbacks include enough predecessor coverage to establish the initial as-of state

## Mock API

- **purpose**: develop and test operations without calling a real provider
- **requires**: one row generator per dataset
- **interface**: given a parsed request, fabricate rows matching that dataset's schema and invariants
- **activation**: the reserved provider token `findata-mock` selects deterministic success;
  `findata-mock:fail=<api>@<call>` selects one deterministic terminal failure for E2E recovery tests
- **failure behavior**: support deterministic injection of provider failures, rate limiting, malformed responses, and publication-window-aware empty responses
- **sharing rule**: a provider-family harness may share envelope format and error simulation, but never dataset-specific row shapes

## Task logging bridge

- **purpose**: let normal plugin logging flow into persistent task logs without dataset-specific protocol code
- **requires**: an authenticated task communication channel
- **interface**: a logging handler that serializes sanitized records to the TaskRunner
- **failure behavior**: logging failure must not corrupt a database commit or expose credentials;
  protocol backpressure is bounded
