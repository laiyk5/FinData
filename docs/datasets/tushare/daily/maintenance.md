# tushare_daily Maintenance SOP

Prerequisite: [`../../__shared__/_general_maintenance_sop.md`](../../__shared__/_general_maintenance_sop.md)

## Source

- **Provider**: Tushare Pro
- **API**: `daily` (docs: https://tushare.pro/document/2?doc_id=27)
- **Grain**: one security × one trading date
- **Primary key**: `ts_code, trade_date`
- **Data**: unadjusted daily OHLCV bars
- **Storage**: full published dataset in Apache Parquet files under `current/trade_month=YYYYMM/`

## Smoke Test

### Fake Provider

```bash
python -m maintool maintain-run tushare_daily \
  --fake \
  --symbols 000001.SZ,600000.SH \
  --trade-date 20240506
```

### Real Provider

```bash
python -m maintool maintain-plan tushare_daily \
  --symbols 600000.SH,600519.SH \
  --trade-date 20240513 \
  --run-id tushare-daily-smoke-YYYYMMDD

python -m maintool prepare tushare_daily --run-id <run_id>
python -m maintool ingest tushare_daily --run-id <run_id>
python -m maintool qa tushare_daily --run-id <run_id>
python -m maintool review tushare_daily --run-id <run_id>
```

## Historical Backfill

For all-market daily bars, request one full-market slice per trading day:

```bash
python -m maintool maintain-plan tushare_daily \
  --all-market \
  --daily-request-strategy trade_date_all \
  --start-date 20250614 --end-date 20260614 \
  --run-id tushare-daily-all-market-YYYYMMDD
```

Use universe selectors when a constituent universe, rather than all market symbols, is desired:

```bash
python -m maintool maintain-plan tushare_daily \
  --symbols '@universe:index:CSI300' \
  --start-date 20160523 --end-date 20260522 \
  --daily-request-strategy auto \
  --run-id tushare-daily-csi300-10y-YYYYMMDD
```

Then run `prepare`, `ingest`, `qa`, `review`, `publish` with the same `run_id`.

## Request Strategy

The `auto` strategy (default) chooses between `symbol_range` (batch symbols/date-chunks) and `trade_date_all` (all symbols for each date) based on which produces fewer requests while respecting the 6000-row limit per request. Override with `--daily-request-strategy symbol_range` or `--daily-request-strategy trade_date_all`. For `--all-market`, use `trade_date_all`; the planner emits one request per open trading date.

## QA Expectations

**Blocks publish** unless:
- All required fields present
- Primary keys unique (within and across files)
- `trade_date` is valid `YYYYMMDD`
- `high >= max(open, close, low)`, `low <= min(open, close, high)`
- `vol` and `amount` are non-negative
- Missing rows are classified (`market_holiday`, `suspension`, `outside_scope`) or explicitly accepted

**Warnings** (do not block):
- Zero volume on a claimed trading day

## Missingness

Expected causes:
- **market_holiday**: trade_calendar says exchange was closed
- **suspension**: calendar says open but no row appears within symbol's observed history bounds
- **outside_scope**: date is before/after symbol's observed history range

Unknown missingness blocks publish. Use accepted_missingness records for documented gaps.

## Publish Criteria

- QA passes (validation + missingness classification)
- Row counts are plausible
- No provider tokens or secrets in manifests/logs
- Generated artifacts not staged in git
