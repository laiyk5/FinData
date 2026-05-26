# report_catalog Maintenance

Follow `docs/dataset_maintenance_sop.md` first. This file records the
dataset-specific procedure for the Cninfo report catalog.

## Ownership Boundary

Tracked in git:

- `dataset_card.md`
- `schema.yaml`
- stable manifest metadata
- maintool code and tests
- this maintenance guide

Generated and not tracked:

- `data/published/current/`
- `data/archive/`
- `checks/*.json`
- `logs/*.json`
- `sandboxes/runs/report_catalog/`

The catalog stores report metadata and PDF URLs only. It does not download or
cache PDF files.

## Source And Scope

Provider: Cninfo website endpoint

Endpoint: `hisAnnouncement/query`

Common report types:

```text
annual,semiannual,q1,q3
```

Stock scope should usually come from `instrument_universe`, for example:

```text
--symbols '@universe:index:CSI300'
```

The `--start-year` and `--end-year` options are Cninfo disclosure-year query
windows. Published `report_year` is inferred from each announcement.

## Fake Provider Gate

Run fake or mock tests before real Cninfo access:

```bash
PYTHONPATH=maintool/src python3 maintool/tests/test_report_catalog.py
PYTHONPATH=maintool/src python3 maintool/tests/test_pipeline.py
```

The fake path should confirm selector recording, canonical fields, ingestion,
QA, publish, and restartable logs.

## Real Smoke Test

Start with a very small universe/date window and explicit real-provider opt-in:

```bash
python3 maintool/bin/fintool --repo-root . maintain-plan report_catalog \
  --provider cninfo \
  --enable-real-api \
  --symbols '@universe:index:SSE50' \
  --start-year 2025 \
  --end-year 2026 \
  --report-types annual,semiannual,q1,q3 \
  --max-pages-per-request 1 \
  --request-budget 20 \
  --run-id report-catalog-cninfo-smoke-YYYYMMDD
```

Then run:

```bash
python3 maintool/bin/fintool --repo-root . prepare report_catalog --run-id <run_id>
python3 maintool/bin/fintool --repo-root . ingest report_catalog --run-id <run_id>
python3 maintool/bin/fintool --repo-root . qa report_catalog --run-id <run_id>
```

Publish only after inspecting raw responses, request ledgers, and QA reports.

## Scaled Historical Backfill

Use five-year windows for large index universes. Keep each window in its own
`run_id` and publish after QA passes.

Example:

```bash
python3 maintool/bin/fintool --repo-root . maintain-plan report_catalog \
  --provider cninfo \
  --enable-real-api \
  --symbols '@universe:index:CSI300' \
  --start-year 2017 \
  --end-year 2021 \
  --report-types annual,semiannual,q1,q3 \
  --max-pages-per-request 1 \
  --request-budget 1500 \
  --run-id report-catalog-csi300-2017-2021-YYYYMMDD
```

Then:

```bash
python3 maintool/bin/fintool --repo-root . prepare report_catalog --run-id <run_id>
python3 maintool/bin/fintool --repo-root . ingest report_catalog --run-id <run_id>
python3 maintool/bin/fintool --repo-root . qa report_catalog --run-id <run_id>
python3 maintool/bin/fintool --repo-root . publish report_catalog --run-id <run_id>
```

Resume interrupted `prepare` stages with the same `run_id`. Do not create a
duplicate run for the same scope unless the earlier sandbox is intentionally
abandoned and documented.

## Rate Limit And Anti-Block Rules

- Keep requests serialized.
- Use conservative Cninfo defaults: base delay plus jitter.
- Preserve raw responses and request ledgers.
- Stop and inspect HTTP 403/429, verification pages, repeated network EOFs, or
  malformed non-JSON responses.
- Treat occasional retryable network failures as recoverable when the retry
  succeeds and the ledger records the attempt.

## Progress Logging

Long runs must preserve:

- `logs/prepare_events.jsonl`
- `logs/prepare_summary.json`
- `logs/ingest_events.jsonl`
- `logs/ingest_summary.json`
- `logs/qa_events.jsonl`
- `logs/qa_summary.json`
- `logs/publish_events.jsonl`
- `logs/publish_summary.json`

For recurring or scheduled work, each cycle should inspect the previous worker,
wait briefly if it is still running, interrupt only at a checkpoint, record
counts and anomalies, then resume from the existing run id.

## Publish Criteria

Publish only when:

- `prepare` has no unresolved failures
- `ingest` row counts are plausible
- `qa` passes with `warning_count=0` or documented accepted warnings
- generated data/check/log files are not staged in git

After publish, keep final coverage in generated checks or publish logs. Do not
hand-edit coverage counts into tracked docs.
