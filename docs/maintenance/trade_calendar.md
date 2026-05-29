# trade_calendar Maintenance SOP

Prerequisite: [`_general_maintenance_sop.md`](_general_maintenance_sop.md)

## Source

- **Provider**: Tushare Pro
- **API**: `trade_cal` (docs: https://tushare.pro/document/2?doc_id=26)
- **Grain**: one exchange × one calendar date
- **Primary key**: `exchange, cal_date`
- **Data**: exchange trading calendar (`SSE`, `SZSE`) with `is_open` and `pretrade_date`

This is a **provider-independent schema** — the canonical dataset is named after the concept, not the provider.

## Smoke Test

### Fake Provider

```bash
python -m maintool --repo-root . maintain-run trade_calendar \
  --provider fake \
  --trade-date 20240506
```

### Real Provider

Use a short known week first:

```bash
python -m maintool --repo-root . maintain-plan trade_calendar \
  --provider tushare --enable-real-api \
  --exchange SSE \
  --start-date 20240513 --end-date 20240517 \
  --run-id trade-calendar-sse-smoke-YYYYMMDD

python -m maintool --repo-root . prepare trade_calendar --run-id <run_id>
python -m maintool --repo-root . ingest trade_calendar --run-id <run_id>
python -m maintool --repo-root . qa trade_calendar --run-id <run_id>
```

Publish only after checking date continuity and `pretrade_date` behavior.

## Full Maintenance

Maintain exchanges separately for easier review:

```bash
# SSE full history — use exchange listing date (1990-12-19) as start
python -m maintool --repo-root . maintain-plan trade_calendar \
  --provider tushare --enable-real-api \
  --exchange SSE \
  --start-date 19901219 --end-date YYYYMMDD \
  --run-id trade-calendar-SSE-full-YYYYMMDD

# SZSE full history — use exchange listing date (1991-07-03) as start
python -m maintool --repo-root . maintain-plan trade_calendar \
  --provider tushare --enable-real-api \
  --exchange SZSE \
  --start-date 19910703 --end-date YYYYMMDD \
  --run-id trade-calendar-SZSE-full-YYYYMMDD
```

Use exchange-specific start dates rather than synthetic earlier dates that create structural missingness. The `--is-open` filter is optional — omit it to get all calendar dates.

## Request Planning

Plans one request per calendar year (chunked by year boundaries). Each request covers `[exchange, start_date, end_date]`.

## QA Expectations

**Blocks publish** unless:
- Required fields present (`exchange`, `cal_date`, `is_open`, `pretrade_date`)
- Primary keys unique
- `cal_date` is valid `YYYYMMDD`
- `pretrade_date` is empty or valid `YYYYMMDD`
- `is_open` is only `0` or `1`
- Every requested calendar date is present (no date gaps)
- Open-day `pretrade_date` points to a previous open day when present

## Missingness

Unlike price data, missing calendar dates are **structural errors** — every date in the requested range must have a row. Unknown missingness blocks publish.

## Publish Criteria

- All requested dates present
- Primary keys unique
- `pretrade_date` chain is valid (each open day's pretrade_date references an existing open day)
- Generated artifacts not staged in git
