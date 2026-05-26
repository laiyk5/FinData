# New Dataset SOP

This SOP defines how to add a maintained dataset without relying on a specific
chat session, local machine, or agent memory.

## Goals

A new dataset is ready when another maintainer can clone the repo, read the
dataset documentation, run the fake-provider path, perform a small real-provider
smoke test, and safely scale the maintenance run using only committed code and
docs plus provider credentials or external data artifacts.

## Git Boundary

Commit the mechanism:

- dataset contract: `schema.yaml`
- human documentation: `dataset_card.md` and `maintenance.md`
- maintool code: `DatasetSpec`, planning, preparation, ingestion, QA, publish
- tests and fake-provider fixtures

Do not commit generated data:

- `data/raw/`
- `data/staged/`
- `data/published/`
- `data/archive/`
- `manifest.yaml`
- `checks/*.json`
- `logs/*.json`
- `sandboxes/runs/`

If a generated file is small and needed as a test fixture, place it under the
test tree and explain why it is a fixture rather than a live dataset artifact.

## Creation Flow

1. Define the dataset purpose and grain.
2. Create `datasets/{dataset}/dataset_card.md`.
3. Create `datasets/{dataset}/schema.yaml`.
4. Let the maintenance tooling create or update local `manifest.yaml` during
   initialization and publish; do not treat it as source-controlled state.
5. Add `datasets/{dataset}/maintenance.md` using
   `docs/dataset_maintenance_sop.md` as the checklist.
6. Add or update `DatasetSpec` in `maintool/src/maintool/dataset_specs.py`.
7. Implement request planning in `plan_requests`.
8. Implement fake-provider preparation first.
9. Implement ingestion from raw provider responses into canonical rows.
10. Implement QA rules for schema, primary keys, missingness, and unusual values.
11. Implement publish integration only after QA can block bad data.
12. Add tests for fake-provider full flow and restartability.
13. Run a fake-provider end-to-end test.
14. Run a small real-provider smoke test with explicit opt-in.
15. Review logs, ledgers, and QA reports before scaling.
16. Scale in bounded windows with conservative rate limits and restartable runs.

## Required Decisions

Record these choices in the dataset card or maintenance guide:

- canonical dataset name
- source provider and endpoint
- row grain
- primary key
- partitioning strategy
- date or version field
- expected missingness categories
- retry and rate-limit policy
- fake-provider behavior
- real-provider smoke scope
- publish and rollback expectations

## Provider Safety

Real provider access must require explicit CLI opt-in. The first real run should
be small enough to inspect manually. For web endpoints without stable official
API contracts, use conservative request rates, jitter, retryable ledgers, raw
response capture, and anti-block detection.

## Bootstrap On A New Machine

1. Clone the repo.
2. Install maintool dependencies.
3. Obtain provider credentials or access settings outside git.
4. Restore external dataset artifacts only if the task needs existing published
   data.
5. Run `validate-dataset` for the dataset.
6. Run the fake-provider maintenance test.
7. Run a small real-provider smoke test.
8. Continue or create a maintenance run from the documented commands.

If existing artifacts are not restored, the maintainer can still rebuild the
dataset from source using the SOP, subject to provider availability and rate
limits.

## Completion Checklist

- Dataset card exists and explains purpose, source, grain, and validation.
- Schema exists and matches `DatasetSpec`.
- Maintenance guide exists and includes smoke, full-run, resume, QA, and publish
  commands.
- Fake-provider path passes.
- Real-provider smoke test passes.
- Progress logs and summaries are written for long stages.
- Generated data and checks are ignored or stored outside git.
- A clean commit contains only code, contracts, and documentation.
