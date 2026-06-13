# FinData

Personal financial data repository for centralized storage, validation, documentation, and publication of research-ready datasets.

## Sections

- **[Design](design/dataset_standard.md)** — Architecture, workflow, and reference standards
- **[Datasets](datasets/tushare/daily/dataset_card.md)** — Per-dataset documentation, schemas, and maintenance SOPs
- **[Development](dev/README.md)** — Developer guidelines and pre-push checklist

## Quick Start

```bash
# Initialize a workspace
python -m maintool init workspace --create-dirs

# List all datasets
cd workspace && python -m maintool list

# Run a full maintenance pipeline
cd workspace && python -m maintool maintain-run tushare_daily \
  --fake --symbols 000001.SZ --trade-date 20240506
```
