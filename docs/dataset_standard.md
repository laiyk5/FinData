# Dataset Standard

This document defines the minimum structure for datasets in FinData.

## Required Files

Each dataset should contain:

```text
dataset_card.md
manifest.yaml
schema.yaml
data/
logs/
checks/
```

## Required Data Folders

```text
data/raw/                Provider responses or source extracts.
data/staged/             Normalized data waiting for validation.
data/published/current/  Current trusted consumer-facing version.
data/archive/            Previous accepted published versions.
```

## Required Metadata

`dataset_card.md` explains the dataset for humans. It should describe purpose, source, schema, coverage, known missingness, update policy, validation checks, and usage notes.

`schema.yaml` defines fields, types, meanings, units, and primary keys.

`manifest.yaml` records operational metadata such as dataset status, provider, coverage, storage format, last published version, and known missingness summary.

## Required Checks

Each dataset should eventually maintain:

```text
checks/validation_report.json
checks/missingness.yaml
checks/checksum_manifest.yaml
checks/coverage_report.yaml
```

## Publishing Rule

A dataset version can be published only when validation passes or all exceptions are explicitly documented and accepted.

## Maintenance Runs

Dataset updates should run through a sandbox:

```text
sandboxes/runs/{dataset}/{run_id}/
```

The sandbox contains a dataset copy, prepared raw files, a request ledger, QA reports, and run metadata. The canonical dataset under `datasets/` should be changed only during the final publish step.
