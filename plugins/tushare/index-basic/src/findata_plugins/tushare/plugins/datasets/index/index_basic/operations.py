"""Operation engine and runtime for the findata-plugins/tushare_index_basic dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from findata.sdk.contracts import OperandError, OperationReporter
from findata.sdk.loader import DataLoader, DatasetNotReadyError
from findata.storage import DataMutation, Workspace
from findata_plugins.tushare.shared.engine import (
    _OPERAND_HELP,
    _batch_due,
    _canonical_index,
    _normalize_index_reference,
    _require_keys,
    _require_no_operands,
    _string_array,
    OperationWorker,
    TushareClient,
    TushareDatasetService,
    dataset_storage_state,
)
from findata_plugins.tushare.plugins.datasets.index.index_basic import INDEX_BASIC_SPEC


class IndexBasicDatasetService(TushareDatasetService):
    """Synchronous operation engine for the findata-plugins/tushare_index_basic dataset."""

    spec = INDEX_BASIC_SPEC

    def _plan(self, operation: str, values: dict[str, Any]) -> None:
        plan_operation(self.workspace, operation, values, today=self.today)

    def _dispatch(self, operation: str, values: dict[str, Any]) -> str:
        return self._index_basic(operation, values)

    def _index_basic(self, operation: str, operands: dict[str, Any]) -> str:
        spec = self.spec
        if operation == "update":
            _require_no_operands(operands)
            existing = self._existing_table(spec)
            if existing is None or existing.num_rows == 0:
                raise OperandError(
                    "findata-plugins/tushare_index_basic update has no tracked indexes; run complete first"
                )
            indexes = [f"tushare:{code}" for code in existing.column("ts_code").to_pylist()]
        elif operation == "complete":
            indexes = _string_array(operands, "indexes")
            _require_keys(operands, {"indexes"})
        else:
            raise OperandError(f"unsupported index basic operation {operation!r}")

        publication: str | None = None
        mutations: list[DataMutation] = []
        batch_started = monotonic()
        self._log(f"plan: {len(indexes)} indexes")
        self._progress(0, len(indexes))
        for completed, reference in enumerate(indexes, start=1):
            code = _canonical_index(reference)
            table = self._fetch(ts_code=code)
            returned = table.column("ts_code").to_pylist() if table.num_rows else []
            if returned != [code]:
                raise RuntimeError(f"index_basic returned no exact metadata match for {reference}")
            mutations.append(DataMutation.replace_primary_keys(table))
            if _batch_due(
                mutations,
                batch_started,
                request_limit=self.client.checkpoint_request_limit or 64,
            ):
                publication = self._commit_mutations(spec, mutations, {})
                mutations = []
                batch_started = monotonic()
            self._progress(completed, len(indexes))
        if mutations:
            publication = self._commit_mutations(spec, mutations, {})
            self._progress(len(indexes), len(indexes))
        assert publication is not None
        return publication


@dataclass(frozen=True, slots=True)
class IndexBasicOperationWorker(OperationWorker):
    """Pickle-safe task-process worker for the index basic dataset."""

    def service(
        self,
        workspace: Workspace,
        client: TushareClient,
        *,
        today: date,
        now: datetime,
        reporter: OperationReporter,
        settings: dict[str, Any],
    ) -> IndexBasicDatasetService:
        return IndexBasicDatasetService(
            workspace,
            client,
            today=today,
            now=now,
            reporter=reporter,
            settings=settings,
        )


@dataclass(frozen=True, slots=True)
class IndexBasicDatasetRuntime:
    """Dataset-scoped behavior for the Tushare index basic plugin."""

    def operation_worker(
        self,
        workspace: Path,
        *,
        mode: str,
        today: date,
        now: datetime | None,
    ) -> IndexBasicOperationWorker:
        return IndexBasicOperationWorker(
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
    ) -> IndexBasicDatasetService:
        return IndexBasicDatasetService(
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
            f"dataset {INDEX_BASIC_SPEC.name!r} has no declared dependency on {target!r}"
        )

    def update_ready(self, workspace: Workspace) -> bool:
        try:
            DataLoader(workspace.root).dataset(INDEX_BASIC_SPEC.name).publication_id
            return True
        except DatasetNotReadyError:
            return False


_OPERATION_NAMES = ["update", "complete"]

_OPERATION_HELP: dict[str, str] = {
    "update": "Refresh the already-committed tracked indexes; parameterless.",
    "complete": "Fetch the explicitly requested index references and merge them into the "
    "tracked table.",
}


def normalize_operation(
    operation: str,
    operands: dict[str, Any],
    *,
    today: date,
) -> dict[str, Any]:
    values = dict(operands)
    if operation not in _OPERATION_NAMES:
        raise OperandError(f"unsupported operation {operation!r} for {INDEX_BASIC_SPEC.name}")
    if operation == "update":
        _require_no_operands(values)
        return {}
    arrays = sorted(
        {_normalize_index_reference(value) for value in _string_array(values, "indexes")}
    )
    _require_keys(values, {"indexes"})
    return {"indexes": arrays}


def dataset_description(workspace: Workspace, *, provider_ready: bool) -> dict[str, Any]:
    state, publication_id = dataset_storage_state(workspace, INDEX_BASIC_SPEC.name)
    return {
        "name": INDEX_BASIC_SPEC.name,
        "provider": "findata-plugins/tushare",
        "provider_ready": provider_ready,
        "capabilities": dict(INDEX_BASIC_SPEC.capabilities),
        "dependencies": [],
        "settings": [],
        "storage": "duckdb",
        "state": state,
        "publication_id": publication_id,
        "operations": [operation_description(name) for name in _OPERATION_NAMES],
    }


def operation_description(operation: str) -> dict[str, Any]:
    if operation not in _OPERATION_NAMES:
        raise OperandError(f"unsupported operation {operation!r} for {INDEX_BASIC_SPEC.name}")
    help_text = _OPERATION_HELP[operation]
    if operation == "update":
        return {"name": operation, "help": help_text, "required": [], "properties": {}}
    return {
        "name": operation,
        "help": help_text,
        "required": ["indexes"],
        "properties": {
            "indexes": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "help": _OPERAND_HELP["indexes"],
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
        strategy = "one request per index"
        estimated_requests = len(normalized["indexes"])

    return {
        "dry_run": True,
        "dataset": INDEX_BASIC_SPEC.name,
        "operation": operation,
        "operands": normalized,
        "strategy": strategy,
        "estimated_provider_requests": estimated_requests,
        "dependencies": dependencies,
        "side_effects": False,
    }
