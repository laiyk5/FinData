# tushare_stk_factor_pro Maintenance SOP

Prerequisite: [`../../__shared__/_general_maintenance_sop.md`](../../__shared__/_general_maintenance_sop.md)

## Source

- **Provider**: Tushare Pro
- **API**: `stk_factor_pro` (docs: https://tushare.pro/document/2?doc_id=328)
- **Grain**: one security × one trading date
- **Primary key**: `ts_code, trade_date`
- **Data**: OHLCV + adjusted-price variants + valuation + share counts + adj_factor + technical factors (MACD, BOLL, DMI, KDJ, BIAS, ATR, MA, EMA)

The v1 contract keeps a curated subset of the full provider field set.

## Smoke Test

### Fake Provider

```bash
python -m maintool maintain-run tushare_stk_factor_pro \
  --fake \
  --symbols 000001.SZ \
  --trade-date 20240506 \
  --run-id stk-factor-smoke-fake
```

### Real Provider

```bash
python -m maintool maintain-plan tushare_stk_factor_pro \
  --symbols 000001.SZ \
  --trade-date 20240506 \
  --run-id stk-factor-smoke-real

python -m maintool prepare tushare_stk_factor_pro --run-id <run_id>
python -m maintool ingest tushare_stk_factor_pro --run-id <run_id>
python -m maintool qa tushare_stk_factor_pro --run-id <run_id>
```

## Historical Backfill

```bash
python -m maintool maintain-plan tushare_stk_factor_pro \
  --symbols '@universe:index:CSI300' \
  --start-date 20160527 --end-date 20260526 \
  --daily-request-strategy auto \
  --run-id stk-factor-csi300-10y-YYYYMMDD
```

## Request Strategy

Reuses the daily request scheduler from `tushare_daily`, but with a **10000-row** per-request limit (provider-documented). The `auto` strategy is recommended; `symbol_range` emits one range-request per symbol over the full date window.

## QA Expectations

**Blocks publish** unless:
- All required fields present
- Primary keys unique
- `trade_date` is valid `YYYYMMDD`
- OHLC values are internally consistent (`high >= max(open,close,low)`, `low <= min`)
- `vol` and `amount` are non-negative
- `float_share <= total_share`
- Missing rows are classified or accepted

**Warnings** (do not block):
- `free_share > float_share` (provider occasionally emits this)
- Usual tushare_daily warnings: pct_chg > 20%, zero volume, large close/pre_close moves, 10× volume spikes

**Nullable fields**: same valuation nullables as daily_basic, plus all adjusted-price variants, adj_factor, and technical factor fields — blank is valid when the provider doesn't return that factor.

## Missingness

Same classification model as `tushare_daily`:
- `market_holiday` — exchange closed
- `suspension` — open day, symbol in observed range but no row
- `outside_scope` — before/after symbol's observed range

## Publish Criteria

- QA passes
- OHLC consistency holds
- Missingness documented or accepted
- Generated artifacts not staged in git
