# FinData

FinData is a personal financial data repository for centralized storage, validation, documentation, and publication of research-ready datasets.

The repository is designed around a few durable rules:

- Every dataset has a human-readable dataset card.
- Every dataset has machine-readable schema and manifest files.
- Raw, staged, published, and archived data are separated.
- Missing data is documented; invalid data is rejected.
- Published data should be reproducible from its metadata, logs, and checks.

## Layout

```text
datasets/      Dataset storage, metadata, checks, and logs.
maintool/      Maintenance CLI and supporting code.
docs/          Repository standards and operating notes.
sandboxes/     Disposable development data.
backups/       Disaster recovery snapshots.
```

## First Dataset

The first pilot dataset is `tushare_daily`, based on the Tushare Pro `daily` API. The second maintained dataset is `trade_calendar`, a provider-independent exchange calendar dataset. These exist to harden the repository standard before adding broader financial datasets such as reports, filings, fundamentals, macro data, and news.

## Maintenance Principle

Publish for consumers, archive for reproducibility, and back up for survival.
