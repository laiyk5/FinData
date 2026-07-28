# `findata-plugins/tushare_index_daily`

API: <https://tushare.pro/document/2?doc_id=95>

Daily OHLCV market data for one Tushare index code. The provider requires an index code and supports
one date or an inclusive date range; it does not include Shenwan industry-index data.

- **provider**: `tushare`
- **keys**: primary and partition key `ts_code` with `trade_date` as the time field
- **schema**: `ts_code`, `trade_date`, `close`, `open`, `high`, `low`, `pre_close`, `change`,
  `pct_chg`, `vol`, and `amount`; all market values are nullable `float64`
- **dependencies**: trade calendar for due-date semantics and index basic for exact index-code
  validation
- **settings**: `update_indexes` defaults to `stored`, the indexes already represented by committed
  coverage; an empty stored set makes `update` a successful no-op
- **operations**: `update()` extends stored indexes, `complete(indexes, timerange)` backfills an
  explicit range, and `refresh(indexes, timerange)` refetches only existing coverage
- **schedule**: weekdays at 17:40, `Asia/Shanghai`
- **storage mutation**: index/time-range replacement with continuous per-index coverage
