# tushare_adj_factor Maintenance SOP

Prerequisite: [`../../__shared__/_general_maintenance_sop.md`](../../__shared__/_general_maintenance_sop.md)

## Source

- **Provider**: Tushare Pro
- **API**: `adj_factor` (docs: https://tushare.pro/document/2?doc_id=28)
- **Grain**: one security × one trading date
- **Primary key**: `ts_code`, `trade_date`
- **Data**: daily adjustment factor (复权因子) — uses the dedicated `adj_factor` endpoint, storing `ts_code`, `trade_date`, `adj_factor`
- **Storage**: full published dataset in Apache Parquet files under `current/trade_month=YYYYMM/`

## Smoke Test

### Fake Provider

```bash
python -m maintool maintain-run tushare_adj_factor \
  --fake \
  --symbols 000001.SZ \
  --trade-date 20240506
```

### Real Provider

```bash
python -m maintool maintain-plan tushare_adj_factor \
  --symbols 000001.SZ \
  --trade-date 20240506 \
  --run-id adj-factor-smoke-YYYYMMDD

python -m maintool prepare tushare_adj_factor --run-id <run_id>
python -m maintool ingest tushare_adj_factor --run-id <run_id>
python -m maintool qa tushare_adj_factor --run-id <run_id>
```

## Historical Backfill

For all-market adjustment factors, request one full-market slice per trading day:

```bash
python -m maintool maintain-plan tushare_adj_factor \
  --all-market \
  --daily-request-strategy trade_date_all \
  --start-date 20250614 --end-date 20260614 \
  --run-id tushare-adj-factor-all-market-YYYYMMDD
```

Use `@universe:` selectors to cover historical index constituents:

```bash
python -m maintool maintain-run tushare_adj_factor \
  --symbols '@universe:index:CSI300' \
  --start-date 20160530 --end-date 20260530 \
  --daily-request-strategy symbol_range
```

The `symbol_range` strategy sends one request per symbol over the full date range, producing the smallest number of requests for known symbol lists.

## Request Strategy

Uses the same request scheduler as `stk_factor_pro` with a 10000-row per-request limit. The API call goes through the dedicated `adj_factor` endpoint. For `symbol_range`, each symbol gets one request covering the full date window. For `auto`, the scheduler may batch symbols into date chunks. For `--all-market`, use `trade_date_all`; the planner emits one request per open trading date.

## QA Expectations

**Blocks publish** unless:
- Required fields present (`ts_code`, `trade_date`, `adj_factor`)
- Primary keys unique
- `trade_date` is valid `YYYYMMDD`
- `adj_factor` parses as a positive decimal

## Publish Criteria

- QA passes
- adj_factor values are positive decimals
- Generated artifacts not staged in git
