# Dataset Standard

This document defines the minimum structure for datasets in FinData.

## Required Files

Each dataset should contain:

```text
dataset_card.md
schema.yaml
maintenance.md
manifest.yaml
published/current/
```

`published/current/` is the stable consumer path for the latest accepted
version. The extra `current/` layer exists so the maintainer can prepare a new
published directory and atomically swap it into place.

## Required Workspace Roots

```text
datasets/   Dataset contracts and current published versions.
sandboxes/  Run-local raw, staged, QA, and logs.
archived/   Historical published versions for every dataset.
cache/      Provider/API response cache for reuse and recovery.
```

## Required Metadata

`dataset_card.md` explains the dataset for humans. It should describe purpose, source, schema, coverage, known missingness, update policy, validation checks, and usage notes.

`schema.yaml` defines fields, types, meanings, units, and primary keys.

`manifest.yaml`, when present locally, records generated operational metadata
such as dataset status, coverage, current publish location, archive location,
and known missingness summary. It is a dataset artifact rather than a
git-managed contract.

## Git Boundary

Git is the source of truth for dataset contracts and maintenance mechanics, not for
large or frequently regenerated data products.

Track these files in git:

```text
dataset_card.md
schema.yaml
maintenance.md
```

Do not track generated artifacts unless a dataset-specific document explicitly
allows a small fixture:

```text
published/
manifest.yaml
archived/
cache/
sandboxes/runs/
```

`schema.yaml` is a human-reviewed contract. It should not be generated from a
single run.

`manifest.yaml` should be generated or updated by maintenance commands when a
dataset is initialized or published. Do not hand-maintain it as source code, and
do not commit it. Stable source-controlled metadata belongs in
`dataset_card.md`, `schema.yaml`, and `maintenance.md`.

## Required Checks

Run QA artifacts live in the sandbox:

```text
sandboxes/runs/{dataset}/{run_id}/qa/
```

## Publishing Rule

A dataset version can be published only when validation passes or all exceptions are explicitly documented and accepted.

## Maintenance Runs

Dataset updates should run through a sandbox:

```text
sandboxes/runs/{dataset}/{run_id}/
```

The sandbox contains a dataset copy, prepared raw files, a request ledger, QA
reports, and run metadata. The canonical dataset under `datasets/` should be
changed only during the final publish step.

Historical publish outputs belong under `archived/`. Each archived version
should be a complete package, not just bare data files. At minimum that package
should include the published data plus the matching dataset card, schema,
maintenance notes, manifest snapshot, and archive metadata.

Provider raw responses may be mirrored into `sandboxes/runs/{dataset}/{run_id}/raw/`
for run-local debugging, but the reusable canonical copy should live under
`cache/{provider}/{api}/`.
