# Maintenance Workflow

FinData maintenance is a controlled pipeline.

## Standard Flow

```text
create run sandbox -> inspect current dataset -> prepare raw data -> ingest sandbox copy -> QA -> publish
```

## Stages

1. Create a run sandbox under `sandboxes/runs/{dataset}/{run_id}/`.
2. Copy the active dataset state into the sandbox.
3. Prepare raw provider responses with a restartable request ledger.
4. Ingest prepared raw files into the sandbox dataset copy.
5. Run QA for schema, duplicates, missingness, and unusual values.
6. Publish only from a QA-passed sandbox.
7. Copy the previous published version into `backups/{provider}/{api_name}/` as a rolling backup (last 3 retained).
8. Write logs, checksums, coverage reports, and manifest updates in the sandbox or dataset contract as appropriate.
9. Save provider raw responses into `cache/{provider}/{api}/` and mirror the run-local copy into `sandboxes/runs/{dataset}/{run_id}/raw/` for debugging and restartability.

## Safety Rules

- Published data should not be modified directly.
- Failed validation should block publication.
- Missing data should be recorded, not silently ignored.
- Rolling backups should protect against accidental local deletion or corruption.
- The real provider token should not be used until the fake-provider pipeline is healthy.
- Real provider runs must use explicit CLI opt-in and should start as small smoke tests.
