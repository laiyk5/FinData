# Report Catalog Dataset Card

## Summary

`report_catalog` stores metadata and PDF links for A-share periodic reports. It intentionally does not download or cache PDF files; downstream PDF datasets should consume this catalog when they need source files.

The first maintained source is Cninfo (`cninfo`), using announcement-list metadata and normalized PDF URLs. Cninfo is a website endpoint rather than a stable official API, so raw responses and request ledgers are preserved for replay and repair.

## Coverage

Initial coverage is empty. Planned maintenance uses `instrument_universe` selectors, such as `@universe:index:SSE50`, and defaults to annual, semiannual, first-quarter, and third-quarter full reports.

The `--start-year` and `--end-year` maintenance options are disclosure-year windows for Cninfo `seDate` queries. Published rows store the inferred fiscal/report year separately in `report_year`; for example, a 2023 annual report is commonly disclosed in 2024.

## Provider Procedure

Maintenance must follow the sandbox workflow:

```text
maintain-plan -> prepare -> ingest -> qa -> publish
```

Real Cninfo runs require `--provider cninfo --enable-real-api`. Run the fake provider first, then a small real smoke test, then batch historical ingestion by universe and year window.

Cninfo requests are serialized and rate limited. The implementation records request status, attempts, raw JSON, row counts, and block/error reasons in the run sandbox. It stops on anti-bot signals such as HTTP 403/429, non-JSON verification pages, or other blocked responses.

## Known Missingness

Some early historical announcements may be missing, renamed, revised, or represented differently across source pages. Missing data should be recorded in QA or run review notes, not silently synthesized.

## Validation Expectations

- `source`, `announcement_id`, `ts_code`, `report_type`, `report_year`, `announcement_title`, and `pdf_url` must be present.
- Duplicate `(source, announcement_id)` rows block publish.
- `report_type` must be one of `annual`, `semiannual`, `q1`, or `q3`.
- `pdf_url` must point to `https://static.cninfo.com.cn/`.
- At most one row per `(ts_code, report_year, report_type)` can have `latest_version=true`.
