# instrument_universe Maintenance

Follow `docs/dataset_maintenance_sop.md` first. This file records the
dataset-specific procedure for maintained instrument universes.

## Ownership Boundary

Tracked in git:

- `dataset_card.md`
- `schema.yaml`
- stable manifest metadata
- universe mapping notes
- this maintenance guide

Generated and not tracked:

- `data/published/current/`
- `data/archive/`
- `checks/*.json`
- `logs/*.json`
- `sandboxes/runs/instrument_universe/`

## Source And Scope

Provider: Tushare Pro

Primary API: `index_weight`

Discovery API: `index_basic`

Use logical universe ids in downstream datasets:

| universe_id | source_id |
| --- | --- |
| `index:SSE50` | `000016.SH` |
| `index:CSI300` | `000300.SH` |
| `index:CSI500` | `000905.SH` |

Confirm new source ids with `index_basic` before adding them to maintenance
docs or defaults.

## Fake Provider Gate

Run the instrument-universe tests before a real provider update:

```bash
PYTHONPATH=maintool/src python3 maintool/tests/test_instrument_universe.py
```

## Real Smoke Test

Use a short recent window and explicit real-provider opt-in:

```bash
python3 maintool/bin/fintool --repo-root . maintain-plan instrument_universe \
  --provider tushare \
  --enable-real-api \
  --universe-id index:CSI300 \
  --source-id 000300.SH \
  --start-date 20260401 \
  --end-date 20260506 \
  --run-id instrument-universe-csi300-smoke-YYYYMMDD
```

Then run:

```bash
python3 maintool/bin/fintool --repo-root . prepare instrument_universe --run-id <run_id>
python3 maintool/bin/fintool --repo-root . ingest instrument_universe --run-id <run_id>
python3 maintool/bin/fintool --repo-root . qa instrument_universe --run-id <run_id>
```

Publish only after checking that the latest member count is plausible.

## Maintenance Notes

- Preserve both `universe_id` and provider-native `source_id` in run manifests.
- Do not synthesize members when `index_weight` returns no rows.
- If a recent window is empty, verify the source id with `index_basic` and widen
  the date window before retrying.
- Downstream datasets should use selectors such as `@universe:index:CSI300`
  rather than copying symbol lists by hand.

## Publish Criteria

Publish only when:

- the universe has at least one member
- primary keys are unique
- `as_of_date` is valid
- weights are non-negative decimal values when present
- generated artifacts are not staged in git
