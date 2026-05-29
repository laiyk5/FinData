# report_catalog Maintenance SOP

Prerequisite: [`_general_maintenance_sop.md`](_general_maintenance_sop.md)

## Source

- **Provider**: Cninfo website endpoint (NOT a stable official API)
- **Endpoint**: `hisAnnouncement/query`
- **Grain**: one announcement × one source
- **Primary key**: `source, announcement_id`
- **Data**: A-share periodic report metadata + PDF URLs; does **not** download PDF files

Report types: `annual`, `semiannual`, `q1`, `q3`. The `--start-year` / `--end-year` options are Cninfo **disclosure-year** query windows; published `report_year` is inferred per announcement.

## Smoke Test

### Fake/Mock Provider

```bash
python -m maintool --repo-root . maintain-run report_catalog \
  --provider fake \
  --trade-date 20240506 \
  --symbols 000001.SZ
```

### Real Provider

Start very small — Cninfo is a website, not a stable API:

```bash
python -m maintool --repo-root . maintain-plan report_catalog \
  --provider cninfo --enable-real-api \
  --symbols '@universe:index:SSE50' \
  --start-year 2025 --end-year 2026 \
  --report-types annual,semiannual,q1,q3 \
  --max-pages-per-request 1 \
  --request-budget 20 \
  --run-id report-catalog-cninfo-smoke-YYYYMMDD

python -m maintool --repo-root . prepare report_catalog --run-id <run_id>
python -m maintool --repo-root . ingest report_catalog --run-id <run_id>
python -m maintool --repo-root . qa report_catalog --run-id <run_id>
```

Publish only after inspecting raw responses, request ledgers, and QA reports.

## Scaled Historical Backfill

Use **five-year disclosure windows** per run. Keep each window in its own `run_id` and publish after QA passes:

```bash
python -m maintool --repo-root . maintain-plan report_catalog \
  --provider cninfo --enable-real-api \
  --symbols '@universe:index:CSI300' \
  --start-year 2017 --end-year 2021 \
  --report-types annual,semiannual,q1,q3 \
  --max-pages-per-request 1 \
  --request-budget 1500 \
  --run-id report-catalog-csi300-2017-2021-YYYYMMDD
```

## Request Planning

Plans one request per symbol × year × page. With `--max-pages-per-request 1`, each page fetches up to 30 announcements. The `--request-budget` caps total non-skipped requests. Use `--jitter-seconds "1.0,3.0"` for Cninfo to avoid triggering anti-bot defenses.

## Anti-Block Rules

Cninfo is a website endpoint — **stop and inspect** on:
- HTTP 403/429
- Verification pages (non-JSON responses)
- Repeated network EOFs
- Malformed responses

The prepare stage classifies these as `blocked` errors and stops the run — it does not keep retrying. Treat occasional retryable network failures as recoverable.

## Rate Limits

- Always serialize requests (no parallelism)
- Default Cninfo base delay: 2.0s, with jitter
- `--rate-limit-seconds` and `--jitter-seconds` override defaults
- `--retry-backoff-seconds` defaults to 30.0s for Cninfo

## QA Expectations

**Blocks publish** unless:
- `source`, `announcement_id`, `ts_code`, `report_type`, `report_year`, `announcement_title`, `pdf_url` are present
- Primary keys unique
- `source` is `cninfo`
- `report_type` is one of: `annual`, `semiannual`, `q1`, `q3`
- `report_year` is a 4-digit year (1990–2100)
- `period_end` and `announcement_date` are valid `YYYYMMDD` when present
- `pdf_url` starts with `https://static.cninfo.com.cn/`
- Boolean fields (`is_correction`, `is_summary`, `is_english`, `is_cancelled`, `latest_version`) are `"true"` or `"false"` when present
- `version_no` is a digit string when present
- At most one `latest_version=true` row per `(ts_code, report_year, report_type)`

## Missingness

No automatic missingness classification. Early historical announcements may be missing, renamed, or revised — record gaps in QA/review, do not synthesize.

## Publish Criteria

- `prepare` has no unresolved failures
- `ingest` row counts are plausible
- QA passes, `warning_count=0` or warnings are documented/accepted
- Generated artifacts not staged in git
