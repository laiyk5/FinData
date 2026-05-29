# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FinData is a personal financial data repository for centralized storage, validation, documentation, and publication of research-ready datasets. Data flows through a controlled pipeline: raw → staged → validated → published → archived.

The maintenance tooling lives in `maintool/` and is a zero-dependency Python 3.11+ CLI built on stdlib only.

## Commands

```bash
# Run the CLI (from repo root)
python -m maintool --repo-root . <command>

# Or via the entry point script
maintool/bin/fintool --repo-root . <command>

# Run all tests
cd maintool && python -m unittest discover tests -v

# Run a single test file
cd maintool && python -m unittest tests/test_pipeline.py -v

# Run a specific test
cd maintool && python -m unittest tests/test_pipeline.py.PipelineTests.test_full_fake_pipeline_publishes_current -v
```

Tests run against fake/mock providers by default — no API key needed. They copy dataset fixtures from `datasets/` into temp directories, so `datasets/*/published/current/` must exist before running tests.

## Architecture

### CLI & Pipeline

Entry point: `maintool/src/maintool/cli.py` → `main()`. Commands:

| Command | What it does |
|---|---|
| `list` | List all datasets |
| `inspect <name>` | Check required files/dirs exist |
| `validate <name>` | Validate scaffold + published CSV content |
| `maintain-plan` | Create a restartable run sandbox with request plan |
| `prepare` | Fetch raw provider responses (restartable ledger) |
| `ingest` | Normalize raw → staged CSV, merge into sandbox current |
| `qa` | Schema validation, duplicate detection, missingness, unusual values |
| `publish` | Atomic swap (next→current), archive previous, update manifest |
| `review` | Human-readable run summary with recommendations |
| `maintain-run` | Full pipeline in one command |

### Key Modules

- **`dataset_specs.py`** — Central registry (`SPECS` dict) of all 8 supported datasets. Each `DatasetSpec` defines the Tushare API name, canonical fields, primary key, date field, partition field, and output filenames. Also contains the request planning engine that decides how to batch API calls (symbol_range / trade_date_all / auto strategies).

- **`run_sandbox.py`** — `RunContext` dataclass with path properties for every directory in a run sandbox. `create_run_sandbox()` copies the current dataset into `sandboxes/runs/{dataset}/{run_id}/`, plans API requests into a restartable ledger (`prepare_ledger.json`), and writes a `run_manifest.json`.

- **`prepare.py`** — The provider-agnostic fetch layer. `prepare_raw()` dispatches to the right fetcher (fake/mock/tushare/cninfo) and iterates the request ledger with retry logic, rate limiting, jitter, and a request budget. Successful responses are cached under `cache/{provider}/{api}/` for reuse across runs.

- **`ingest.py`** — Reads prepared raw JSON files, normalizes values to strings, writes staged CSV files (partitioned by date/exchange/universe), then merges with existing published current rows (upsert by primary key). Clears and rewrites the sandbox `published/current/` tree.

- **`qa.py`** — Three reports: validation (schema checks, decimal parsing, OHLC sanity, cross-file duplicate PKs), missingness (expected vs actual key coverage, with calendar/history-based acceptance), and unusual values (large price moves, zero volume, volume spikes). QA must pass before publish.

- **`publish.py`** — Copies sandbox current → `published/next-{run_id}`, checksums it, archives the previous current into `archived/{dataset}/` as a complete package (data + metadata snapshot), then atomically renames next→current. Updates `manifest.yaml` with new coverage/storage/quality/publication blocks.

- **`tushare_http.py`** — Low-level HTTP client for the Tushare Pro API. Handles error classification (rate_limit/server/permission/network) for retry decisions. Each dataset API has a typed fetch function returning `TushareDailyResponse`.

- **`workspace.py`** — Pure path resolution functions. All paths derive from `repo_root`.

- **`review.py`** — Aggregates manifest, ledger, ingest report, and QA reports into a readable run summary with a recommended next action.

### Provider Model

Four providers: `fake` (deterministic test data), `mock` (realistic test data for trade_calendar/instrument_universe/report_catalog), `tushare` (real API, requires `TUSHARE_API_KEY` env var + `--enable-real-api` flag), `cninfo` (real Cninfo API for report_catalog, requires `--enable-real-api`). The safety guard in `validate_provider_guard()` blocks real providers without the explicit opt-in flag.

### Symbol Resolution

The `--symbols` argument accepts either literal comma-separated codes (`000001.SZ,600000.SH`) or `@universe:index:SSE50` — the latter resolves symbols from the published `instrument_universe` dataset, picking members from the latest `as_of_date`.

### Docs Structure

- **`docs/design/`** — Repository architecture and design standards: `dataset_standard.md`, `maintenance_workflow.md`, `reference_selectors.md`
- **`docs/maintenance/`** — Operational SOPs: `_general_maintenance_sop.md` (applies to all datasets), `_new_dataset_sop.md` (how to onboard a dataset), plus per-dataset SOPs (`tushare_daily.md`, `trade_calendar.md`, etc.)

### Data Lifecycle (git boundary)

Tracked in git: `dataset_card.md`, `schema.yaml`, `maintenance.md`. Everything else is generated/derived: `published/`, `manifest.yaml`, `archived/`, `cache/`, `sandboxes/runs/`. The `.gitignore` enforces this.
