"""Operation engine and runtime for the findata/tushare/trade_cal dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

from findata.contracts import DateRange, OperandError, OperationReporter
from findata.storage import DataMutation, Workspace
from findata_tushare_provider.engine import (
    _OPERAND_HELP,
    _batch_due,
    _format_range,
    _merge_interval,
    _missing_for_continuity,
    _require_keys,
    _require_no_operands,
    _string_array,
    _timerange,
    OperationWorker,
    TushareDatasetService,
    dataset_storage_state,
)
from findata_tushare_provider.provider import TushareClient
from findata_tushare_trade_cal import TRADE_CAL_SPEC


class TradeCalDatasetService(TushareDatasetService):
    """Synchronous operation engine for the findata/tushare/trade_cal dataset."""

    spec = TRADE_CAL_SPEC

    def _plan(self, operation: str, values: dict[str, Any]) -> None:
        plan_operation(self.workspace, operation, values, today=self.today)

    def _dispatch(self, operation: str, values: dict[str, Any]) -> str:
        return self._trade_cal(operation, values)

    def _trade_cal(self, operation: str, operands: dict[str, Any]) -> str:
        if operation == "update":
            _require_no_operands(operands)
            requested = DateRange(self.today, self.today + timedelta(days=1))
            exchanges = ["SSE", "SZSE"]
        elif operation == "complete":
            exchanges = _string_array(operands, "exchanges")
            if set(exchanges) - {"SSE", "SZSE"}:
                raise OperandError("trade calendar supports only SSE and SZSE")
            requested = _timerange(operands, today=self.today)
            _require_keys(operands, {"exchanges", "timerange"})
        else:
            raise OperandError(f"unsupported trade calendar operation {operation!r}")

        spec = self.spec
        existing_coverage = self._coverage_map(spec.name)
        next_coverage = dict(existing_coverage)
        publication: str | None = None
        mutations: list[DataMutation] = []
        batch_started = monotonic()
        jobs = [
            (exchange, interval)
            for exchange in exchanges
            for interval in _missing_for_continuity(existing_coverage.get(exchange), requested)
        ]
        self._log(
            f"plan: {len(exchanges)} exchanges over "
            f"{requested.start.isoformat()}..{requested.end.isoformat()}"
        )
        self._progress(0, len(jobs))
        for completed, (exchange, interval) in enumerate(jobs, start=1):
            start, end = interval.to_provider_inclusive()
            table = self._fetch(
                exchange=exchange,
                start_date=start,
                end_date=end,
            )
            if table.num_rows == 0:
                raise RuntimeError(f"trade_cal returned empty due interval for {exchange}")
            next_coverage[exchange] = _merge_interval(next_coverage.get(exchange), interval)
            mutations.append(
                DataMutation.replace_range(
                    table,
                    partition=exchange,
                    start=interval.start,
                    end=interval.end,
                )
            )
            if _batch_due(
                mutations,
                batch_started,
                request_limit=self.client.checkpoint_request_limit or 64,
            ):
                publication = self._commit_mutations(spec, mutations, next_coverage)
                mutations = []
                batch_started = monotonic()
            self._progress(completed, len(jobs))
        if mutations:
            publication = self._commit_mutations(spec, mutations, next_coverage)
            self._progress(len(jobs), len(jobs))
        return publication or self.loader.dataset(spec.name).publication_id


@dataclass(frozen=True, slots=True)
class TradeCalOperationWorker(OperationWorker):
    """Pickle-safe task-process worker for the trade calendar dataset."""

    def service(
        self,
        workspace: Workspace,
        client: TushareClient,
        *,
        today: date,
        now: datetime,
        reporter: OperationReporter,
        settings: dict[str, Any],
    ) -> TradeCalDatasetService:
        return TradeCalDatasetService(
            workspace,
            client,
            today=today,
            now=now,
            reporter=reporter,
            settings=settings,
        )


@dataclass(frozen=True, slots=True)
class TradeCalDatasetRuntime:
    """Dataset-scoped behavior for the Tushare trade calendar plugin."""

    def operation_worker(
        self,
        workspace: Path,
        *,
        mode: str,
        today: date,
        now: datetime | None,
    ) -> TradeCalOperationWorker:
        return TradeCalOperationWorker(
            workspace=workspace,
            provider=mode,
            token="mock-token" if mode == "mock" else "",
            today=today.isoformat(),
            now=now.isoformat() if now is not None else None,
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
            f"dataset {TRADE_CAL_SPEC.name!r} has no declared dependency on {target!r}"
        )

    def update_ready(self, workspace: Workspace) -> bool:
        return True


_OPERATION_NAMES = ["update", "complete"]

_OPERATION_HELP: dict[str, str] = {
    "update": "Extend both SSE and SZSE calendars through tomorrow; parameterless.",
    "complete": "Fetch the requested historical civil-date range for the selected exchanges.",
}


def normalize_operation(
    operation: str,
    operands: dict[str, Any],
    *,
    today: date,
) -> dict[str, Any]:
    values = dict(operands)
    if operation not in _OPERATION_NAMES:
        raise OperandError(f"unsupported operation {operation!r} for {TRADE_CAL_SPEC.name}")
    if operation == "update":
        _require_no_operands(values)
        return {}
    timerange = _timerange(values, today=today)
    if timerange.end > today + timedelta(days=1):
        raise OperandError("future trade-calendar intervals cannot be completed")
    arrays = sorted(_string_array(values, "exchanges"))
    if set(arrays) - {"SSE", "SZSE"}:
        raise OperandError("trade calendar supports only SSE and SZSE")
    _require_keys(values, {"exchanges", "timerange"})
    return {"exchanges": arrays, "timerange": _format_range(timerange)}


def dataset_description(workspace: Workspace, *, provider_ready: bool) -> dict[str, Any]:
    state, publication_id = dataset_storage_state(workspace, TRADE_CAL_SPEC.name)
    return {
        "name": TRADE_CAL_SPEC.name,
        "provider": "tushare",
        "provider_ready": provider_ready,
        "capabilities": dict(TRADE_CAL_SPEC.capabilities),
        "dependencies": [],
        "settings": [],
        "storage": "duckdb",
        "state": state,
        "publication_id": publication_id,
        "operations": [operation_description(name) for name in _OPERATION_NAMES],
    }


def operation_description(operation: str) -> dict[str, Any]:
    if operation not in _OPERATION_NAMES:
        raise OperandError(f"unsupported operation {operation!r} for {TRADE_CAL_SPEC.name}")
    help_text = _OPERATION_HELP[operation]
    if operation == "update":
        return {"name": operation, "help": help_text, "required": [], "properties": {}}
    return {
        "name": operation,
        "help": help_text,
        "required": ["exchanges", "timerange"],
        "properties": {
            "exchanges": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "help": _OPERAND_HELP["exchanges"],
            },
            "timerange": {
                "type": "string",
                "format": "half-open-date-range",
                "help": _OPERAND_HELP["timerange"],
            },
        },
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
    else:
        strategy = "one request per exchange"
        estimated_requests = len(normalized["exchanges"])

    return {
        "dry_run": True,
        "dataset": TRADE_CAL_SPEC.name,
        "operation": operation,
        "operands": normalized,
        "strategy": strategy,
        "estimated_provider_requests": estimated_requests,
        "dependencies": dependencies,
        "side_effects": False,
    }
