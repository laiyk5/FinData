# tushare_moneyflow Maintenance SOP

Prerequisite: [`../../__shared__/_general_maintenance_sop.md`](../../__shared__/_general_maintenance_sop.md)

## Source

- **Provider**: Tushare Pro
- **API**: `moneyflow` (docs: https://tushare.pro/document/2?doc_id=170)
- **Grain**: one security × one trading date
- **Primary key**: `ts_code, trade_date`
- **Data**: 沪深A股资金流向 — buy/sell volumes and amounts segmented by order size (small/medium/large/extra-large), plus net flow

Data begins 2010 per provider docs.

## Smoke Test

### Fake Provider

```bash
python -m maintool maintain-run tushare_moneyflow \
  --fake \
  --symbols 000001.SZ \
  --trade-date 20240506 \
  --run-id moneyflow-smoke-fake
```

### Real Provider

```bash
python -m maintool maintain-plan tushare_moneyflow \
  --symbols 000001.SZ \
  --trade-date 20240506 \
  --run-id moneyflow-smoke-real

python -m maintool prepare tushare_moneyflow --run-id <run_id>
python -m maintool ingest tushare_moneyflow --run-id <run_id>
python -m maintool qa tushare_moneyflow --run-id <run_id>
```

## Historical Backfill

```bash
python -m maintool maintain-plan tushare_moneyflow \
  --symbols '@universe:index:CSI300' \
  --start-date 20160527 --end-date 20260522 \
  --daily-request-strategy auto \
  --run-id moneyflow-csi300-10y-YYYYMMDD
```

## Request Strategy

Reuses the daily request scheduler from `tushare_daily` with 6000-row limit. Default `auto` strategy.

## QA Expectations

**Blocks publish** unless:
- All required fields present
- Primary keys unique
- `trade_date` is valid `YYYYMMDD`
- Numeric fields parse as decimals when present
- Buy/sell component fields are non-negative when present
- Missing rows are classified or accepted

**Important**: `net_mf_vol` and `net_mf_amount` are validated for type only — they are provider-native fields and should not be forced to equal a simple component sum. Provider-side blank numeric cells (especially in older history) are preserved as empty CSV cells and treated as nullable.

## Missingness

Same classification model as `tushare_daily`:
- `market_holiday` — exchange closed
- `suspension` — open day, symbol in observed range but no row
- `outside_scope` — before/after symbol's observed range

## Publish Criteria

- QA passes
- Buy/sell component fields non-negative
- Missingness documented or accepted
- Generated artifacts not staged in git
