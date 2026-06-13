# Dataset Maintenance SOP

This SOP is the default operating procedure for maintaining an existing dataset.
Dataset-specific `maintenance.md` files may tighten parameters, but should not
skip the safety gates here.

## What Must Be Reproducible

A maintainer should be able to continue work from git documentation plus any
external data artifacts. Do not rely on chat history for:

- provider names and endpoints
- request scopes
- rate limits
- smoke-test commands
- restart commands
- QA expectations
- publish criteria
- rollback notes

## Standard Flow

```text
maintain-plan -> prepare -> ingest -> qa -> publish
```

Each run lives under:

```text
sandboxes/runs/{dataset}/{run_id}/
```

The sandbox owns run-local state such as `run_manifest.json`, run-local raw
provider responses, request ledgers, staged files, QA reports, and stage logs.
Reusable canonical provider responses should be stored in `cache/`, with the
sandbox holding the run-local copy used by the current maintenance run.

## Before A Run

1. Read `docs/datasets/{provider}/{api_name}/dataset_card.md`.
2. Read `docs/datasets/{provider}/{api_name}/maintenance.md`.
3. Check `git status --short`.
4. Confirm generated data paths are not staged.
5. Confirm the source index or symbol selector exists if the dataset uses one.
6. Choose a descriptive `run_id`.
7. Decide whether this is fake, smoke, or scaled maintenance.

## Fake Provider Gate

Use `--fake` to validate the pipeline shape before touching the real source.
The fake path should cover:

- sandbox creation
- raw preparation
- restartable ledger behavior
- ingestion
- QA
- publish mechanics
- stage event and summary logs

```bash
cd workspace && python -m maintool maintain-run <dataset> --fake \
  --trade-date 20240506 --symbols 000001.SZ
```

## Real Provider Gate

Omit `--fake` to use the dataset's real provider. Keep the first real run
small, inspectable, and easy to discard. Confirm:

- request count is small
- rate limit and jitter are conservative
- raw responses are captured
- provider errors are classified
- QA passes or fails for a clear reason
- publish is skipped until the smoke output is trusted

```bash
cd workspace && python -m maintool maintain-run <dataset> \
  --trade-date 20240506 --symbols 000001.SZ
```

Requires `TUSHARE_API_KEY` for tushare-backed datasets.

## Scaled Runs

For long runs:

- split work by natural windows such as date ranges, exchanges, or universes
- keep request ledgers restartable
- write progress logs and stage summaries
- monitor early progress, then let the process run
- resume from existing `run_id` instead of creating duplicate runs
- publish in bounded batches when that improves reviewability

**Never run concurrent backfills on the same dataset.** Each run copies the
dataset into its sandbox at plan time and publishes by swapping `published/current/`.
Parallel runs on the same dataset race: the second publish silently overwrites
the first. If you need to backfill multiple index codes or windows on the same
dataset, run them sequentially — wait for one to finish publishing before
starting the next.

For web endpoints with anti-bot risk, use conservative serialized requests,
jitter, retry backoff, and stop on block signals such as HTTP 403/429 or
verification pages.

## Stage Checks

After `prepare`:

- ledger has no unexpected failures
- retry count is acceptable and explained
- raw paths exist for successful requests
- stage summary records elapsed time and counts

After `ingest`:

- prepared row count is plausible
- merge count is plausible
- staged files use the canonical schema

After `qa`:

- validation passes
- duplicate primary keys are absent
- missingness is either acceptable or documented
- unusual value warnings are understood

Before `publish`:

- no unrelated files are staged in git
- generated dataset artifacts are not committed
- backup behavior is understood

## Generated Outputs

Generated outputs are operational evidence and data artifacts. They should be
stored locally or ignored by git unless a dataset-specific fixture rule says
otherwise.

Common generated outputs:

- `datasets/{provider}/{api_name}/published/current/`
- `backups/{provider}/{api_name}/`
- `sandboxes/runs/{dataset}/{run_id}/raw/`
- `sandboxes/runs/{dataset}/{run_id}/staged/`
- `sandboxes/runs/{dataset}/{run_id}/qa/`
- `sandboxes/runs/{dataset}/{run_id}/logs/`

## Handoff Notes

At the end of a maintenance task, record:

- run id
- scope
- provider
- start and end times
- row counts
- failure and retry summary
- QA result
- publish timestamp
- final artifact location
- any follow-up risks

The handoff belongs in committed docs when it changes procedure. Per-run
statistics belong in generated logs and summaries.
