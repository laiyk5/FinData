from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from findata.loader import DataLoader


@dataclass(frozen=True, slots=True)
class ConstituentRequest:
    dependency_dataset: str
    dependency_key: str
    result_column: str
    start: date
    end: date
    effective_date_column: str | None = None
    selection: str = "range_union"


def resolve_constituents(
    loader: DataLoader,
    request: ConstituentRequest,
    *,
    fulfill: Callable[[ConstituentRequest], None],
) -> list[str]:
    """Fulfill and read one already-parsed constituent request.

    Public selector syntax is intentionally absent from this interface.
    """
    fulfill(request)
    dataset = loader.dataset(request.dependency_dataset)
    if request.effective_date_column is None:
        table = dataset.query(
            keys=[request.dependency_key],
            time_range=(request.start, request.end),
            columns=[request.result_column],
            require_coverage=True,
        )
        return list(dict.fromkeys(table.column(request.result_column).to_pylist()))

    if request.selection not in {"range_union", "latest"}:
        raise ValueError(f"unknown constituent selection {request.selection!r}")
    table = dataset.query(
        keys=[request.dependency_key],
        columns=[request.result_column, request.effective_date_column],
    )
    rows = table.to_pylist()
    available = sorted(
        {
            row[request.effective_date_column]
            for row in rows
            if row[request.effective_date_column] < request.end
        }
    )
    if not available:
        raise ValueError(
            f"no constituent snapshot exists before {request.end.isoformat()}"
        )
    if request.selection == "latest":
        selected = {available[-1]}
    else:
        predecessors = [value for value in available if value <= request.start]
        if not predecessors:
            raise ValueError(
                f"no constituent snapshot exists on or before {request.start.isoformat()}"
            )
        selected = {
            predecessors[-1],
            *(value for value in available if request.start < value < request.end),
        }
    return list(
        dict.fromkeys(
            row[request.result_column]
            for row in rows
            if row[request.effective_date_column] in selected
        )
    )
