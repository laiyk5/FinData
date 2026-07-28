"""Minimal operation engine for the findata-test/demo_hello dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from findata.sdk.contracts import (
    DatasetSpec,
    OperandError,
    OperationReporter,
    OperationRequest,
)
from findata.sdk.loader import DataLoader, DatasetNotReadyError
from findata.storage import Workspace

from findata_test.demo.plugins.datasets.examples.hello import HELLO_SPEC, _hello_rows


@dataclass(frozen=True, slots=True)
class HelloOperationWorker:
    """Pickle-safe worker for demo_hello operations."""

    workspace: Path
    today: str

    def __call__(
        self,
        request: OperationRequest,
        context: OperationReporter,
    ) -> dict[str, Any]:
        operands = dict(request.get("operands", {}))
        spec = HELLO_SPEC
        rows = int(operands.get("rows", 5))
        if rows < 1:
            raise OperandError("rows must be positive")
        context.log(f"Generating {rows} hello rows")
        table = spec.table_from_response(
            HELLO_FIELDS,
            [
                [row[name] for name in HELLO_FIELDS]
                for row in _hello_rows(rows)
            ],
        )
        publication = self._publish(spec, table)
        return {"publication_id": publication, "rows": rows}

    def _publish(self, spec: DatasetSpec, table: Any) -> str:
        import findata.storage as storage

        ws = storage.Workspace(self.workspace)
        publisher = ws.publisher(spec.name)
        return publisher.publish(table)


HELLO_FIELDS = ("name", "greeting", "count")

_OPERATION_NAMES = ["update", "complete", "refresh"]
_OPERATION_HELP: dict[str, str] = {
    "update": "Generate the default hello rows.",
    "complete": "Generate hello rows with optional --param rows=N.",
    "refresh": "Same as complete for demo_hello (regenerates data).",
}


class HelloDatasetRuntime:
    """Minimal DatasetRuntime that generates hardcoded greeting data."""

    def operation_worker(
        self,
        workspace: Path,
        *,
        mode: str,
        today: date,
        now: datetime | None,
    ) -> HelloOperationWorker:
        return HelloOperationWorker(
            workspace=workspace,
            today=today.isoformat(),
        )

    def normalize_operation(
        self,
        operation: str,
        operands: dict[str, Any],
        *,
        today: date,
    ) -> dict[str, Any]:
        if operation not in _OPERATION_NAMES:
            raise OperandError(
                f"unsupported operation {operation!r} for {HELLO_SPEC.name}"
            )
        values = dict(operands)
        if "rows" in values:
            rows = values["rows"]
            if isinstance(rows, str):
                try:
                    rows = int(rows)
                except ValueError as exc:
                    raise OperandError(f"rows must be an integer: {rows!r}") from exc
            if not isinstance(rows, int) or rows < 1:
                raise OperandError(f"rows must be a positive integer: {rows!r}")
            values["rows"] = rows
        return values

    def plan_operation(
        self,
        workspace: Workspace,
        operation: str,
        operands: dict[str, Any],
        *,
        today: date,
    ) -> dict[str, Any]:
        normalized = self.normalize_operation(operation, operands, today=today)
        return {
            "dry_run": True,
            "dataset": HELLO_SPEC.name,
            "operation": operation,
            "operands": normalized,
            "strategy": "generate",
            "estimated_provider_requests": 0,
            "dependencies": [],
            "side_effects": False,
        }

    def dataset_description(
        self,
        workspace: Workspace,
        *,
        provider_ready: bool,
    ) -> dict[str, Any]:
        loader = DataLoader(workspace.root)
        state = "uninitialized"
        publication_id: str | None = None
        try:
            reader = loader.dataset(HELLO_SPEC.name)
            publication_id = reader.publication_id
            state = "ready"
        except (DatasetNotReadyError, RuntimeError):
            pass
        return {
            "name": HELLO_SPEC.name,
            "provider": "findata-test/demo",
            "provider_ready": provider_ready,
            "capabilities": dict(HELLO_SPEC.capabilities),
            "dependencies": [],
            "settings": [],
            "storage": "duckdb",
            "state": state,
            "publication_id": publication_id,
            "operations": [
                self.operation_description(name) for name in _OPERATION_NAMES
            ],
        }

    def operation_description(self, operation: str) -> dict[str, Any]:
        if operation not in _OPERATION_NAMES:
            raise OperandError(
                f"unsupported operation {operation!r} for {HELLO_SPEC.name}"
            )
        help_text = _OPERATION_HELP.get(operation, "")
        return {
            "name": operation,
            "help": help_text,
            "required": [],
            "properties": {
                "rows": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 5,
                    "help": "Number of greeting rows to generate",
                }
            },
        }

    def resolve_dependency(
        self,
        target: str,
        requirement: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        raise ValueError(
            f"dataset {HELLO_SPEC.name!r} has no declared dependencies"
        )

    def update_ready(self, workspace: Workspace) -> bool:
        return True
