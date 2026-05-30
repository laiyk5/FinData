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
backups/    Rolling backup copies of previous published versions.
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

## Maintenance Runs

Dataset updates run through a sandbox:

```text
sandboxes/runs/{dataset_name}/{run_id}/
```

The sandbox contains a dataset copy, prepared raw files, a request ledger, QA reports, and run metadata. The canonical dataset under `datasets/` is changed only during the final publish step.

## Backup

On publish, the previous `published/current/` is copied to `backups/<provider>/<api_name>/<timestamp>/`. The last 3 backups are kept; older ones are pruned automatically.
