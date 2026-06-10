from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path
from typing import Any
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .workspace_config import WorkspaceLayout

from .dataset_specs import plan_requests, request_key, summarize_request_plan
from .jsonio import read_json, write_json
from .workspace import (
    cache_root as workspace_cache_root,
    dataset_backup_root,
    dataset_current_root,
    dataset_root as workspace_dataset_root,
    sandbox_dataset_current_root,
    sandbox_dataset_root as workspace_sandbox_dataset_root,
    sandbox_dataset_staged_root,
)


@dataclass(frozen=True)
class RunContext:
    repo_root: Path
    dataset_name: str
    provider: str
    api_name: str
    run_id: str

    @property
    def path_key(self) -> str:
        return f"{self.provider}/{self.api_name}"

    @cached_property
    def layout(self) -> WorkspaceLayout:
        from .workspace_config import load_layout

        return load_layout(self.repo_root)

    @property
    def dataset_root(self) -> Path:
        return workspace_dataset_root(self.repo_root, self.dataset_name, self.layout)

    @property
    def published_current_root(self) -> Path:
        return dataset_current_root(self.repo_root, self.dataset_name, self.layout)

    @property
    def backup_root(self) -> Path:
        return dataset_backup_root(self.repo_root, self.dataset_name, self.layout)

    @property
    def cache_root(self) -> Path:
        return workspace_cache_root(self.repo_root, self.layout)

    @property
    def sandbox_root(self) -> Path:
        return self.layout.sandboxes_root / self.dataset_name / self.run_id

    @property
    def sandbox_dataset_root(self) -> Path:
        return workspace_sandbox_dataset_root(self.sandbox_root)

    @property
    def sandbox_published_current_root(self) -> Path:
        return sandbox_dataset_current_root(self.sandbox_root)

    @property
    def sandbox_staged_root(self) -> Path:
        return sandbox_dataset_staged_root(self.sandbox_root)

    @property
    def raw_root(self) -> Path:
        return self.sandbox_root / "raw"

    @property
    def cache_dataset_root(self) -> Path:
        return self.cache_root / self.provider / self.api_name

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
    from .dataset_specs import get_spec

    spec = get_spec(dataset_name)
    return RunContext(
        repo_root=repo_root,
        dataset_name=dataset_name,
        provider=spec.provider,
        api_name=spec.api_name,
        run_id=run_id,
    )


def load_run_manifest(context: RunContext) -> dict[str, Any]:
    return read_json(context.run_manifest_path)


def load_prepare_ledger(context: RunContext) -> dict[str, Any]:
    return read_json(context.prepare_ledger_path)


def save_prepare_ledger(context: RunContext, ledger: dict[str, Any]) -> None:
    write_json(context.prepare_ledger_path, ledger)


def active_dataset_ignore(source_dir: str, names: list[str]) -> set[str]:
    ignored = {"__pycache__", "data", "checks", "logs", "raw", "staged", "archive"}
    return ignored.intersection(names)


def inspect_current_state(repo_root: Path, dataset_name: str, layout: WorkspaceLayout | None = None) -> dict[str, Any]:
    if layout is None:
        from .workspace_config import load_layout
        layout = load_layout(repo_root)
    current_dir = dataset_current_root(repo_root, dataset_name, layout)
    backup_dir = dataset_backup_root(repo_root, dataset_name, layout)

    return {
        "dataset_root": str(workspace_dataset_root(repo_root, dataset_name, layout)),
        "raw_file_count": 0,
        "staged_file_count": 0,
        "published_file_count": count_files(current_dir),
        "published_row_count": count_csv_rows(current_dir),
        "backup_file_count": count_files(backup_dir),
        "backup_version_count": count_directories(backup_dir),
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


def count_directories(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(1 for path in root.iterdir() if path.is_dir())


def create_run_sandbox(
    repo_root: Path,
    dataset_name: str,
    use_fake: bool = False,
    symbols: list[str] | None = None,
    trade_dates: list[str] | None = None,
    run_id: str | None = None,
    rate_limit_seconds: float = 0.0,
    max_retries: int = 3,
    retry_backoff_seconds: float = 0.0,
    jitter_seconds: str | None = None,
    request_budget: int | None = None,
    extras: dict[str, Any] | None = None,
) -> RunContext:
    from .dataset_specs import get_spec

    spec = get_spec(dataset_name)
    provider = "fake" if use_fake else spec.provider
    if dataset_name not in {
        "tushare_daily",
        "tushare_daily_basic",
        "tushare_stk_factor_pro",
        "tushare_adj_factor",
        "tushare_moneyflow",
        "tushare_index_weight",
        "trade_calendar",
        "report_catalog",
    }:
        raise ValueError(f"Maintenance pipeline is not implemented for dataset: {dataset_name}")
    if provider not in {"fake", "mock", "tushare", "cninfo"}:
        raise ValueError(f"Unsupported provider: {provider}")
    symbols = symbols or []
    trade_dates = trade_dates or []
    extras = extras or {}
    if dataset_name in {"tushare_daily", "tushare_daily_basic", "tushare_stk_factor_pro", "tushare_adj_factor", "tushare_moneyflow"} and not symbols:
        raise ValueError("At least one symbol is required.")
    if dataset_name in {"tushare_daily", "tushare_daily_basic", "tushare_stk_factor_pro", "tushare_moneyflow"} and not trade_dates and not extras.get("start_date"):
        raise ValueError("At least one trade date is required.")
    if dataset_name == "trade_calendar":
        for key in ("exchange", "start_date", "end_date"):
            if not extras.get(key):
                raise ValueError(f"{key} is required for trade_calendar.")
    if dataset_name == "tushare_index_weight":
        for key in ("index_code", "start_date", "end_date"):
            if not extras.get(key):
                raise ValueError(f"{key} is required for tushare_index_weight.")
    if dataset_name == "report_catalog":
        for key in ("universe_id", "start_year", "end_year", "report_types"):
            if not extras.get(key):
                raise ValueError(f"{key} is required for report_catalog.")
        if not symbols:
            raise ValueError("At least one symbol is required for report_catalog.")

    context = RunContext(
        repo_root=repo_root,
        dataset_name=dataset_name,
        provider=spec.provider,
        api_name=spec.api_name,
        run_id=run_id or default_run_id(dataset_name),
    )
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
            "cache_path": None,
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
        "daily_request": extras if dataset_name in {"tushare_daily", "tushare_daily_basic", "tushare_moneyflow"} and extras.get("start_date") else None,
        "factor_request": extras if dataset_name == "tushare_stk_factor_pro" and extras.get("start_date") else None,
        "calendar_request": extras if dataset_name == "trade_calendar" else None,
        "index_weight_request": extras if dataset_name == "tushare_index_weight" else None,
        "report_catalog_request": extras if dataset_name == "report_catalog" else None,
        "symbol_selector": extras.get("symbol_selector")
        if dataset_name in {"tushare_daily", "tushare_daily_basic", "tushare_stk_factor_pro", "tushare_moneyflow", "report_catalog"}
        else None,
        "symbol_selector_resolved_at": extras.get("symbol_selector_resolved_at")
        if dataset_name in {"tushare_daily", "tushare_daily_basic", "tushare_stk_factor_pro", "tushare_moneyflow", "report_catalog"}
        else None,
        "resolved_symbols": symbols
        if dataset_name in {"tushare_daily", "tushare_daily_basic", "tushare_stk_factor_pro", "tushare_moneyflow", "report_catalog"} and extras.get("symbol_selector")
        else None,
        "request_settings": {
            "rate_limit_seconds": rate_limit_seconds,
            "max_retries": max_retries,
            "retry_backoff_seconds": retry_backoff_seconds,
            "jitter_seconds": jitter_seconds,
            "request_budget": request_budget,
        },
        "request_plan": summarize_request_plan(dataset_name, planned_requests),
        "current_state": inspect_current_state(repo_root, dataset_name),
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
