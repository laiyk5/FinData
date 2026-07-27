# Request optimizer

- **purpose**: minimize provider calls when request rate is the bottleneck by merging
  per-symbol, time-ranged demands into as few legal provider requests as possible
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

## Problem model

After parsing and pruning, the demand is a set of per-symbol date intervals
`S = {(u_i, l_i, r_i)}` with half-open `[l_i, r_i)` civil-date ranges. A provider request is a
**rectangle**: a symbol set `G` times one continuous date interval `[a, b)`, returning the
Cartesian product with an estimated row count

```
rows(q) = |G| × date_count(a, b) ≤ row_limit
```

where `date_count` counts the dataset's due observations inside the interval (open trading
dates for a trading-day dataset, months for a monthly dataset), not calendar days. A request
covers a demand when `u ∈ G`, `a ≤ l`, and `b ≥ r`; covering extra symbol-date cells is allowed
when the operation filters them before commit. Merging demands is therefore a
**rectangle covering problem with a capacity constraint**, which is NP-hard in general; the
toolkit uses an exact formulation only for small inputs and a greedy approximation otherwise.

## Request families

The optimizer never invents request shapes. The consuming plugin declares the families its
provider actually supports, and every candidate rectangle belongs to one of them:

- **bounded symbol set × range** — `|G| ≤ symbol_set_cap`. When the cap is 1, every rectangle
  in this family is one symbol over one bounded date range.
- **full market × single date** — `full_market_fetch` with a declared conservative market-row
  bound `M ≤ row_limit`. Each rectangle is one due date covering every demanded symbol at once;
  the operation filters the market-wide response to the demanded symbols before commit. If a
  live response ever reaches `row_limit`, completeness is uncertain and the operation falls back
  for that date to bounded-symbol requests.

A provider that silently ignores multi-symbol parameters (returning an empty or partial
response) does not have a multi-symbol family; adapters must verify support empirically before
declaring one. Declaring a family that does not exist turns the optimizer's output into data
loss, so family declarations are provider contracts, not aspirations.

## Preprocessing

1. **Prune** not-due intervals (publication windows) and already-resolved intervals (coverage).
   Continuous coverage leaves at most two residual intervals per symbol.
2. **Slice** any symbol interval whose own `date_count` exceeds `row_limit` into date-aligned
   pieces that fit; slicing happens before grouping.
3. **Normalize** per symbol. Two modes:
   - *segmented* (default): keep distant intervals as separate segments so a large gap does not
     inflate the covering rectangle's area;
   - *full merge*: collapse each symbol to its single bounding interval, which is simpler but
     wastes capacity when gaps are large.

## Exact formulation (small inputs)

For `n ≤ 30` demand segments, every symbol subset `G` whose bounding interval satisfies
`rows(G) ≤ row_limit` is a legal candidate, and the minimum cover is a 0-1 integer program:
minimize `Σ x_c` over candidates subject to every segment being covered by at least one chosen
candidate. This is the reference for correctness and for measuring greedy quality; the toolkit
does not require a solver dependency, so production planning uses the greedy path below.

## Greedy grouping

**Hardest-first seeding, minimum-increment growth.** Repeat until no segment remains:

1. Seed a group with the remaining segment of longest `date_count` (hardest to place).
2. Grow it iteratively: among segments that keep the group legal, add the one with the best
   score, compared lexicographically as
   1. smallest row increment `rows(G ∪ {u}) − rows(G)`;
   2. smallest unused capacity `row_limit − rows(G ∪ {u})` (highest fill);
   3. largest date overlap with the group's current range;
   4. longest own span (harder to place later).
3. When nothing can be added legally, emit the group as one request and continue.

Time complexity is `O(n² · d)` for `n` segments and date-count cost `d`; date counts are
precomputed and cached per interval so `d` stays `O(1)`.

## Local search

Greedy output is improved by two cheap moves, applied until neither helps:

- **pair merge** — replace two requests by their union rectangle when it stays within capacity;
- **segment migration** — move a segment to another request when both stay legal and the source
  request empties out.

## Validation

Every plan is independently checked before any provider request: each rectangle satisfies the
capacity constraint, and each unpruned demand segment is covered by at least one rectangle. A
failed check is a planning error, never a partial fetch.

## Worked example: `findata-plugins/tushare_daily_basic`

Verified provider contract: `symbol_set_cap: 1` (multi-code queries return empty), `row_limit:
6000`, and a full-market `trade_date` form returning ~5,500 rows (a safe declared bound under
6,000, with the runtime fallback above). The two families are therefore `1 symbol × range` and
`market × 1 date`.

Backfilling 300 symbols over 2026-01-01 to 2026-07-24 (~135 due trading dates, ~42,000 demanded
cells) then prices as:

| shape | requests | note |
| --- | --- | --- |
| per-symbol ranges | 300 | ~135 rows per request, ~2% fill |
| full-market per date | ~135 | ~5,500 rows per request, ~92% fill |
| mixed | ≥ 135 | market dates plus one residual range per symbol never beat the pure shapes |

The information-theoretic floor of `⌈42,000 / 6,000⌉ = 7` requests is unreachable because the
provider has no multi-symbol family; ~135 requests is the realizable optimum and is what the
cost comparison selects. One-time dependency fulfillment (calendar, index metadata, weights)
adds a handful of requests on first run only.

## Example

For `findata-plugins/tushare_daily_basic`, the symbol cap is one and the row limit is 6000; calendar pruning
removes closed dates, and long per-symbol ranges split before their conservative row estimate
reaches the limit.
