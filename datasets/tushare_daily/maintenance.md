# tushare_daily Maintenance

Follow `docs/dataset_maintenance_sop.md` first. This file records the
dataset-specific procedure for Tushare daily bars.

## Ownership Boundary

Tracked in git:

- `dataset_card.md`
- `schema.yaml`
- stable manifest metadata
- this maintenance guide

Generated and not tracked:

- `data/raw/`
- `data/staged/`
- `data/published/current/`
- `data/archive/`
- `checks/*.json`
- `logs/*.json`
- `sandboxes/runs/tushare_daily/`

## Source And Scope

Provider: Tushare Pro

API: `daily`

One row represents one security on one trading date. The dataset stores
unadjusted daily bars.

Prefer `instrument_universe` selectors for stock scope:

```text
--symbols '@universe:index:CSI300'
```

## Fake Provider Gate

Run the daily and pipeline tests before a real provider update:

```bash
PYTHONPATH=maintool/src python3 maintool/tests/test_pipeline.py
PYTHONPATH=maintool/src python3 maintool/tests/test_tushare_provider.py
```

## Real Smoke Test

Use a small symbol/date scope and explicit real-provider opt-in:

```bash
python3 maintool/bin/fintool --repo-root . maintain-plan tushare_daily \
  --provider tushare \
  --enable-real-api \
  --symbols 600000.SH,600519.SH \
  --trade-date 20240513 \
  --run-id tushare-daily-smoke-YYYYMMDD
```

Then run:

```bash
python3 maintool/bin/fintool --repo-root . prepare tushare_daily --run-id <run_id>
python3 maintool/bin/fintool --repo-root . ingest tushare_daily --run-id <run_id>
python3 maintool/bin/fintool --repo-root . qa tushare_daily --run-id <run_id>
```

Publish only after the row counts and missingness behavior are understood.

## Scheduling Notes

The planner chooses request shapes based on provider limits and requested scope.
For universe-based historical runs, prefer the default `auto` strategy unless a
dataset review explicitly chooses:

- `--daily-request-strategy symbol_range`
- `--daily-request-strategy trade_date_all`

When `trade_date_all` is used with a selected symbol list, ingestion must filter
provider rows back to the requested symbols.

## Missingness Notes

Expected missingness includes:

- market holidays
- suspensions
- symbols outside the requested universe
- provider gaps

Use `trade_calendar` to distinguish holidays from unexpected missing prices.

## Publish Criteria

Publish only when:

- primary keys are unique
- price and volume sanity checks pass
- missingness is classified or accepted
- provider token values are absent from run manifests and logs
- generated artifacts are not staged in git
