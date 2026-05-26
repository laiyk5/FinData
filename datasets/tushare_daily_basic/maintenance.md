# tushare_daily_basic Maintenance

Follow `docs/dataset_maintenance_sop.md` first. This file records the
dataset-specific procedure for Tushare `daily_basic`.

## Ownership Boundary

Tracked in git:

- `dataset_card.md`
- `schema.yaml`
- this maintenance guide
- maintool code and tests

Generated and not tracked:

- `manifest.yaml`
- `data/raw/`
- `data/staged/`
- `data/published/current/`
- `data/archive/`
- `checks/*.json`
- `logs/*.json`
- `sandboxes/runs/tushare_daily_basic/`

## Source And Scope

Provider: Tushare Pro

API: `daily_basic`

Documentation: https://tushare.pro/document/2?doc_id=32

The provider documents a 6000-row single-request limit. The maintool reuses the
same date/symbol scheduling strategy as `tushare_daily`.

Prefer `instrument_universe` selectors for maintained stock pools:

```text
--symbols '@universe:index:CSI300'
```

## Fake Provider Gate

Run the fake-provider and pipeline tests before real provider access:

```bash
PYTHONPATH=maintool/src python3 maintool/tests/test_daily_basic.py
PYTHONPATH=maintool/src python3 maintool/tests/test_pipeline.py
```

## Real Smoke Test

Use a small symbol/date scope and explicit real-provider opt-in:

```bash
python3 maintool/bin/fintool --repo-root . maintain-plan tushare_daily_basic \
  --provider tushare \
  --enable-real-api \
  --symbols 600000.SH,600519.SH \
  --trade-date 20240513 \
  --request-budget 1 \
  --run-id tushare-daily-basic-smoke-YYYYMMDD
```

Then run:

```bash
python3 maintool/bin/fintool --repo-root . prepare tushare_daily_basic --run-id <run_id>
python3 maintool/bin/fintool --repo-root . ingest tushare_daily_basic --run-id <run_id>
python3 maintool/bin/fintool --repo-root . qa tushare_daily_basic --run-id <run_id>
```

Publish only after inspecting row counts, missingness, and numeric sanity checks.

## Historical Maintenance

For a universe over a date range:

```bash
python3 maintool/bin/fintool --repo-root . maintain-plan tushare_daily_basic \
  --provider tushare \
  --enable-real-api \
  --symbols '@universe:index:CSI300' \
  --start-date 20160523 \
  --end-date 20260522 \
  --daily-request-strategy auto \
  --run-id tushare-daily-basic-csi300-YYYYMMDD
```

Then run `prepare`, `ingest`, `qa`, and `publish` with the same `run_id`.

Resume interrupted `prepare` stages with the existing `run_id`. Do not create a
duplicate run for the same scope unless the earlier sandbox is intentionally
abandoned and documented.

## Missingness Notes

Expected missingness includes:

- market holidays and non-trading days
- suspended securities
- newly listed securities before listing
- provider-side unavailable daily indicators
- blank PE values for loss-making companies

Use `trade_calendar` and accepted missingness records to distinguish expected
missingness from true provider gaps.

## Publish Criteria

Publish only when:

- primary keys are unique
- date fields are valid
- non-valuation numeric fields parse as decimals
- share counts and market values are non-negative
- `float_share <= total_share`
- `free_share <= float_share`
- missingness is classified or accepted
- generated artifacts are not staged in git
