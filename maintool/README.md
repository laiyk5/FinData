# maintool

`maintool` is the maintenance command-line tool for FinData.

It plans provider requests, prepares raw responses, ingests normalized tables into run sandboxes, runs QA, reviews run evidence, and publishes QA-passed datasets. Backtest-facing Tushare market datasets use pandas/pyarrow for Apache Parquet output.

## Usage

All commands must be run from within a workspace directory. The CLI uses the current working directory as the workspace root.

```bash
# From the repo root, cd into the workspace first
cd workspace && python -m maintool list
cd workspace && python -m maintool inspect tushare_daily
cd workspace && python -m maintool validate tushare_daily
cd workspace && python -m maintool maintain-plan tushare_daily --fake --trade-date 20240506
cd workspace && python -m maintool prepare tushare_daily --run-id RUN_ID
cd workspace && python -m maintool ingest tushare_daily --run-id RUN_ID
cd workspace && python -m maintool qa tushare_daily --run-id RUN_ID
cd workspace && python -m maintool review tushare_daily --run-id RUN_ID
cd workspace && python -m maintool publish tushare_daily --run-id RUN_ID
cd workspace && python -m maintool maintain-run tushare_daily --fake --trade-date 20240506
cd workspace && python -m maintool maintain-run trade_calendar --provider mock --exchange SSE --start-date 20240501 --end-date 20240531
```

Or via the entry point script:
```bash
cd workspace && python ../maintool/bin/fintool list
```

`maintain-run` is the full fake-provider pipeline shortcut. It creates a run sandbox, prepares raw data, ingests into the sandbox copy, runs QA, and publishes only if QA passes.

`review` summarizes a run's lifecycle, prepare ledger, ingest report, QA reports, and recommended next action.

## Real Tushare Smoke Run

Real Tushare ingestion is opt-in and reads the token only from `TUSHARE_API_KEY`.

```bash
cd workspace && python -m maintool maintain-run tushare_daily \
  --trade-date 20240510 \
  --symbols 000001.SZ \
  --rate-limit-seconds 0.25 \
  --run-id real-smoke-20240510
```

Use small smoke runs before broad ranges. Broad all-market Tushare pulls should use explicit `--all-market` and a date-based request strategy.

For `tushare_daily`, request scheduling defaults to `--daily-request-strategy auto`. The planner batches comma-separated symbols and date ranges while keeping estimated rows per request within the documented 6000-row limit, then records the selected plan in `run_manifest.json`. Use `--all-market --daily-request-strategy trade_date_all` when an all-market daily pull is intentional.

## Trade Calendar

Mock provider:

```bash
cd workspace && python -m maintool maintain-run trade_calendar \
  --provider mock \
  --exchange SSE \
  --start-date 20240501 \
  --end-date 20240531 \
  --run-id trade-calendar-mock-202405
```

Real Tushare provider:

```bash
cd workspace && python -m maintool maintain-run trade_calendar \
  --exchange SSE \
  --start-date 20240501 \
  --end-date 20240531 \
  --run-id trade-calendar-real-smoke-202405
```
