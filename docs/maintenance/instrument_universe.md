# instrument_universe Maintenance SOP

Prerequisite: [`_general_maintenance_sop.md`](_general_maintenance_sop.md)

## Source

- **Provider**: Tushare Pro
- **Primary API**: `index_weight` (for member lists)
- **Discovery API**: `index_basic` (for verifying index codes)
- **Grain**: one member × one universe × one snapshot date
- **Primary key**: `universe_id, member_code, as_of_date`
- **Data**: named instrument sets that downstream datasets reference via `@universe:<id>` selectors

## Provider Discovery

Index universe maintenance requires two steps:

1. **Verify the index code** with `index_basic` (search by name, check `ts_code`, `market`, `publisher`)
2. **Retrieve members** with `index_weight` (pass confirmed `ts_code` as `source_id`)

Never assume a provider-native index code without verification.

### Known Mappings

| universe_id | source_id | index name |
|---|---|---|
| `index:SSE50` | `000016.SH` | SSE 50 |
| `index:CSI300` | `000300.SH` | CSI 300 |
| `index:CSI500` | `000905.SH` | CSI 500 |

## Smoke Test

### Fake Provider

```bash
python -m maintool --repo-root . maintain-run instrument_universe \
  --provider fake \
  --trade-date 20240506
```

### Real Provider

```bash
python -m maintool --repo-root . maintain-plan instrument_universe \
  --provider tushare --enable-real-api \
  --universe-id index:CSI300 \
  --source-id 000300.SH \
  --start-date 20260401 --end-date 20260506 \
  --run-id instrument-universe-csi300-smoke-YYYYMMDD

python -m maintool --repo-root . prepare instrument_universe --run-id <run_id>
python -m maintool --repo-root . ingest instrument_universe --run-id <run_id>
python -m maintool --repo-root . qa instrument_universe --run-id <run_id>
```

Publish only after checking that the latest member count is plausible.

## Full Maintenance

Each universe is a single request (one date range, one `source_id`):

```bash
python -m maintool --repo-root . maintain-plan instrument_universe \
  --provider tushare --enable-real-api \
  --universe-id index:SSE50 \
  --source-id 000016.SH \
  --start-date 20260401 --end-date YYYYMMDD \
  --run-id instrument-universe-sse50-YYYYMMDD
```

## QA Expectations

**Blocks publish** unless:
- `universe_id`, `member_code`, `as_of_date` are present
- Primary keys unique
- Date fields (`valid_from`, `valid_to`, `as_of_date`) are valid `YYYYMMDD` when present
- `member_code` matches pattern: digits, dot, exchange suffix (`SH`/`SZ`/`BJ`)
- `weight` parses as non-negative decimal when present
- `rank` is a digit string when present
- Published current is not empty (zero members blocks publish)

## Missingness

No automatic missingness classification. If `index_weight` returns no rows:
1. Verify the `source_id` is correct using `index_basic`
2. Widen the date window
3. Do **not** synthesize members

## Maintenance Notes

- Preserve both `universe_id` and provider-native `source_id` in run manifests
- Downstream datasets should use `@universe:index:CSI300` selectors, not hand-coded symbol lists
- The selector resolution is recorded in the downstream run manifest (symbols frozen at resolution time)
- Tushare is the maintained provider path, not the official exchange publication

## Publish Criteria

- Universe has at least one member
- Primary keys unique
- `as_of_date` is valid
- Weights are non-negative decimals
- Generated artifacts not staged in git
