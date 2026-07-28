"""Operations for the Tushare index_daily dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from findata.sdk.contracts import DateRange, OperandError, OperationReporter
from findata.sdk.loader import DataLoaderError, DatasetNotReadyError
from findata.storage import DataMutation, Workspace
from findata_plugins.tushare.plugins.datasets.index.index_daily import INDEX_DAILY_SPEC
from findata_plugins.tushare.shared.engine import (
    _OPERAND_HELP, _canonical_index, _format_range, _merge_interval,
    _missing_for_continuity, _normalize_index_reference, _require_keys,
    _require_no_operands, _string_array, _timerange, OperationWorker,
    TushareClient, TushareDatasetService, dataset_storage_state,
)
from findata_plugins.tushare.shared.publication import PublicationWindow, daily_window


class IndexDailyDatasetService(TushareDatasetService):
    spec = INDEX_DAILY_SPEC

    def _plan(self, operation: str, values: dict[str, Any]) -> None:
        plan_operation(self.workspace, operation, values, today=self.today)

    def _dispatch(self, operation: str, values: dict[str, Any]) -> str | None:
        if operation == "update":
            _require_no_operands(values)
            indexes = self._update_indexes()
            if not indexes:
                self._log("no stored indexes; index_daily update has no work")
                return None
            requested = DateRange(self.today, self.today + timedelta(days=1))
        elif operation in {"complete", "refresh"}:
            indexes = _string_array(values, "indexes")
            requested = _timerange(values, today=self.today)
            _require_keys(values, {"indexes", "timerange"})
        else:
            raise OperandError(f"unsupported index daily operation {operation!r}")
        if daily_window(self.today, self.now) == PublicationWindow.BEFORE:
            requested = DateRange(requested.start, min(requested.end, self.today))
        if requested.end <= requested.start:
            return None
        self._fulfill("findata-plugins/tushare_trade_cal", {"exchanges": ["SSE", "SZSE"], "timerange": _format_range(requested)})
        self._ensure_index_metadata(indexes)
        coverage = self._coverage_map(self.spec.name)
        mutations: list[DataMutation] = []
        next_coverage = dict(coverage)
        self._progress(0, len(indexes))
        for position, reference in enumerate(indexes, start=1):
            code = _canonical_index(reference)
            if operation == "refresh":
                covered = coverage.get(code)
                if covered is None or requested.start < covered.start or requested.end > covered.end:
                    raise OperandError(f"refresh range is outside coverage for {reference}")
                intervals = [requested]
            else:
                intervals = _missing_for_continuity(coverage.get(code), requested)
            for interval in intervals:
                start, end = interval.to_provider_inclusive()
                table = self._fetch(ts_code=code, start_date=start, end_date=end)
                if table.num_rows == 0:
                    self._require_resolvable_empty(interval.end - timedelta(days=1))
                next_coverage[code] = _merge_interval(next_coverage.get(code), interval)
                mutations.append(DataMutation.replace_range(table, partition=code, start=interval.start, end=interval.end))
            self._progress(position, len(indexes))
        if not mutations:
            try:
                return self.loader.dataset(self.spec.name).publication_id
            except DataLoaderError:
                return None
        return self._commit_mutations(self.spec, mutations, next_coverage)

    def _update_indexes(self) -> list[str]:
        selectors = self._update_setting(self.spec.name) or ["stored"]
        coverage = self._coverage_map(self.spec.name)
        return sorted({_canonical_index(item) for item in selectors if item != "stored"} | (set(coverage) if "stored" in selectors else set()))


@dataclass(frozen=True, slots=True)
class IndexDailyOperationWorker(OperationWorker):
    def service(self, workspace: Workspace, client: TushareClient, *, today: date, now: datetime, reporter: OperationReporter, settings: dict[str, Any]) -> IndexDailyDatasetService:
        return IndexDailyDatasetService(workspace, client, today=today, now=now, reporter=reporter, settings=settings)


@dataclass(frozen=True, slots=True)
class IndexDailyDatasetRuntime:
    def operation_worker(self, workspace: Path, *, mode: str, today: date, now: datetime | None) -> IndexDailyOperationWorker:
        return IndexDailyOperationWorker(workspace=workspace, provider=mode, token="mock-token" if mode == "mock" else "", today=today.isoformat(), now=now.isoformat() if now else None)
    def operation_service(self, workspace: Workspace, client: TushareClient, *, today: date, now: datetime, settings: dict[str, Any] | None) -> IndexDailyDatasetService:
        return IndexDailyDatasetService(workspace, client, today=today, now=now, settings=settings)
    def normalize_operation(self, operation: str, operands: dict[str, Any], *, today: date) -> dict[str, Any]: return normalize_operation(operation, operands, today=today)
    def plan_operation(self, workspace: Workspace, operation: str, operands: dict[str, Any], *, today: date) -> dict[str, Any]: return plan_operation(workspace, operation, operands, today=today)
    def dataset_description(self, workspace: Workspace, *, provider_ready: bool) -> dict[str, Any]: return dataset_description(workspace, provider_ready=provider_ready)
    def operation_description(self, operation: str) -> dict[str, Any]: return operation_description(operation)
    def resolve_dependency(self, target: str, requirement: dict[str, object]) -> tuple[str, dict[str, object]]: raise ValueError(f"dataset {INDEX_DAILY_SPEC.name!r} has no declared dependency on {target!r}")
    def update_ready(self, workspace: Workspace) -> bool: return True


def normalize_operation(operation: str, operands: dict[str, Any], *, today: date) -> dict[str, Any]:
    values = dict(operands)
    if operation == "update": _require_no_operands(values); return {}
    if operation not in {"complete", "refresh"}: raise OperandError(f"unsupported operation {operation!r} for {INDEX_DAILY_SPEC.name}")
    indexes = sorted({_normalize_index_reference(item) for item in _string_array(values, "indexes")})
    _require_keys(values, {"indexes", "timerange"})
    return {"indexes": indexes, "timerange": _format_range(_timerange(values, today=today))}


def operation_description(operation: str) -> dict[str, Any]:
    if operation == "update": return {"name": "update", "help": "Update locally covered indexes.", "required": [], "properties": {}}
    if operation in {"complete", "refresh"}: return {"name": operation, "help": "Fetch an explicit index date range.", "required": ["indexes", "timerange"], "properties": {"indexes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "help": _OPERAND_HELP["indexes"]}, "timerange": {"type": "string", "format": "timerange", "help": _OPERAND_HELP["timerange"]}}}
    raise OperandError(f"unsupported operation {operation!r} for {INDEX_DAILY_SPEC.name}")


def dataset_description(workspace: Workspace, *, provider_ready: bool) -> dict[str, Any]:
    state, publication_id = dataset_storage_state(workspace, INDEX_DAILY_SPEC.name)
    return {"name": INDEX_DAILY_SPEC.name, "provider": "findata-plugins/tushare", "provider_ready": provider_ready, "capabilities": dict(INDEX_DAILY_SPEC.capabilities), "dependencies": ["findata-plugins/tushare_trade_cal", "findata-plugins/tushare_index_basic"], "settings": [], "storage": "duckdb", "state": state, "publication_id": publication_id, "operations": [operation_description(item) for item in ("update", "complete", "refresh")]}


def plan_operation(workspace: Workspace, operation: str, operands: dict[str, Any], *, today: date) -> dict[str, Any]:
    normalized = normalize_operation(operation, operands, today=today)
    return {"dry_run": True, "dataset": INDEX_DAILY_SPEC.name, "operation": operation, "operands": normalized, "strategy": "one request per index range" if operation != "update" else "coverage-backed update", "estimated_provider_requests": len(normalized.get("indexes", [])) or None, "dependencies": [], "side_effects": False}
