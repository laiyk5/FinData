# `findata/tushare/index_weight`

API: <https://tushare.pro/document/2?doc_id=96>

Monthly index constituent membership and weight snapshots. `trade_date` is the authoritative
effective date. The plugin retains the latest provider snapshot returned within each requested
calendar month; a month with no row means that no new snapshot superseded the preceding one.

| field | Arrow type | nullable | meaning |
| --- | --- | --- | --- |
| `index_code` | `utf8` | no | exact Tushare index code returned by `index_weight` |
| `effective_month` | `date32[day]` | no | calendar month containing `trade_date`; it is not the effective date |
| `con_code` | `utf8` | no | constituent Tushare security code |
| `trade_date` | `date32[day]` | no | provider snapshot date retained for provenance |
| `weight` | `float64` | no | constituent weight in percent |

- **provider**: `tushare`
- **capabilities**: `time-accumulating`
- **keys**: primary key `(index_code, effective_month, con_code)`; partition key `index_code`; secondary key `con_code`; time field `effective_month`
- **observation domain**: dated provider snapshots; monthly intervals are request and logical query
  buckets, not assertions that every month must contain a new snapshot
- **settings**:
  - `dataset.findata/tushare/index_weight.update_indexes`: required nonempty array for `update`; the plugin
    accepts unsuffixed, metadata-validated `tushare:<ts_code>` references, preserves the exact
    Tushare code, and owns all parsing and validation
- **publication timing**: future months are before-window; the current month is mutable and is
  re-fetched whenever an operation needs it; earlier queried months are final
- **suggested schedule**: cron `0 18 * * 1`, `Asia/Shanghai`
- **missing-data policy**: `accept-empty`; an empty due month records that no new snapshot occurred
  and carries the preceding snapshot forward semantically without copying its rows
- **request plan**: one exact-code request per uncovered index/month using that month's inclusive
  provider endpoints; always re-fetch an intersecting current month; constituent fulfillment also
  fetches the preceding month needed to establish the initial as-of state
- **dependencies**: `findata/tushare/index_basic` for provider-reference validation
- **dependency fulfillment**: `{indexes, timerange}` requires both fields, resolves each qualified
  reference to its exact materialized `ts_code`, expands the half-open range to intersecting calendar
  months plus one predecessor month, and maps missing query coverage to `complete`
- **operations**:
  - `update()` — extend every configured `update_indexes` entry through the current month and
    re-fetch that mutable month; a missing or empty setting is rejected
  - `complete(indexes, timerange)` — fetch every intersecting historical or current calendar month
    for the requested unsuffixed `tushare:<ts_code>` references while preserving continuous monthly
    coverage
- **toolkit**: checkpoint-batch planner, coverage tracker, publication-window pruning, mock API
- **storage mutation**: index/month replacement in DuckDB; a nonempty refresh replaces the complete
  logical month so removed constituents do not survive, while an empty month changes coverage only
- **coverage**: one continuous month-aligned provider-query interval per index; it records whether
  snapshot events were queried, independently of whether rows existed; current-month coverage is
  deliberately refreshable
- **status fields**: resolved month range and constituent count per index

No operation infers an index from `findata/tushare/index_basic` metadata. `complete` and dependency
fulfillment process only their explicit references and never add them to `update_indexes`; only a
user configuration mutation changes the maintained set.

The constituent-set resolver uses `trade_date`, never `effective_month`, for point-in-time meaning.
`<reference>@YYYYMM` selects the latest snapshot effective by that month-end. `<reference>@latest`
selects the latest snapshot effective on the consuming operation's latest due date. A bare reference
returns the union of the latest snapshot effective at the range start and every later snapshot whose
`trade_date` falls inside the half-open range. This prevents both stale-month failures and
look-ahead bias.
