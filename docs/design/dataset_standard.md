# Dataset Standard

This document defines the minimum structure for datasets in FinData.

## Directory Layout

Datasets live in `datasets/<provider>/<api_name>/`:

```text
datasets/
  tushare/
    daily/
    daily_basic/
    ...
  cninfo/
    report_catalog/
```

`datasets/` is an output folder — nothing under it is tracked in git.

## Required Documentation

Documentation is tracked in `docs/datasets/<provider>/<api_name>/`:

```text
dataset_card.md   # Reference link, brief intro, metadata
schema.yaml       # Field definitions and primary key
```

Maintenance SOPs live in `docs/maintenance/<name>.md`.

## Required Workspace Roots

```text
datasets/   Published data (output only).
sandboxes/  Run-local raw, staged, QA, and logs.
backups/    Reserved for optional backup copies; publishing currently does not write backups.
cache/      Provider/API response cache for reuse and recovery.
```

## Git Boundary

Track these in git:

```text
docs/datasets/<provider>/<api_name>/dataset_card.md
docs/datasets/<provider>/<api_name>/schema.yaml
docs/maintenance/<name>.md
maintool/
```

Do not track generated artifacts:

```text
datasets/
backups/
cache/
sandboxes/runs/
```

## Required Checks

Run QA artifacts live in the sandbox:

```text
sandboxes/runs/{dataset_name}/{run_id}/qa/
```

## Publishing Rule

A dataset version can be published only when validation passes or all exceptions are explicitly documented and accepted.

Datasets may choose their canonical file format in `DatasetSpec`. Backtest-facing Tushare market datasets (`daily`, `adj_factor`, and `index_weight`) use Apache Parquet under month partitions such as `current/trade_month=202606/`.

## Maintenance Runs

Dataset updates run through a sandbox:

```text
sandboxes/runs/{dataset_name}/{run_id}/
```

The sandbox contains a dataset copy, prepared raw files, a request ledger, QA reports, and run metadata. The canonical dataset under `datasets/` is changed only during the final publish step.

## Backup

Publishing currently does not create rolling backups. Previous `published/current/` is replaced only during the final publish step after QA passes; run sandboxes, provider cache, stage logs, checksums, and git-tracked contracts remain the operational audit trail.
