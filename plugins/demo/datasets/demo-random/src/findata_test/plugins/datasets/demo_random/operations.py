"""Operation engine for the findata-test/demo_random dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from findata.contracts import (
    DateRange,
    OperandError,
    OperationReporter,
    OperationRequest,
)
from findata.loader import DataLoader, DatasetNotReadyError
from findata.plugins import DatasetRuntime
from findata.storage import Workspace

from findata_test.plugins.datasets.demo_random import (
    RANDOM_FIELDS,
    RANDOM_SPEC,
    _generate_random_walk,
)


@dataclass(frozen=True, slots=True)
class RandomOperationWorker:
    """Pickle-safe worker for demo_random operations."""

    workspace: Path
    today: str
    seed: int

    def __call__(
        self,
        request: OperationRequest,
        context: OperationReporter,
    ) -> dict[str, Any]:
        operation = str(request["operation"])
        operands = dict(request.get("operands", {}))
        spec = RANDOM_SPEC
        seed = int(operands.get("seed", self.seed))

        tickers = sorted(set(str(t) for t in operands.get("tickers", ["AAPL"])))
        timerange = str(operands.get("timerange", f"{self.today}:{self.today}"))

        if not tickers:
            raise OperandError("at least one ticker is required")

        context.log(
            f"Generating random walk for {len(tickers)} ticker(s) "
            f"over {timerange!r} (seed={seed})"
        )

        start_str, end_str = timerange.split(":", 1)
        rows = _generate_random_walk(tickers, start_str, end_str, seed=seed)

        if not rows:
            context.log("No trading days in range; no data to commit")
            return {"publication_id": None, "rows": 0}

        table = spec.table_from_response(
            RANDOM_FIELDS,
            [
                [row.get(field) for field in RANDOM_FIELDS]
                for row in rows
            ],
        )

        publication = self._publish(spec, table, tickers=tickers, timerange=timerange)
        return {"publication_id": publication, "rows": len(rows)}

    def _publish(self, spec: Any, table: Any, tickers: Any = None, timerange: str = "") -> str:
        import findata.storage as storage

        ws = storage.Workspace(self.workspace)
        publisher = ws.publisher(spec.name)
        # Build coverage for coverage-tracked datasets (those with a time_field)
        coverage = None
        if timerange and tickers:
            parts = timerange.split(":", 1)
            if len(parts) == 2:
                try:
                    from datetime import date as dt_date

                    cov_start = dt_date.fromisoformat(parts[0])
                    cov_end = dt_date.fromisoformat(parts[1])
                    coverage = [
                        storage.Coverage(key=t, start=cov_start, end=cov_end)
                        for t in tickers
                    ]
                except (ValueError, TypeError):
                    pass
        return publisher.publish(table, coverage=coverage)


_OPERATION_NAMES = ["update", "complete", "refresh"]
_OPERATION_HELP: dict[str, str] = {
    "update": "Generate random walk data for configured tickers for the latest due date.",
    "complete": "Backfill random walk data for the given tickers and timerange.",
    "refresh": "Re-generate random walk data within existing coverage.",
}


def _parse_timerange(value: str, *, today: date) -> DateRange:
    try:
        return DateRange.parse(value, today=today)
    except (OperandError, ValueError) as exc:
        raise OperandError(f"invalid timerange {value!r}: {exc}") from exc


def _normalize_operands(
    operation: str,
    operands: dict[str, Any],
    *,
    today: date,
) -> dict[str, Any]:
    values = dict(operands)

    if operation == "update":
        if "tickers" in values or "timerange" in values:
            raise OperandError(f"{operation} does not accept tickers or timerange")
        return {}

    timerange = values.pop("timerange", None)
    if not timerange:
        raise OperandError(f"{operation} requires a timerange")

    tr = _parse_timerange(timerange, today=today)

    tickers_raw = values.pop("tickers", [])
    if isinstance(tickers_raw, str):
        tickers_raw = [tickers_raw]
    tickers = sorted(set(str(t) for t in tickers_raw))
    if not tickers:
        raise OperandError(f"{operation} requires at least one ticker")

    seed = values.pop("seed", None)
    if seed is not None:
        try:
            seed = int(seed)
        except (TypeError, ValueError) as exc:
            raise OperandError(f"invalid seed {seed!r}") from exc

    if values:
        raise OperandError(f"unexpected operands: {list(values)!r}")

    result: dict[str, Any] = {
        "tickers": tickers,
        "timerange": f"{tr.start.isoformat()}:{tr.end.isoformat()}",
    }
    if seed is not None:
        result["seed"] = seed
    return result


class RandomDatasetRuntime:
    """DatasetRuntime for the demo random-walk dataset."""

    def operation_worker(
        self,
        workspace: Path,
        *,
        mode: str,
        today: date,
        now: datetime | None,
    ) -> RandomOperationWorker:
        return RandomOperationWorker(
            workspace=workspace,
            today=today.isoformat(),
            seed=42,
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
                f"unsupported operation {operation!r} for {RANDOM_SPEC.name}"
            )
        return _normalize_operands(operation, operands, today=today)

    def plan_operation(
        self,
        workspace: Workspace,
        operation: str,
        operands: dict[str, Any],
        *,
        today: date,
    ) -> dict[str, Any]:
        normalized = self.normalize_operation(operation, operands, today=today)
        tickers = normalized.get("tickers", [])
        return {
            "dry_run": True,
            "dataset": RANDOM_SPEC.name,
            "operation": operation,
            "operands": normalized,
            "strategy": "generate random walk",
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
            reader = loader.dataset(RANDOM_SPEC.name)
            publication_id = reader.publication_id
            state = "ready"
        except (DatasetNotReadyError, RuntimeError):
            pass
        return {
            "name": RANDOM_SPEC.name,
            "provider": "findata-test/demo",
            "provider_ready": provider_ready,
            "capabilities": dict(RANDOM_SPEC.capabilities),
            "dependencies": [],
            "settings": [
                {
                    "key": f"dataset.{RANDOM_SPEC.name}.update_symbols",
                    "schema": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "help": "Tickers maintained by update.",
                    "required": True,
                    "configured": workspace.get_config(
                        f"dataset.{RANDOM_SPEC.name}.update_symbols"
                    )
                    is not None,
                },
                {
                    "key": f"dataset.{RANDOM_SPEC.name}.seed",
                    "schema": {"type": "integer", "minimum": 0},
                    "help": "Random seed for reproducible generation.",
                    "required": False,
                    "configured": workspace.get_config(
                        f"dataset.{RANDOM_SPEC.name}.seed"
                    )
                    is not None,
                },
            ],
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
                f"unsupported operation {operation!r} for {RANDOM_SPEC.name}"
            )
        help_text = _OPERATION_HELP.get(operation, "")
        if operation == "update":
            return {"name": operation, "help": help_text, "required": [], "properties": {}}

        return {
            "name": operation,
            "help": help_text,
            "required": ["tickers", "timerange"],
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "help": "Asset ticker symbols (e.g. AAPL, GOOGL)",
                },
                "timerange": {
                    "type": "string",
                    "format": "half-open-date-range",
                    "help": "Half-open date range start:end (e.g. 2026-07-01:2026-07-20)",
                },
                "seed": {
                    "type": "integer",
                    "minimum": 0,
                    "help": "Override random seed (default: 42)",
                },
            },
        }

    def resolve_dependency(
        self,
        target: str,
        requirement: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        raise ValueError(
            f"dataset {RANDOM_SPEC.name!r} has no declared dependencies"
        )

    def update_ready(self, workspace: Workspace) -> bool:
        return bool(
            workspace.get_config(f"dataset.{RANDOM_SPEC.name}.update_symbols")
        )
