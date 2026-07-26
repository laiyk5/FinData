from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from types import MappingProxyType
from typing import Any, Protocol, TypedDict, runtime_checkable

import pyarrow as pa


class OperandError(ValueError):
    """An operation operand cannot be normalized or validated."""


class DatasetDataError(ValueError):
    """Provider data violates a registered dataset contract."""


@dataclass(frozen=True, slots=True)
class DateRange:
    """A nonempty half-open civil-date interval."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise OperandError("timerange must be nonempty and ordered as [start, end)")

    @classmethod
    def parse(cls, value: str, *, today: date) -> DateRange:
        if not isinstance(value, str) or value.count(":") != 1:
            raise OperandError("timerange must use start:end syntax")
        start_text, end_text = value.split(":", 1)
        start = _parse_operand_date(start_text, today=today)
        end = _parse_operand_date(end_text, today=today)
        return cls(start=start, end=end)

    def to_provider_inclusive(self) -> tuple[str, str]:
        return self.start.strftime("%Y%m%d"), (self.end - timedelta(days=1)).strftime("%Y%m%d")


def _parse_operand_date(value: str, *, today: date) -> date:
    if value == "today":
        return today
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise OperandError(f"invalid ISO date: {value!r}") from exc
    if len(value) != 10:
        raise OperandError(f"invalid ISO date: {value!r}")
    return parsed


RowNormalizer = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def _identity_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rows


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """Read-side and provider-normalization contract for one dataset."""

    name: str
    api_name: str
    schema: pa.Schema
    provider_fields: tuple[str, ...]
    primary_key: tuple[str, ...]
    partition_key: str | None = None
    secondary_key: str | None = None
    time_field: str | None = None
    missing_data_policy: str = "strict"
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    aliases: Mapping[str, str] = field(default_factory=dict)
    normalize_rows: RowNormalizer = field(default=_identity_rows, repr=False, compare=False)

    def __post_init__(self) -> None:
        schema_names = set(self.schema.names)
        if not self.primary_key or not set(self.primary_key) <= schema_names:
            raise ValueError(f"{self.name}: primary key must be present in schema")
        for key in (self.partition_key, self.secondary_key, self.time_field):
            if key is not None and key not in schema_names:
                raise ValueError(f"{self.name}: declared key {key!r} is absent from schema")
        if self.missing_data_policy not in {"strict", "accept-empty", "best-effort"}:
            raise ValueError(f"{self.name}: invalid missing-data policy")
        object.__setattr__(self, "capabilities", MappingProxyType(dict(self.capabilities)))
        object.__setattr__(self, "aliases", MappingProxyType(dict(self.aliases)))

    def table_from_response(
        self,
        response_fields: Sequence[str],
        response_items: Sequence[Sequence[Any]],
    ) -> pa.Table:
        field_names = tuple(response_fields)
        missing = [name for name in self.provider_fields if name not in field_names]
        if missing:
            raise DatasetDataError(f"{self.api_name}: missing response fields {missing!r}")

        rows: list[dict[str, Any]] = []
        for position, item in enumerate(response_items):
            if len(item) != len(field_names):
                raise DatasetDataError(
                    f"{self.api_name}: item {position} has {len(item)} values for "
                    f"{len(field_names)} fields"
                )
            rows.append(dict(zip(field_names, item, strict=True)))

        normalized = self.normalize_rows(rows)
        self._validate_rows(normalized)
        try:
            return pa.Table.from_pylist(normalized, schema=self.schema)
        except (pa.ArrowException, TypeError, ValueError) as exc:
            raise DatasetDataError(f"{self.api_name}: value does not match logical schema") from exc

    def _validate_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        nonnullable = tuple(field.name for field in self.schema if not field.nullable)
        seen: set[tuple[Any, ...]] = set()
        for position, row in enumerate(rows):
            missing_values = [name for name in nonnullable if row.get(name) is None]
            if missing_values:
                raise DatasetDataError(
                    f"{self.api_name}: row {position} has null required fields {missing_values!r}"
                )
            key = tuple(row.get(name) for name in self.primary_key)
            if key in seen:
                raise DatasetDataError(f"{self.api_name}: duplicate primary key {key!r}")
            seen.add(key)


def provider_date(value: Any, *, nullable: bool = False) -> date | None:
    if value in (None, ""):
        if nullable:
            return None
        raise DatasetDataError("required provider date is empty")
    try:
        return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:8]}")
    except (TypeError, ValueError, IndexError) as exc:
        raise DatasetDataError(f"invalid provider date: {value!r}") from exc


class OperationRequest(TypedDict):
    """The immutable task request handed to a dataset operation worker."""

    execution_id: str
    dataset: str
    operation: str
    operands: dict[str, Any]
    configuration_revision: int
    settings: dict[str, Any]


@runtime_checkable
class OperationReporter(Protocol):
    """Child-process interface a worker uses for logs, states, and cancellation."""

    def checkpoint(self) -> None:
        """Cooperative cancellation point; raises when cancellation was requested."""
        ...

    def log(self, message: str) -> None: ...

    def diagnostic(
        self,
        severity: str,
        code: str,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
        count: int = 1,
    ) -> None: ...

    def progress(
        self,
        current: int | float,
        total: int | float,
        **metrics: int | float,
    ) -> None: ...

    def stage(self, value: str) -> None: ...

    def waiting(self, reason: str) -> None: ...

    def running(self) -> None: ...

    def begin_subtask(self, *, timeout: float) -> None: ...

    def end_subtask(self) -> None: ...

    def fulfill(self, dataset: str, requirement: Mapping[str, Any]) -> Any:
        """Run a declared dependency to satisfy one requirement; raises on failure."""
        ...


OperationWorker = Callable[[OperationRequest, OperationReporter], Mapping[str, Any]]
