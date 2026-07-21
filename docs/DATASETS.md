# Dataset plugins

This file is the canonical catalog of dataset-plugin contracts. Architectural rules shared by every dataset live in [DESIGN.md](DESIGN.md); reusable plugin-side components live in [TOOLKITS.md](TOOLKITS.md).

Each dataset entry defines its provider, logical Arrow schema, keys, observation domain, plugin
settings, publication timing, missing-data policy, dependencies, operations and operand semantics,
storage, coverage, and status fields. Machine-readable schemas created during implementation must
express these contracts without changing them.

## Common conventions

- Logical dates are Arrow `date32[day]`, strings are `utf8`, and floating-point provider values are `float64`. Provider `YYYYMMDD` strings are normalized before validation and never exposed as the logical date type.
- A non-null primary-key field is required on every row, and primary-key tuples must be unique within a publication. A missing declared provider field is an error; undeclared extra provider fields are ignored until intentionally added by a data-layout version.
- Operation `timerange` values are nonempty half-open `[start, end)` civil-date ranges. The CLI spelling is `YYYY-MM-DD:YYYY-MM-DD`; `today` is resolved once, in the dataset timezone, to the current date used as an exclusive endpoint. Inclusive provider endpoints are an adapter detail.
- `symbols`, `indexes`, and `exchanges` are nonempty arrays of strings, deduplicated after canonicalization. A single CLI scalar is coerced to a one-element array.
- `update` is always parameterless. Its dataset plugin alone interprets any settings needed to
  select work. A one-time `complete` or `refresh` never changes plugin settings.
- Built-in `complete` and `refresh` operations declare their fully normalized operands as a stable coalescing identity. `update` never coalesces because its target depends on submission time, publication state, and the plugin-settings revision.

## `tushare_trade_cal`

API: <https://tushare.pro/document/2?doc_id=26>

The SSE and SZSE exchange calendars. Its observation domain is every civil date, including closed days.

| field | Arrow type | nullable | meaning |
| --- | --- | --- | --- |
| `exchange` | `utf8` | no | `SSE` or `SZSE` |
| `cal_date` | `date32[day]` | no | calendar date |
| `is_open` | `bool` | no | whether the exchange is open |
| `pretrade_date` | `date32[day]` | yes | preceding open date |

- **provider**: `tushare`
- **capabilities**: `time-accumulating`
- **keys**: primary key `(exchange, cal_date)`; partition key `exchange`; time field `cal_date`; no file partitioning
- **settings**: none; `update` intrinsically maintains `SSE` and `SZSE`
- **publication timing**: the provider publishes ahead, but v1 resolves only dates through today; future dates are before-window
- **suggested schedule**: cron `0 9 * * 1`, `Asia/Shanghai`
- **missing-data policy**: `strict`; an empty due response is a failure
- **request plan**: one provider request per exchange
- **dependencies**: none
- **dependency fulfillment**: `{exchanges, timerange}` requires both fields and maps missing due intervals to `complete(exchanges=..., timerange=...)`; future intervals are rejected
- **operations**:
  - `update()` — extend both exchanges through the half-open endpoint tomorrow
  - `complete(exchanges, timerange)` — fetch the requested historical civil-date range; only `SSE` and `SZSE` are valid exchanges
- **toolkit**: `single-file-append`, coverage tracker, mock API
- **storage**: one CSV, logically append-only and published through the storage contract
- **coverage**: one continuous civil-date interval per exchange; closed dates are covered rows with `is_open=false`
- **status fields**: resolved time range and number of exchanges

## `tushare_stock_basic`

API: <https://tushare.pro/document/2?doc_id=25>

The complete A-share security-list snapshot across provider statuses `L`, `D`, `P`, and `G`.

| field | Arrow type | nullable | field | Arrow type | nullable |
| --- | --- | --- | --- | --- | --- |
| `ts_code` | `utf8` | no | `symbol` | `utf8` | no |
| `name` | `utf8` | no | `area` | `utf8` | yes |
| `industry` | `utf8` | yes | `fullname` | `utf8` | yes |
| `enname` | `utf8` | yes | `cnspell` | `utf8` | yes |
| `market` | `utf8` | no | `exchange` | `utf8` | no |
| `curr_type` | `utf8` | yes | `list_status` | `utf8` | no |
| `list_date` | `date32[day]` | yes | `delist_date` | `date32[day]` | yes |
| `is_hs` | `utf8` | yes | `act_name` | `utf8` | yes |
| `act_ent_type` | `utf8` | yes |  |  |  |

- **provider**: `tushare`
- **keys**: primary key `ts_code`; no partition or time field
- **settings**: none; every `update` fetches the complete snapshot
- **publication timing**: no publication window; the provider maintains the snapshot
- **suggested schedule**: cron `0 8 * * 1`, `Asia/Shanghai`
- **missing-data policy**: `strict`; the merged snapshot is never legitimately empty
- **request plan**: request each of `L`, `D`, `P`, and `G` separately for `SSE`, `SZSE`, and `BSE`, merge by `ts_code`, and fail on conflicting duplicates or any response reaching the provider's 6000-row limit because completeness would be uncertain
- **dependencies**: none
- **operations**:
  - `update()` — fetch all four statuses and replace the published snapshot
- **toolkit**: `single-file-replace`, mock API
- **storage**: one CSV replaced on every publication
- **status fields**: number of symbols grouped by `list_status`

## `tushare_index_basic`

API: <https://tushare.pro/document/2?doc_id=94>

The synchronized Tushare index-reference catalog used for discovery and validation. It preserves
the exact `ts_code` values returned separately for every market declared by the adapter.

| field | Arrow type | nullable | meaning |
| --- | --- | --- | --- |
| `ts_code` | `utf8` | no | exact Tushare provider ID |
| `symbol` | `utf8` | no | provider-local index symbol |
| `name` | `utf8` | no | provider short name |
| `fullname` | `utf8` | yes | provider full name |
| `market` | `utf8` | no | Tushare market or service-provider category |
| `publisher` | `utf8` | yes | reported publisher |
| `category` | `utf8` | yes | reported index category |
| `list_date` | `date32[day]` | yes | reported publication date |
| `exp_date` | `date32[day]` | yes | reported termination date |

- **provider**: `tushare`
- **keys**: primary key `ts_code`; no partition or time field
- **settings**: none; `update` requests every market declared by the adapter and rejects conflicting
  duplicate `ts_code` records
- **missing-data policy**: `strict`; an empty complete catalog is a failure
- **dependencies**: none
- **operations**: `update()` replaces the catalog snapshot
- **toolkit**: `single-file-replace`, mock API
- **storage**: one snapshot published through the storage contract
- **status fields**: synchronization time and counts by market

Catalog presence establishes only that Tushare returned the reference. It does not promise
`index_weight` coverage, historical depth, or account permission. Synchronization is the ordinary
`task run tushare_index_basic update` dataset operation; core findata has no index-specific command.

The built-in Tushare plugins spell an index reference as `tushare:<ts_code>`, for example
`tushare:000300.SH`. This is plugin syntax, not a core findata identifier. The prefix distinguishes
an index selector from a direct security code, while the remainder is copied byte-for-byte from
`tushare_index_basic`; the plugins define no aliases and perform no exchange or suffix mapping.
An unknown reference is rejected locally. A consuming plugin may additionally define `@YYYYMM`,
`@latest`, or bare range-union selection, but suffixes are not part of the catalog identity.

## `tushare_index_weight`

API: <https://tushare.pro/document/2?doc_id=96>

Monthly index constituent membership and weight. The plugin normalizes each provider response to one effective snapshot per index and calendar month; if a response contains multiple `trade_date` values in a month, the latest date is authoritative.

| field | Arrow type | nullable | meaning |
| --- | --- | --- | --- |
| `index_code` | `utf8` | no | exact Tushare index code returned by `index_weight` |
| `effective_month` | `date32[day]` | no | first civil date of the represented month |
| `con_code` | `utf8` | no | constituent Tushare security code |
| `trade_date` | `date32[day]` | no | provider snapshot date retained for provenance |
| `weight` | `float64` | no | constituent weight in percent |

- **provider**: `tushare`
- **capabilities**: `time-accumulating`
- **keys**: primary key `(index_code, effective_month, con_code)`; partition key `index_code`; secondary key `con_code`; time field `effective_month`; partitioned by index and month
- **observation domain**: every calendar month, represented by `[month_start, next_month_start)`
- **settings**:
  - `dataset.tushare_index_weight.update_indexes`: required nonempty array for `update`; the plugin
    accepts unsuffixed, catalog-validated `tushare:<ts_code>` references, preserves the exact
    Tushare code, and owns all parsing and validation
- **publication timing**: future months are before-window; the current month is inside-window, so an empty response remains unresolved; earlier months are after-window
- **suggested schedule**: cron `0 18 * * 1`, `Asia/Shanghai`
- **missing-data policy**: `strict`; an empty historical month is a failure
- **request plan**: one provider request per index and month using that month's inclusive provider endpoints
- **dependencies**: `tushare_index_basic` for provider-reference validation
- **dependency fulfillment**: `{indexes, timerange}` requires both fields, resolves each qualified
  reference to its exact catalog `ts_code`, expands the half-open range to intersecting calendar
  months, and maps missing months to `complete(indexes=..., timerange=...)`
- **operations**:
  - `update()` — extend every configured `update_indexes` entry from its coverage end through the
    current month; a newly selected index begins at the current month, and a missing or empty setting
    is rejected
  - `complete(indexes, timerange)` — fetch every intersecting historical or current calendar month
    for the requested unsuffixed `tushare:<ts_code>` references while preserving continuous monthly
    coverage
- **toolkit**: `partitioned`, coverage tracker, publication-window pruning, mock API
- **storage**: `:index/:month.parquet`
- **coverage**: one continuous month-aligned interval per index; successful current-month data resolves the entire represented month
- **status fields**: resolved month range and constituent count per index

The constituent-set resolver queries `effective_month`. `<reference>@YYYYMM` requires and selects
that month. `<reference>@latest` requires the month containing the consuming operation's latest due
date and selects it; it never silently substitutes an older covered month. A bare reference
requires every month intersecting the consuming operation's range and returns their constituent
union. An inside-window month that remains unavailable after dependency fulfillment therefore
produces an explicit coverage failure rather than stale membership.

## `tushare_daily_basic`

API: <https://tushare.pro/document/2?doc_id=32>

Per-symbol daily valuation, share, and market-value indicators.

| field | Arrow type | nullable | field | Arrow type | nullable |
| --- | --- | --- | --- | --- | --- |
| `ts_code` | `utf8` | no | `trade_date` | `date32[day]` | no |
| `close` | `float64` | yes | `turnover_rate` | `float64` | yes |
| `turnover_rate_f` | `float64` | yes | `volume_ratio` | `float64` | yes |
| `pe` | `float64` | yes | `pe_ttm` | `float64` | yes |
| `pb` | `float64` | yes | `ps` | `float64` | yes |
| `ps_ttm` | `float64` | yes | `dv_ratio` | `float64` | yes |
| `dv_ttm` | `float64` | yes | `total_share` | `float64` | yes |
| `float_share` | `float64` | yes | `free_share` | `float64` | yes |
| `total_mv` | `float64` | yes | `circ_mv` | `float64` | yes |
| `limit_status` | `int8` | yes |  |  |  |

- **provider**: `tushare`
- **capabilities**: `symbol_set_cap: 1`, `row_limit: 6000`, `time-accumulating`
- **keys**: primary key `(ts_code, trade_date)`; partition key `ts_code`; time field `trade_date`; partitioned by symbol and month
- **observation domain**: SSE/SZSE open trading dates from `tushare_trade_cal`; a suspension can resolve without a row
- **settings**:
  - `dataset.tushare_daily_basic.update_symbols`: required nonempty array for `update`; the plugin
    accepts direct Tushare security codes or its own `tushare:<ts_code>[@<selection>]`
    constituent-set selector syntax
  - `tushare:000300.SH@latest` resolves the membership month containing each update's latest due
    trading date and requires that month to be covered
  - the setting is unrelated to explicit `complete` operands and changing it never removes
    previously published symbols
- **publication timing**: on a trading date, before 15:00 is before-window, 15:00–17:00 is inside-window, and after 17:00 is after-window, all in `Asia/Shanghai`; historical trading dates are after-window
- **suggested schedule**: cron `40 17 * * 1-5`, `Asia/Shanghai`
- **missing-data policy**: `accept-empty`; a due empty result, including suspension, resolves that symbol-date
- **request plan**: use the request optimizer; v1 fetches one symbol over a bounded date range and does not use the provider's full-market form because no safe market-row bound is declared
- **dependencies**:
  - `tushare_trade_cal` with requirement `{exchanges: ["SSE", "SZSE"], timerange}` for trading-day pruning
  - `tushare_index_weight` with requirement `{indexes, timerange}` for symbolic constituent selectors
- **operations**:
  - `update()` — resolve the configured `update_symbols` for the latest due trading date and extend those symbols through the next civil-date endpoint; a newly selected symbol begins at that latest due date
  - `complete(symbols, timerange)` — backfill or extend the requested canonical symbols and constituent selectors; a disjoint range is extended toward existing coverage until the intervals abut
  - `refresh(symbols, timerange)` — re-fetch the explicit symbols strictly inside their existing resolved coverage; both operands are required and symbolic selectors use the same range-based semantics as `complete`
- **backfill visibility**: `complete` logs requested versus fetched ranges and warns per symbol when continuity causes the fetched span to exceed twice the requested span
- **toolkit**: `partitioned`, coverage tracker, publication-window pruning, request optimizer, constituent-set resolver, mock API
- **storage**: `:symbol/:month.parquet`
- **coverage**: one continuous civil-date interval per symbol in which every open trading date is resolved; closed dates do not create gaps, and resolved-empty trading dates need no data row
- **status fields**: resolved time range per symbol and number of symbols
