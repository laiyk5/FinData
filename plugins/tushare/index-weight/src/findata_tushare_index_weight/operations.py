"""Operation engine and runtime for the findata/tushare/index_weight dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from findata.contracts import OperandError, OperationReporter
from findata.storage import DataMutation, Workspace
from findata_tushare_index_basic.operations import (
    IndexBasicDatasetService,
    dataset_description as index_basic_dataset_description,
)
from findata_tushare_provider.engine import (
    _OPERAND_HELP,
    _batch_due,
    _canonical_index,
    _expand_to_months,
    _format_range,
    _merge_interval,
    _missing_for_continuity,
    _month_range,
    _month_starts,
    _normalize_index_reference,
    _require_keys,
    _require_no_operands,
    _string_array,
    _timerange,
    OperationWorker,
    TushareDatasetService,
    dataset_storage_state,
)
from findata_tushare_provider.provider import TushareClient
from findata_tushare_provider.publication import PublicationWindow, monthly_window
from findata_tushare_index_weight import INDEX_WEIGHT_SPEC


class IndexWeightDatasetService(TushareDatasetService):
    """Synchronous operation engine for the findata/tushare/index_weight dataset."""

    spec = INDEX_WEIGHT_SPEC

    def _plan(self, operation: str, values: dict[str, Any]) -> None:
        plan_operation(self.workspace, operation, values, today=self.today)

    def _dispatch(self, operation: str, values: dict[str, Any]) -> str:
        return self._index_weight(operation, values)

    def _dependency_service(self, dataset: str) -> TushareDatasetService:
        return {
            "findata/tushare/index_basic": IndexBasicDatasetService,
        }[dataset](
            self.workspace,
            self.client,
            today=self.today,
            now=self.now,
            settings=self._settings,
        )

    def _index_weight(self, operation: str, operands: dict[str, Any]) -> str:
        if operation == "update":
            _require_no_operands(operands)
            references = self._update_setting(self.spec.name)
            if not references:
                raise OperandError("findata/tushare/index_weight update requires update_indexes")
            self._ensure_index_metadata(references)
            indexes = [_canonical_index(value) for value in references]
            requested = _month_range(self.today, self.today)
        elif operation == "complete":
            references = _string_array(operands, "indexes")
            self._ensure_index_metadata(references)
            indexes = [_canonical_index(value) for value in references]
            requested = _expand_to_months(_timerange(operands, today=self.today))
            _require_keys(operands, {"indexes", "timerange"})
        else:
            raise OperandError(f"unsupported index weight operation {operation!r}")

        spec = self.spec
        existing_coverage = self._coverage_map(spec.name)
        next_coverage = dict(existing_coverage)
        publication: str | None = None
        mutations: list[DataMutation] = []
        batch_started = monotonic()
        jobs: list[tuple[str, date]] = []
        current_month = self.today.replace(day=1)
        for index in indexes:
            months = {
                month
                for interval in _missing_for_continuity(existing_coverage.get(index), requested)
                for month in _month_starts(interval)
            }
            if requested.start <= current_month < requested.end:
                months.add(current_month)
            jobs.extend((index, month) for month in sorted(months))
        self._log(f"plan: {len(jobs)} index-months across {len(indexes)} indexes")
        self._progress(0, len(jobs))
        for completed, (index, month) in enumerate(jobs, start=1):
            month_interval = _month_range(month, month)
            start, end = month_interval.to_provider_inclusive()
            if monthly_window(month, self.now) == PublicationWindow.BEFORE:
                raise RuntimeError(f"index_weight month is before publication window for {index}")
            table = self._fetch(
                index_code=index,
                start_date=start,
                end_date=end,
            )
            next_coverage[index] = _merge_interval(next_coverage.get(index), month_interval)
            mutations.append(
                DataMutation.replace_range(
                    table,
                    partition=index,
                    start=month_interval.start,
                    end=month_interval.end,
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
class IndexWeightOperationWorker(OperationWorker):
    """Pickle-safe task-process worker for the index weight dataset."""

    def service(
        self,
        workspace: Workspace,
        client: TushareClient,
        *,
        today: date,
        now: datetime,
        reporter: OperationReporter,
        settings: dict[str, Any],
    ) -> IndexWeightDatasetService:
        return IndexWeightDatasetService(
            workspace,
            client,
            today=today,
            now=now,
            reporter=reporter,
            settings=settings,
        )


@dataclass(frozen=True, slots=True)
class IndexWeightDatasetRuntime:
    """Dataset-scoped behavior for the Tushare index weight plugin."""

    def operation_worker(
        self,
        workspace: Path,
        *,
        mode: str,
        today: date,
        now: datetime | None,
    ) -> IndexWeightOperationWorker:
        return IndexWeightOperationWorker(
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
        if target != "findata/tushare/index_basic":
            raise ValueError(
                f"dataset {INDEX_WEIGHT_SPEC.name!r} has no declared dependency on {target!r}"
            )
        return "complete", dict(requirement)

    def update_ready(self, workspace: Workspace) -> bool:
        return bool(workspace.get_config("dataset.findata/tushare/index_weight.update_indexes"))


_OPERATION_NAMES = ["update", "complete"]

_OPERATION_HELP: dict[str, str] = {
    "update": "Extend the configured indexes through the current month; requires "
    "dataset.findata/tushare/index_weight.update_indexes.",
    "complete": "Fetch every intersecting calendar month for the requested index references, "
    "extending continuous monthly coverage.",
}


def normalize_operation(
    operation: str,
    operands: dict[str, Any],
    *,
    today: date,
) -> dict[str, Any]:
    values = dict(operands)
    if operation not in _OPERATION_NAMES:
        raise OperandError(f"unsupported operation {operation!r} for {INDEX_WEIGHT_SPEC.name}")
    if operation == "update":
        _require_no_operands(values)
        return {}
    timerange = _timerange(values, today=today)
    arrays = sorted(
        {_normalize_index_reference(value) for value in _string_array(values, "indexes")}
    )
    _require_keys(values, {"indexes", "timerange"})
    return {"indexes": arrays, "timerange": _format_range(timerange)}


def dataset_description(workspace: Workspace, *, provider_ready: bool) -> dict[str, Any]:
    state, publication_id = dataset_storage_state(workspace, INDEX_WEIGHT_SPEC.name)
    return {
        "name": INDEX_WEIGHT_SPEC.name,
        "provider": "tushare",
        "provider_ready": provider_ready,
        "capabilities": dict(INDEX_WEIGHT_SPEC.capabilities),
        "dependencies": ["findata/tushare/index_basic"],
        "settings": _dataset_settings(workspace),
        "storage": "duckdb",
        "state": state,
        "publication_id": publication_id,
        "operations": [operation_description(name) for name in _OPERATION_NAMES],
    }


def operation_description(operation: str) -> dict[str, Any]:
    if operation not in _OPERATION_NAMES:
        raise OperandError(f"unsupported operation {operation!r} for {INDEX_WEIGHT_SPEC.name}")
    help_text = _OPERATION_HELP[operation]
    if operation == "update":
        return {"name": operation, "help": help_text, "required": [], "properties": {}}
    return {
        "name": operation,
        "help": help_text,
        "required": ["indexes", "timerange"],
        "properties": {
            "indexes": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "help": _OPERAND_HELP["indexes"],
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
    description = dataset_description(workspace, provider_ready=True)
    dependencies = [
        {
            "dataset": name,
            "state": index_basic_dataset_description(workspace, provider_ready=True)["state"],
        }
        for name in description["dependencies"]
    ]
    strategy = "plugin operation"
    estimated_requests: int | None = None
    if operation == "update":
        strategy = "configured update"
    else:
        strategy = "one request per uncovered index-month"

    return {
        "dry_run": True,
        "dataset": INDEX_WEIGHT_SPEC.name,
        "operation": operation,
        "operands": normalized,
        "strategy": strategy,
        "estimated_provider_requests": estimated_requests,
        "dependencies": dependencies,
        "side_effects": False,
    }


def _dataset_settings(workspace: Workspace) -> list[dict[str, Any]]:
    from findata_tushare_index_weight import index_weight_plugin

    return [
        {
            "key": key,
            "schema": dict(setting.schema),
            "help": setting.help,
            "required": setting.required,
            "configured": workspace.get_config(key) is not None,
        }
        for key, setting in index_weight_plugin().settings.items()
    ]
