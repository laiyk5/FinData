# FinData Repository Card

## Purpose

FinData is a general-purpose financial data repository for research and development. It aims to store datasets with enough structure, documentation, and maintenance evidence that both humans and agents can safely inspect, update, validate, and use the data.

## Scope

Planned dataset families include:

- Market prices
- Financial reports
- Company filings
- Fundamentals
- Macro data
- News and event data

The repository currently starts with the `tushare_daily` pilot dataset.

## Data Lifecycle

Each dataset follows this lifecycle:

```text
raw -> staged -> validated -> published -> archived/backed up
```

Raw data preserves provider responses. Staged data is normalized but not trusted. Published data is validated and consumer-facing. Archived data keeps previous accepted versions. Backups are external or snapshot copies for disaster recovery.

## Quality Policy

Invalid data should not be published. Missing data may be accepted only when it is documented in the dataset card, manifest, or missingness records.

## Repository Status

Status: early scaffold

The repository standard and first dataset metadata are being established before automated ingestion and publication are added.
