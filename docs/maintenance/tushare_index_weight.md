# tushare_index_weight Maintenance SOP

Prerequisite: [`_general_maintenance_sop.md`](_general_maintenance_sop.md)

## Source

- **Provider**: Tushare Pro
- **API**: `index_weight` (docs: https://tushare.pro/document/2?doc_id=96)
- **Grain**: one constituent × one index × one snapshot date
- **Primary key**: `index_code, con_code, trade_date`
- **Data**: raw provider index constituent weights (point-in-time monthly snapshots)

This dataset preserves raw provider columns. The normalized `instrument_universe` dataset is the downstream consumer-facing universe source.

## Smoke Test

### Fake Provider

```bash
python -m maintool --repo-root . maintain-run tushare_index_weight \
  --provider fake \
  --trade-date 20240506 \
  --index-code 000300.SH \
  --start-date 20240501 --end-date 20240531
```

### Real Provider

```bash
python -m maintool --repo-root . maintain-plan tushare_index_weight \
  --provider tushare --enable-real-api \
  --index-code 000300.SH \
  --start-date 20260401 --end-date 20260430 \
  --run-id tushare-index-weight-csi300-smoke-YYYYMMDD

python -m maintool --repo-root . prepare tushare_index_weight --run-id <run_id>
python -m maintool --repo-root . ingest tushare_index_weight --run-id <run_id>
python -m maintool --repo-root . qa tushare_index_weight --run-id <run_id>
python -m maintool --repo-root . review tushare_index_weight --run-id <run_id>
```

Publish only after checking that the member count and latest snapshot date are plausible.

## Full CSI300 Backfill

CSI300 launch month is April 2005:

```bash
python -m maintool --repo-root . maintain-plan tushare_index_weight \
  --provider tushare --enable-real-api \
  --index-code 000300.SH \
  --start-date 20050401 --end-date YYYYMMDD \
  --rate-limit-seconds 0.5 --max-retries 3 --retry-backoff-seconds 1.0 \
  --run-id tushare-index-weight-csi300-20050401-YYYYMMDD
```

## Request Planning

The maintool plans one request per calendar month between start and end dates. The provider documents monthly snapshots — do not request at finer granularity.

## QA Expectations

**Blocks publish** unless:
- Required fields present (`index_code`, `con_code`, `trade_date`, `weight`)
- Primary keys unique
- `index_code` and `con_code` use Tushare-style codes (e.g., `000300.SH`, `600000.SH`)
- `trade_date` is valid `YYYYMMDD`
- `weight` parses as a non-negative decimal
- Published current is not empty (zero rows blocks publish)

## Missingness

No automatic missingness classification is applied — missingness is structural when the provider simply returns no rows for a window. If `index_weight` returns no rows for a recent month, verify the index code with `index_basic` and widen the date window before retrying.

## Publish Criteria

- Published current has at least one row
- Primary keys unique
- Weights are non-negative decimals
- Generated artifacts not staged in git
