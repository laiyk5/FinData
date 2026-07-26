# `findata/tushare/daily_basic`

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
- **keys**: primary key `(ts_code, trade_date)`; partition key `ts_code`; time field `trade_date`
- **observation domain**: SSE/SZSE open trading dates from `findata/tushare/trade_cal`; a suspension can resolve without a row
- **settings**:
  - `dataset.findata/tushare/daily_basic.update_symbols`: required nonempty array for `update`; the plugin
    accepts direct Tushare security codes or its own `tushare:<ts_code>[@<selection>]`
    constituent-set selector syntax
  - `tushare:000300.SH@latest` resolves the membership month containing each update's latest due
    trading date and requires that month to be covered
  - the setting is unrelated to explicit `complete` operands and changing it never removes
    previously published symbols
- **publication timing**: on a trading date, before 15:00 is before-window, 15:00–17:00 is inside-window, and after 17:00 is after-window, all in `Asia/Shanghai`; historical trading dates are after-window
- **suggested schedule**: cron `40 17 * * 1-5`, `Asia/Shanghai`
- **missing-data policy**: `accept-empty`; a due empty result, including suspension, resolves that symbol-date
- **request plan**: use the request optimizer; the operation chooses per-symbol bounded-range requests or full-market per-date requests, whichever needs fewer provider calls, filters each full-market response to the requested symbols before commit, falls back to per-symbol requests for any date whose full-market response reaches the declared 6000-row limit, and resolves empty per-date results under the accept-empty policy and publication-window rules — see [request optimizer](../toolkit/request_optimizer.md)
- **dependencies**:
  - `findata/tushare/trade_cal` with requirement `{exchanges: ["SSE", "SZSE"], timerange}` for trading-day pruning
  - `findata/tushare/index_basic` with requirement `{indexes}` for local selector validation and exact
    reference metadata
  - `findata/tushare/index_weight` with requirement `{indexes, timerange}` for symbolic constituent selectors
- **operations**:
  - `update()` — resolve the configured `update_symbols` for the latest due trading date and extend those symbols through the next civil-date endpoint; a newly selected symbol begins at that latest due date
  - `complete(symbols, timerange)` — backfill or extend the requested canonical symbols and constituent selectors; a disjoint range is extended toward existing coverage until the intervals abut
  - `refresh(symbols, timerange)` — re-fetch the explicit symbols strictly inside their existing resolved coverage; both operands are required and symbolic selectors use the same range-based semantics as `complete`
- **backfill visibility**: `complete` logs requested versus fetched ranges and warns per symbol when continuity causes the fetched span to exceed twice the requested span
- **toolkit**: checkpoint-batch planner, coverage tracker, publication-window pruning, request optimizer, constituent-set resolver, mock API
- **storage mutation**: symbol/time-range replacement in DuckDB; data and coverage commit together
- **coverage**: one continuous civil-date interval per symbol in which every open trading date is resolved; closed dates do not create gaps, and resolved-empty trading dates need no data row
- **status fields**: resolved time range per symbol and number of symbols
