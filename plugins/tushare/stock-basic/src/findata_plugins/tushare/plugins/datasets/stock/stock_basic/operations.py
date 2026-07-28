"""Operation engine and runtime for the findata-plugins/tushare_stock_basic dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa

from findata.sdk.contracts import OperandError, OperationReporter
from findata.storage import Workspace
from findata_plugins.tushare.shared.engine import (
    _merge_tables,
    _require_no_operands,
    OperationWorker,
    TushareClient,
    TushareDatasetService,
    dataset_storage_state,
)
from findata_plugins.tushare.plugins.datasets.stock.stock_basic import STOCK_BASIC_SPEC


class StockBasicDatasetService(TushareDatasetService):
    """Synchronous operation engine for the findata-plugins/tushare_stock_basic dataset."""

    spec = STOCK_BASIC_SPEC

    def _plan(self, operation: str, values: dict[str, Any]) -> None:
        plan_operation(self.workspace, operation, values, today=self.today)

    def _dispatch(self, operation: str, values: dict[str, Any]) -> str:
        return self._stock_basic(operation, values)

    def _stock_basic(self, operation: str, operands: dict[str, Any]) -> str:
        if operation != "update":
            raise OperandError("findata-plugins/tushare_stock_basic supports only update")
        _require_no_operands(operands)
        tables: list[pa.Table] = []
        jobs = [
            (status, exchange)
            for status in ("L", "D", "P", "G")
            for exchange in ("SSE", "SZSE", "BSE")
        ]
        statuses = dict.fromkeys(status for status, _ in jobs)
        exchanges = dict.fromkeys(exchange for _, exchange in jobs)
        self._log(
            f"plan: {len(statuses)} statuses × {len(exchanges)} exchanges = {len(jobs)} "
            "requests (complete-table replacement)"
        )
        self._progress(0, len(jobs))
        for completed, (status, exchange) in enumerate(jobs, start=1):
            table = self._fetch(list_status=status, exchange=exchange)
            if table.num_rows >= 6000:
                raise RuntimeError(f"stock_basic response may be truncated for {status}/{exchange}")
            tables.append(table)
            self._progress(completed, len(jobs))
        combined = pa.concat_tables(tables)
        if combined.num_rows == 0:
            raise RuntimeError("stock_basic merged snapshot is unexpectedly empty")
        if self._reporter is not None and hasattr(self._reporter, "stage"):
            self._reporter.stage(f"committing:{self.spec.name}")
        return self._publisher(self.spec.name).publish(_merge_tables(self.spec, None, combined))


@dataclass(frozen=True, slots=True)
class StockBasicOperationWorker(OperationWorker):
    """Pickle-safe task-process worker for the stock basic dataset."""

    def service(
        self,
        workspace: Workspace,
        client: TushareClient,
        *,
        today: date,
        now: datetime,
        reporter: OperationReporter,
        settings: dict[str, Any],
    ) -> StockBasicDatasetService:
        return StockBasicDatasetService(
            workspace,
            client,
            today=today,
            now=now,
            reporter=reporter,
            settings=settings,
        )


@dataclass(frozen=True, slots=True)
class StockBasicDatasetRuntime:
    """Dataset-scoped behavior for the Tushare stock basic plugin."""

    def operation_worker(
        self,
        workspace: Path,
        *,
        mode: str,
        today: date,
        now: datetime | None,
    ) -> StockBasicOperationWorker:
        return StockBasicOperationWorker(
            workspace=workspace,
            provider=mode,
            token="mock-token" if mode == "mock" else "",
            today=today.isoformat(),
            now=now.isoformat() if now is not None else None,
        )

    def operation_service(
        self,
        workspace: Workspace,
        client: TushareClient,
        *,
        today: date,
        now: datetime,
        settings: dict[str, Any] | None,
    ) -> StockBasicDatasetService:
        return StockBasicDatasetService(
            workspace,
            client,
            today=today,
            now=now,
            settings=settings,
        )

    def normalize_operation(
        self,
        operation: str,
        operands: dict[str, Any],
        *,
        today: date,
    ) -> dict[str, Any]:
        return normalize_operation(operation, operands, today=today)

    def plan_operation(
        self,
        workspace: Workspace,
        operation: str,
        operands: dict[str, Any],
        *,
        today: date,
    ) -> dict[str, Any]:
        return plan_operation(workspace, operation, operands, today=today)

    def dataset_description(
        self,
        workspace: Workspace,
        *,
        provider_ready: bool,
    ) -> dict[str, Any]:
        return dataset_description(workspace, provider_ready=provider_ready)

    def operation_description(self, operation: str) -> dict[str, Any]:
        return operation_description(operation)

    def resolve_dependency(
        self,
        target: str,
        requirement: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        raise ValueError(
            f"dataset {STOCK_BASIC_SPEC.name!r} has no declared dependency on {target!r}"
        )

    def update_ready(self, workspace: Workspace) -> bool:
        return True


_OPERATION_NAMES = ["update"]

_OPERATION_HELP: dict[str, str] = {
    "update": "Fetch the complete A-share security table and replace the committed snapshot; "
    "parameterless.",
}


def normalize_operation(
    operation: str,
    operands: dict[str, Any],
    *,
    today: date,
) -> dict[str, Any]:
    values = dict(operands)
    if operation not in _OPERATION_NAMES:
        raise OperandError(f"unsupported operation {operation!r} for {STOCK_BASIC_SPEC.name}")
    _require_no_operands(values)
    return {}


def dataset_description(workspace: Workspace, *, provider_ready: bool) -> dict[str, Any]:
    state, publication_id = dataset_storage_state(workspace, STOCK_BASIC_SPEC.name)
    return {
        "name": STOCK_BASIC_SPEC.name,
        "provider": "findata-plugins/tushare",
        "provider_ready": provider_ready,
        "capabilities": dict(STOCK_BASIC_SPEC.capabilities),
        "dependencies": [],
        "settings": [],
        "storage": "duckdb",
        "state": state,
        "publication_id": publication_id,
        "operations": [operation_description(name) for name in _OPERATION_NAMES],
    }


def operation_description(operation: str) -> dict[str, Any]:
    if operation not in _OPERATION_NAMES:
        raise OperandError(f"unsupported operation {operation!r} for {STOCK_BASIC_SPEC.name}")
    return {
        "name": operation,
        "help": _OPERATION_HELP[operation],
        "required": [],
        "properties": {},
    }


def plan_operation(
    workspace: Workspace,
    operation: str,
    operands: dict[str, Any],
    *,
    today: date,
) -> dict[str, Any]:
    """Build a read-only preview from normalized operands and committed local state."""
    normalized = normalize_operation(operation, operands, today=today)
    dependencies: list[dict[str, Any]] = []
    strategy = "plugin operation"
    estimated_requests: int | None = None
    if operation == "update":
        strategy = "configured update"

    return {
        "dry_run": True,
        "dataset": STOCK_BASIC_SPEC.name,
        "operation": operation,
        "operands": normalized,
        "strategy": strategy,
        "estimated_provider_requests": estimated_requests,
        "dependencies": dependencies,
        "side_effects": False,
    }
