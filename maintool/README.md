# maintool

`maintool` is the maintenance command-line tool for FinData.

The first version is intentionally small and dependency-free. Its job is to inspect dataset metadata and validate the repository scaffold before ingestion, publishing, backup, and provider integrations are added.

## Usage

```bash
uv run python bin/fintool --repo-root .. list
uv run python bin/fintool --repo-root .. inspect tushare_daily
uv run python bin/fintool --repo-root .. validate tushare_daily
uv run python bin/fintool --repo-root .. maintain-plan tushare_daily --provider fake --trade-date 20240506
uv run python bin/fintool --repo-root .. prepare tushare_daily --run-id RUN_ID
uv run python bin/fintool --repo-root .. ingest tushare_daily --run-id RUN_ID
uv run python bin/fintool --repo-root .. qa tushare_daily --run-id RUN_ID
uv run python bin/fintool --repo-root .. review tushare_daily --run-id RUN_ID
uv run python bin/fintool --repo-root .. publish tushare_daily --run-id RUN_ID
uv run python bin/fintool --repo-root .. maintain-run tushare_daily --provider fake --trade-date 20240506
uv run python bin/fintool --repo-root .. maintain-run trade_calendar --provider mock --exchange SSE --start-date 20240501 --end-date 20240531
```

Run commands from the `maintool/` directory.

`maintain-run` is the full fake-provider pipeline shortcut. It creates a run sandbox, prepares raw data, ingests into the sandbox copy, runs QA, and publishes only if QA passes.

`review` summarizes a run's lifecycle, prepare ledger, ingest report, QA reports, and recommended next action.

## Real Tushare Smoke Run

Real Tushare ingestion is opt-in and reads the token only from `TUSHARE_API_KEY`.

```bash
uv run python bin/fintool --repo-root .. maintain-run tushare_daily \
  --provider tushare \
  --enable-real-api \
  --trade-date 20240510 \
  --symbols 000001.SZ \
  --rate-limit-seconds 0.25 \
  --run-id real-smoke-20240510
```

Do not use broad symbol/date ranges until trade calendar and suspension classification are implemented.

For `tushare_daily`, request scheduling defaults to `--daily-request-strategy auto`. The planner batches comma-separated symbols and date ranges while keeping estimated rows per request within the documented 6000-row limit, then records the selected plan in `run_manifest.json`. Use `--daily-request-strategy trade_date_all` only when an all-market daily pull is intentional.

## Trade Calendar

Mock provider:

```bash
uv run python bin/fintool --repo-root .. maintain-run trade_calendar \
  --provider mock \
  --exchange SSE \
  --start-date 20240501 \
  --end-date 20240531 \
  --run-id trade-calendar-mock-202405
```

Real Tushare provider:

```bash
uv run python bin/fintool --repo-root .. maintain-run trade_calendar \
  --provider tushare \
  --enable-real-api \
  --exchange SSE \
  --start-date 20240501 \
  --end-date 20240531 \
  --run-id trade-calendar-real-smoke-202405
```
