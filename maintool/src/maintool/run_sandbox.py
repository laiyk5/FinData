from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dataset_specs import plan_requests, request_key, summarize_request_plan
from .jsonio import read_json, write_json


@dataclass(frozen=True)
class RunContext:
    repo_root: Path
    dataset_name: str
    run_id: str

    @property
    def dataset_root(self) -> Path:
        return self.repo_root / "datasets" / self.dataset_name

    @property
    def sandbox_root(self) -> Path:
        return self.repo_root / "sandboxes" / "runs" / self.dataset_name / self.run_id

    @property
    def sandbox_dataset_root(self) -> Path:
        return self.sandbox_root / "dataset"

    @property
    def raw_root(self) -> Path:
        return self.sandbox_root / "raw"

    @property
    def qa_root(self) -> Path:
        return self.sandbox_root / "qa"

    @property
    def log_root(self) -> Path:
        return self.sandbox_root / "logs"

    @property
    def prepare_event_log_path(self) -> Path:
        return self.log_root / "prepare_events.jsonl"

    @property
    def prepare_summary_path(self) -> Path:
        return self.log_root / "prepare_summary.json"

    @property
    def run_manifest_path(self) -> Path:
        return self.sandbox_root / "run_manifest.json"

    @property
    def prepare_ledger_path(self) -> Path:
        return self.sandbox_root / "prepare_ledger.json"

    @property
    def accepted_missingness_path(self) -> Path:
        return self.sandbox_root / "accepted_missingness.json"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_run_id(dataset_name: str) -> str:
    return f"{utc_stamp()}-{dataset_name}"


def request_file_stem(trade_date: str, ts_code: str) -> str:
    return f"daily_{trade_date}_{ts_code.replace('.', '_')}"


def get_run_context(repo_root: Path, dataset_name: str, run_id: str) -> RunContext:
    return RunContext(repo_root=repo_root, dataset_name=dataset_name, run_id=run_id)


def load_run_manifest(context: RunContext) -> dict[str, Any]:
    return read_json(context.run_manifest_path)


def load_prepare_ledger(context: RunContext) -> dict[str, Any]:
    return read_json(context.prepare_ledger_path)


def save_prepare_ledger(context: RunContext, ledger: dict[str, Any]) -> None:
    write_json(context.prepare_ledger_path, ledger)


def active_dataset_ignore(source_dir: str, names: list[str]) -> set[str]:
    ignored = {"__pycache__"}
    source_path = Path(source_dir)
    if source_path.name == "data" and "archive" in names:
        ignored.add("archive")
    return ignored.intersection(names)


def inspect_current_state(dataset_root: Path) -> dict[str, Any]:
    current_dir = dataset_root / "data" / "published" / "current"
    staged_dir = dataset_root / "data" / "staged"
    raw_dir = dataset_root / "data" / "raw"

    return {
        "dataset_root": str(dataset_root),
        "raw_file_count": count_files(raw_dir),
        "staged_file_count": count_files(staged_dir),
        "published_file_count": count_files(current_dir),
        "published_row_count": count_csv_rows(current_dir),
    }


def count_files(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file())


def count_csv_rows(root: Path) -> int:
    if not root.exists():
        return 0
    row_count = 0
    for csv_path in root.rglob("*.csv"):
        with csv_path.open(newline="", encoding="utf-8") as input_file:
            row_count += sum(1 for _ in csv.DictReader(input_file))
    return row_count


def create_run_sandbox(
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
) -> RunContext:
    if dataset_name not in {"tushare_daily", "trade_calendar", "instrument_universe", "report_catalog"}:
        raise ValueError(f"Maintenance pipeline is not implemented for dataset: {dataset_name}")
    if provider not in {"fake", "mock", "tushare", "cninfo"}:
        raise ValueError(f"Unsupported provider: {provider}")
    extras = extras or {}
    if dataset_name == "tushare_daily" and not symbols:
        raise ValueError("At least one symbol is required.")
    if dataset_name == "tushare_daily" and not trade_dates and not extras.get("start_date"):
        raise ValueError("At least one trade date is required.")
    if dataset_name == "trade_calendar":
        for key in ("exchange", "start_date", "end_date"):
            if not extras.get(key):
                raise ValueError(f"{key} is required for trade_calendar.")
    if dataset_name == "instrument_universe":
        for key in ("universe_id", "source_id", "start_date", "end_date"):
            if not extras.get(key):
                raise ValueError(f"{key} is required for instrument_universe.")
    if dataset_name == "report_catalog":
        for key in ("universe_id", "start_year", "end_year", "report_types"):
            if not extras.get(key):
                raise ValueError(f"{key} is required for report_catalog.")
        if not symbols:
            raise ValueError("At least one symbol is required for report_catalog.")

    context = RunContext(repo_root=repo_root, dataset_name=dataset_name, run_id=run_id or default_run_id(dataset_name))
    if not context.dataset_root.is_dir():
        raise FileNotFoundError(f"Dataset not found: {context.dataset_root}")
    if context.sandbox_root.exists():
        raise FileExistsError(f"Run sandbox already exists: {context.sandbox_root}")

    context.sandbox_root.mkdir(parents=True)
    shutil.copytree(context.dataset_root, context.sandbox_dataset_root, ignore=active_dataset_ignore)
    context.raw_root.mkdir(parents=True, exist_ok=True)
    context.qa_root.mkdir(parents=True, exist_ok=True)
    context.log_root.mkdir(parents=True, exist_ok=True)

    requests: dict[str, dict[str, Any]] = {}
    planned_requests = plan_requests(dataset_name, symbols, trade_dates, extras)
    for planned in planned_requests:
        key = request_key(dataset_name, planned)
        requests[key] = {
            "key": key,
            **planned,
            "status": "pending",
            "attempts": 0,
            "attempt_history": [],
            "raw_path": None,
            "row_count": None,
            "last_error": None,
            "error_type": None,
            "updated_at": None,
        }

    manifest = {
        "run_id": context.run_id,
        "dataset": dataset_name,
        "provider": provider,
        "created_at": utc_stamp(),
        "source_dataset_path": str(context.dataset_root),
        "sandbox_dataset_path": str(context.sandbox_dataset_root),
        "symbols": symbols,
        "trade_dates": trade_dates,
        "daily_request": extras if dataset_name == "tushare_daily" and extras.get("start_date") else None,
        "calendar_request": extras if dataset_name == "trade_calendar" else None,
        "universe_request": extras if dataset_name == "instrument_universe" else None,
        "report_catalog_request": extras if dataset_name == "report_catalog" else None,
        "symbol_selector": extras.get("symbol_selector") if dataset_name in {"tushare_daily", "report_catalog"} else None,
        "symbol_selector_resolved_at": extras.get("symbol_selector_resolved_at")
        if dataset_name in {"tushare_daily", "report_catalog"}
        else None,
        "resolved_symbols": symbols
        if dataset_name in {"tushare_daily", "report_catalog"} and extras.get("symbol_selector")
        else None,
        "request_settings": {
            "rate_limit_seconds": rate_limit_seconds,
            "max_retries": max_retries,
            "retry_backoff_seconds": retry_backoff_seconds,
            "jitter_seconds": jitter_seconds,
            "request_budget": request_budget,
        },
        "request_plan": summarize_request_plan(dataset_name, planned_requests),
        "current_state": inspect_current_state(context.dataset_root),
        "steps": {
            "planned": True,
            "prepared": False,
            "ingested": False,
            "qa_passed": False,
            "published": False,
        },
    }

    ledger = {
        "run_id": context.run_id,
        "dataset": dataset_name,
        "provider": provider,
        "created_at": manifest["created_at"],
        "requests": requests,
    }

    write_json(context.run_manifest_path, manifest)
    write_json(context.prepare_ledger_path, ledger)
    write_json(context.accepted_missingness_path, {"accepted": []})
    return context


def mark_step(context: RunContext, step: str, value: bool = True) -> None:
    manifest = load_run_manifest(context)
    manifest.setdefault("steps", {})[step] = value
    manifest["updated_at"] = utc_stamp()
    write_json(context.run_manifest_path, manifest)
