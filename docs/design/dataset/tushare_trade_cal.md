# `tushare_trade_cal`

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
- **toolkit**: checkpoint-batch planner, coverage tracker, mock API
- **storage mutation**: exchange/time-range replacement in the dataset's DuckDB `data` table;
  matching coverage commits in the same transaction
- **coverage**: one continuous civil-date interval per exchange; closed dates are covered rows with `is_open=false`
- **status fields**: resolved time range and number of exchanges
