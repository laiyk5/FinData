# FinData

A general-purpose financial data repository for research and development. Datasets are stored with enough structure, documentation, and maintenance evidence that both humans and agents can safely inspect, update, validate, and use the data.

## Scope

Currently maintained datasets (8):

| Dataset | Provider | Description |
|---|---|---|
| `tushare_daily` | Tushare Pro | Unadjusted daily OHLCV bars |
| `tushare_daily_basic` | Tushare Pro | Daily fundamental indicators (PE, PB, turnover, etc.) |
| `tushare_stk_factor_pro` | Tushare Pro | Adjusted OHLCV + technical analysis factors |
| `tushare_adj_factor` | Tushare Pro | Daily forward-adjustment factor (复权因子) |
| `tushare_moneyflow` | Tushare Pro | Money flow data |
| `tushare_index_weight` | Tushare Pro | Index constituent weights |
| `trade_calendar` | Multi-provider | Exchange trading calendars |
| `report_catalog` | Cninfo | Financial report catalog |

Planned families: fundamentals, macro data, news and event data.

## Data Lifecycle

```text
raw → staged → validated → published
```

- **Raw** — provider responses preserved verbatim
- **Staged** — normalized but not yet trusted
- **Validated** — schema checks, duplicate detection, missingness, unusual values
- **Published** — consumer-facing, atomic replacement after QA passes

## Quality Policy

- Invalid data is rejected; publish is blocked until QA passes.
- Missing data is accepted only when documented (dataset card, manifest, or missingness records).
- Every dataset has a human-readable dataset card, machine-readable schema, and manifest.
- Published data is reproducible from its metadata, logs, and checksums.

## Layout

```text
datasets/      Dataset contracts, metadata, and documentation.
maintool/      Python CLI for planning, preparing, ingesting, QA, review, and publish.
docs/          Repository standards, dataset cards, maintenance SOPs.
sandboxes/     Isolated run sandboxes for maintenance operations.
backups/       Reserved; publishing is currently configured not to write backups.
```

## Quick Start

```bash
# Initialize a workspace
python -m maintool init workspace --create-dirs

# List all datasets
cd workspace && python -m maintool list

# Validate a dataset
cd workspace && python -m maintool validate tushare_daily

# Full maintenance pipeline (single dataset)
cd workspace && python -m maintool maintain-run tushare_daily \
  --fake --symbols 000001.SZ --trade-date 20240506

# Run tests
cd maintool && python -m unittest discover tests -v
```

## Maintenance Principle

Publish for consumers after QA; keep reproducibility evidence in run sandboxes, provider cache, logs, checksums, and dataset contracts.
