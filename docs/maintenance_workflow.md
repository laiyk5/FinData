# Maintenance Workflow

FinData maintenance is a controlled pipeline.

## Standard Flow

```text
create run sandbox -> inspect current dataset -> prepare raw data -> ingest sandbox copy -> QA -> archive/publish
```

## Stages

1. Create a run sandbox under `sandboxes/runs/{dataset}/{run_id}/`.
2. Copy the active dataset state into the sandbox.
3. Prepare raw provider responses with a restartable request ledger.
4. Ingest prepared raw files into the sandbox dataset copy.
5. Run QA for schema, duplicates, missingness, and unusual values.
6. Publish only from a QA-passed sandbox.
7. Move the previous published version into `data/archive/`.
8. Write logs, checksums, coverage reports, and manifest updates.
9. Create or refresh backups when backup support is added.

## Safety Rules

- Published data should not be modified directly.
- Failed validation should block publication.
- Missing data should be recorded, not silently ignored.
- Backups should protect against accidental local deletion or corruption.
- Archive versions should remain available for research reproducibility.
- Raw preparation should be restartable from the request ledger.
- The real provider token should not be used until the fake-provider pipeline is healthy.
- Real provider runs must use explicit CLI opt-in and should start as small smoke tests.
