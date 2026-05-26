# Dataset Standard

This document defines the minimum structure for datasets in FinData.

## Required Files

Each dataset should contain:

```text
dataset_card.md
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

`manifest.yaml`, when present locally, records generated operational metadata
such as dataset status, coverage, last published version, and known missingness
summary. It is a dataset artifact rather than a git-managed contract.

## Git Boundary

Git is the source of truth for dataset contracts and maintenance mechanics, not for
large or frequently regenerated data products.

Track these files in git:

```text
dataset_card.md
schema.yaml
maintenance.md
checks/README.md
logs/README.md
```

Do not track generated artifacts unless a dataset-specific document explicitly
allows a small fixture:

```text
data/
manifest.yaml
checks/*.json
logs/*.json
sandboxes/runs/
```

`schema.yaml` is a human-reviewed contract. It should not be generated from a
single run.

`manifest.yaml` should be generated or updated by maintenance commands when a
dataset is initialized or published. Do not hand-maintain it as source code, and
do not commit it. Stable source-controlled metadata belongs in
`dataset_card.md`, `schema.yaml`, and `maintenance.md`.

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
