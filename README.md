# FinData

A personal financial data repository — datasets are stored with enough structure,
documentation, and maintenance evidence that both humans and agents can safely
inspect, update, validate, and use the data.

📖 **Documentation:** [laiyk5.github.io/FinData](https://laiyk5.github.io/FinData/)

## Quick Start

```bash
# Initialize a workspace
python -m maintool init workspace --create-dirs

# List available datasets
cd workspace && python -m maintool list

# Run a maintenance pipeline (dry-run with fake data)
cd workspace && python -m maintool maintain-run tushare_daily \
  --fake --symbols 000001.SZ --trade-date 20240506

# Run tests
cd maintool && python -m unittest discover tests -v
```

## Repository

| Directory | Purpose |
|---|---|
| `maintool/` | Python CLI — plan, prepare, ingest, QA, review, publish |
| `docs/` | MkDocs site — design standards, dataset cards, schemas, SOPs |
| `datasets/` | Dataset contracts, metadata, and documentation |
| `sandboxes/` | Isolated run sandboxes for maintenance operations |
| `backups/` | Reserved; publishing is currently configured not to write backups |

See the [documentation site](https://laiyk5.github.io/FinData/) for architecture
details, per-dataset maintenance guides, and development guidelines.
