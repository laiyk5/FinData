# trade_calendar Maintenance

Follow `docs/dataset_maintenance_sop.md` first. This file records the
dataset-specific procedure for exchange trading calendars.

## Ownership Boundary

Tracked in git:

- `dataset_card.md`
- `schema.yaml`
- stable manifest metadata
- this maintenance guide

Generated and not tracked:

- `data/published/current/`
- `data/archive/`
- `checks/*.json`
- `logs/*.json`
- `sandboxes/runs/trade_calendar/`

## Source And Scope

Provider: Tushare Pro

API: `trade_cal`

Canonical exchanges:

- `SSE`
- `SZSE`

One row represents one exchange on one calendar date.

## Fake Provider Gate

Run calendar and pipeline tests before a real provider update:

```bash
PYTHONPATH=maintool/src python3 maintool/tests/test_trade_calendar.py
PYTHONPATH=maintool/src python3 maintool/tests/test_pipeline.py
```

## Real Smoke Test

Use a short known week first:

```bash
python3 maintool/bin/fintool --repo-root . maintain-plan trade_calendar \
  --provider tushare \
  --enable-real-api \
  --exchange SSE \
  --start-date 20240513 \
  --end-date 20240517 \
  --run-id trade-calendar-sse-smoke-YYYYMMDD
```

Then run:

```bash
python3 maintool/bin/fintool --repo-root . prepare trade_calendar --run-id <run_id>
python3 maintool/bin/fintool --repo-root . ingest trade_calendar --run-id <run_id>
python3 maintool/bin/fintool --repo-root . qa trade_calendar --run-id <run_id>
```

Publish only after checking date continuity and `pretrade_date` behavior.

## Full Maintenance

Maintain exchanges separately when that makes review easier, then publish after
QA passes. Use the listing date or a documented exchange-specific start date
rather than a synthetic earlier date that creates structural missingness.

## Publish Criteria

Publish only when:

- every requested calendar date is present
- primary keys are unique
- `is_open` is `0` or `1`
- date fields are valid `YYYYMMDD`
- `pretrade_date` points to a previous open day when present
- generated artifacts are not staged in git
