# tushare_daily_basic Maintenance SOP

Prerequisite: [`_general_maintenance_sop.md`](_general_maintenance_sop.md)

## Source

- **Provider**: Tushare Pro
- **API**: `daily_basic` (docs: https://tushare.pro/document/2?doc_id=32)
- **Grain**: one security × one trading date
- **Primary key**: `ts_code, trade_date`
- **Data**: daily valuation (PE/PB/PS), share capital, turnover, market cap indicators

## Smoke Test

### Fake Provider

```bash
python -m maintool --repo-root . maintain-run tushare_daily_basic \
  --provider fake \
  --symbols 000001.SZ,600000.SH \
  --trade-date 20240506
```

### Real Provider

```bash
python -m maintool --repo-root . maintain-plan tushare_daily_basic \
  --provider tushare --enable-real-api \
  --symbols 600000.SH,600519.SH \
  --trade-date 20240513 \
  --request-budget 1 \
  --run-id tushare-daily-basic-smoke-YYYYMMDD

python -m maintool --repo-root . prepare tushare_daily_basic --run-id <run_id>
python -m maintool --repo-root . ingest tushare_daily_basic --run-id <run_id>
python -m maintool --repo-root . qa tushare_daily_basic --run-id <run_id>
```

## Historical Backfill

```bash
python -m maintool --repo-root . maintain-plan tushare_daily_basic \
  --provider tushare --enable-real-api \
  --symbols '@universe:index:CSI300' \
  --start-date 20160526 --end-date 20260525 \
  --daily-request-strategy auto \
  --run-id tushare-daily-basic-csi300-10y-YYYYMMDD
```

Then run `prepare`, `ingest`, `qa`, `review`, `publish` with the same `run_id`.

## Request Strategy

Reuses the daily request scheduler from `tushare_daily` with 6000-row limit. Default `auto` strategy is recommended.

## QA Expectations

**Blocks publish** unless:
- All required fields present
- Primary keys unique
- `trade_date` is valid `YYYYMMDD`
- Non-valuation numeric fields parse as decimals
- Share and market-value fields are non-negative
- `float_share <= total_share`
- `free_share <= float_share`
- Missing rows are classified or accepted

**Nullable fields**: `pe`, `pe_ttm`, `pb`, `ps_ttm`, `dv_ratio`, `dv_ttm`, `volume_ratio`, `free_share` — blank is valid (e.g., PE for loss-making companies).

## Missingness

Expected causes:
- **market_holiday**: exchange closed per trade_calendar
- **suspension**: no data for a symbol on an open trading day within its observed range
- **outside_scope**: date before/after symbol's observed history

The missingness report uses prepared raw primary keys as the expected set (from raw files), rather than manifest-based expectations. This avoids false positive missingness from provider-side unavailable rows.

## Publish Criteria

- QA passes
- Share count sanity (`float_share <= total_share`, `free_share <= float_share`)
- Missingness classified or accepted
- Generated artifacts not staged in git
