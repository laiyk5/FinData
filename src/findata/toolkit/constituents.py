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
    table = loader.dataset(request.dependency_dataset).query(
        keys=[request.dependency_key],
        time_range=(request.start, request.end),
        columns=[request.result_column],
        require_coverage=True,
    )
    return list(dict.fromkeys(table.column(request.result_column).to_pylist()))
