from __future__ import annotations

from pathlib import Path
from typing import Any

from .ingest import ingest_prepared_raw
from .prepare import prepare_fake_raw, prepare_raw
from .publish import publish_sandbox
from .qa import run_qa
from .run_sandbox import RunContext, create_run_sandbox


def run_full_fake_pipeline(
    repo_root: Path,
    dataset_name: str,
    symbols: list[str],
    trade_dates: list[str],
    run_id: str | None = None,
    rate_limit_seconds: float = 0.0,
    max_retries: int = 3,
    retry_backoff_seconds: float = 0.0,
    jitter_seconds: str | None = None,
    request_budget: int | None = None,
    extras: dict[str, Any] | None = None,
) -> tuple[RunContext, dict[str, Any]]:
    context = create_run_sandbox(
        repo_root=repo_root,
        dataset_name=dataset_name,
        provider="fake",
        symbols=symbols,
        trade_dates=trade_dates,
        run_id=run_id,
        rate_limit_seconds=rate_limit_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        jitter_seconds=jitter_seconds,
        request_budget=request_budget,
        extras=extras,
    )
    prepare_summary = prepare_fake_raw(context)
    if prepare_summary["failed"]:
        raise RuntimeError(f"Prepare failed for {prepare_summary['failed']} request(s).")

    ingest_report = ingest_prepared_raw(context)
    qa_status = run_qa(context)
    if not qa_status["passed"]:
        raise RuntimeError("QA failed. Publish is blocked.")

    publish_log = publish_sandbox(context)
    return context, {
        "prepare": prepare_summary,
        "ingest": ingest_report,
        "qa": qa_status,
        "publish": publish_log,
    }


def run_full_pipeline(
    repo_root: Path,
    dataset_name: str,
    provider: str,
    symbols: list[str],
    trade_dates: list[str],
    run_id: str | None = None,
    rate_limit_seconds: float = 0.0,
    max_retries: int = 3,
    retry_backoff_seconds: float = 0.0,
    jitter_seconds: str | None = None,
    request_budget: int | None = None,
    extras: dict[str, Any] | None = None,
) -> tuple[RunContext, dict[str, Any]]:
    context = create_run_sandbox(
        repo_root=repo_root,
        dataset_name=dataset_name,
        provider=provider,
        symbols=symbols,
        trade_dates=trade_dates,
        run_id=run_id,
        rate_limit_seconds=rate_limit_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        jitter_seconds=jitter_seconds,
        request_budget=request_budget,
        extras=extras,
    )
    prepare_summary = prepare_raw(context)
    if prepare_summary["failed"]:
        raise RuntimeError(f"Prepare failed for {prepare_summary['failed']} request(s).")

    ingest_report = ingest_prepared_raw(context)
    qa_status = run_qa(context)
    if not qa_status["passed"]:
        raise RuntimeError("QA failed. Publish is blocked.")

    publish_log = publish_sandbox(context)
    return context, {
        "prepare": prepare_summary,
        "ingest": ingest_report,
        "qa": qa_status,
        "publish": publish_log,
    }
