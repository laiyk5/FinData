from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol
from typing import Any

import pyarrow as pa

from findata.contracts import DateRange, DatasetSpec, OperandError
from findata.datasets.tushare import TUSHARE_DATASETS
from findata.loader import DataLoader, DatasetNotReadyError, UnsupportedCoverageError
from findata.providers.tushare import TushareClient, TushareHTTPTransport
from findata.rate_limit import FileRateLimiter
from findata.storage import Coverage, Workspace
from findata.testing.tushare import MockTushareTransport


@dataclass(frozen=True, slots=True)
class OperationResult:
    dataset: str
    operation: str
    publication_id: str
    fetched_requests: int


class OperationReporter(Protocol):
    def checkpoint(self) -> None: ...

    def log(self, message: str) -> None: ...

    def fulfill(self, dataset: str, requirement: dict[str, Any]) -> Any: ...


@dataclass(frozen=True, slots=True)
class OperationWorker:
    """Pickle-safe task-process entry point for one configured workspace."""

    workspace: Path
    provider: str
    token: str
    today: str

    def __call__(self, request: dict[str, object], context: OperationReporter) -> dict[str, Any]:
        current_date = date.fromisoformat(self.today)
        workspace = Workspace(Path(self.workspace))
        if self.provider == "mock":
            transport = MockTushareTransport(today=current_date)
            token = self.token or "mock-token"
        elif self.provider == "real":
            transport = TushareHTTPTransport()
            configured = workspace.get_config("provider.tushare.token")
            if isinstance(configured, dict) and isinstance(configured.get("env"), str):
                import os

                token = os.environ.get(configured["env"], "")
            else:
                token = str(configured or self.token)
        else:
            raise ValueError(f"unsupported provider mode {self.provider!r}")
        rate_limit = int(workspace.get_config("provider.tushare.rate_limit", 500))
        limiter = FileRateLimiter(
            workspace.root / "providers" / "tushare-rate.json",
            limit=rate_limit,
            period=60,
        )

        def permit() -> None:
            limiter.acquire(checkpoint=context.checkpoint, waiting=context.waiting)
            context.running()

        service = DatasetService(
            workspace,
            TushareClient(token=token, transport=transport, permit=permit),
            today=current_date,
            reporter=context,
        )
        context.log(f"starting {request['dataset']} {request['operation']}")
        context.checkpoint()
        result = service.run(
            str(request["dataset"]),
            str(request["operation"]),
            dict(request.get("operands") or {}),
        )
        context.checkpoint()
        context.log(f"published {result.publication_id}")
        return asdict(result)


def register_v1_datasets(workspace: Workspace) -> None:
    strategies = {
        "tushare_trade_cal": "single-file-csv",
        "tushare_stock_basic": "single-file-csv",
        "tushare_index_weight": "partitioned-parquet",
        "tushare_daily_basic": "partitioned-parquet",
    }
    for dataset, strategy in strategies.items():
        workspace.register_dataset(dataset, strategy=strategy)


def resolve_v1_dependency(
    parent_dataset: str,
    target_dataset: str,
    requirement: dict[str, object],
) -> tuple[str, dict[str, object]]:
    if parent_dataset != "tushare_daily_basic" or target_dataset not in {
        "tushare_trade_cal",
        "tushare_index_weight",
    }:
        raise ValueError(
            f"dataset {parent_dataset!r} has no declared dependency on {target_dataset!r}"
        )
    return "complete", dict(requirement)


class DatasetService:
    """Synchronous operation engine used by task processes and deterministic tests."""

    def __init__(
        self,
        workspace: Workspace,
        client: TushareClient,
        *,
        today: date,
        reporter: OperationReporter | None = None,
    ) -> None:
        self.workspace = workspace
        self.client = client
        self.today = today
        self.loader = DataLoader(workspace.root)
        self._request_count = 0
        self._reporter = reporter

    def set_universe(self, dataset: str, selectors: list[str]) -> None:
        if dataset not in {"tushare_index_weight", "tushare_daily_basic"}:
            raise OperandError(f"dataset {dataset!r} has an intrinsic universe")
        self.workspace.set_universe(dataset, selectors)

    def get_universe(self, dataset: str) -> list[str]:
        return self.workspace.get_universe(dataset)

    def run(self, dataset: str, operation: str = "update", operands: dict[str, Any] | None = None) -> OperationResult:
        before = self._request_count
        values = dict(operands or {})
        if dataset == "tushare_trade_cal":
            publication = self._trade_cal(operation, values)
        elif dataset == "tushare_stock_basic":
            publication = self._stock_basic(operation, values)
        elif dataset == "tushare_index_weight":
            publication = self._index_weight(operation, values)
        elif dataset == "tushare_daily_basic":
            publication = self._daily_basic(operation, values)
        else:
            raise KeyError(dataset)
        return OperationResult(dataset, operation, publication, self._request_count - before)

    def _fetch(self, dataset: str, **params: Any) -> pa.Table:
        if self._reporter is not None:
            self._reporter.checkpoint()
            self._reporter.log(f"fetch {dataset}")
        self._request_count += 1
        table = self.client.query(dataset, **params)
        if self._reporter is not None:
            self._reporter.checkpoint()
        return table

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

        spec = TUSHARE_DATASETS["tushare_trade_cal"]
        existing_coverage = self._coverage_map(spec.name)
        tables: list[pa.Table] = []
        next_coverage = dict(existing_coverage)
        for exchange in exchanges:
            for interval in _missing_for_continuity(existing_coverage.get(exchange), requested):
                start, end = interval.to_provider_inclusive()
                table = self._fetch(
                    spec.name,
                    exchange=exchange,
                    start_date=start,
                    end_date=end,
                )
                if table.num_rows == 0:
                    raise RuntimeError(f"trade_cal returned empty due interval for {exchange}")
                tables.append(table)
                next_coverage[exchange] = _merge_interval(next_coverage.get(exchange), interval)
        return self._publish(spec, tables, next_coverage)

    def _stock_basic(self, operation: str, operands: dict[str, Any]) -> str:
        if operation != "update":
            raise OperandError("tushare_stock_basic supports only update")
        _require_no_operands(operands)
        tables: list[pa.Table] = []
        for status in ("L", "D", "P", "G"):
            for exchange in ("SSE", "SZSE", "BSE"):
                table = self._fetch(
                    "tushare_stock_basic", list_status=status, exchange=exchange
                )
                if table.num_rows >= 6000:
                    raise RuntimeError(
                        f"stock_basic response may be truncated for {status}/{exchange}"
                    )
                tables.append(table)
        combined = pa.concat_tables(tables)
        if combined.num_rows == 0:
            raise RuntimeError("stock_basic merged snapshot is unexpectedly empty")
        return self.workspace.publisher("tushare_stock_basic").publish(
            _merge_tables(TUSHARE_DATASETS["tushare_stock_basic"], None, combined)
        )

    def _index_weight(self, operation: str, operands: dict[str, Any]) -> str:
        if operation == "update":
            _require_no_operands(operands)
            indexes = self.get_universe("tushare_index_weight")
            if not indexes:
                raise OperandError("tushare_index_weight update requires a configured universe")
            requested = _month_range(self.today, self.today)
        elif operation == "complete":
            indexes = [_canonical_index(value) for value in _string_array(operands, "indexes")]
            requested = _expand_to_months(_timerange(operands, today=self.today))
            _require_keys(operands, {"indexes", "timerange"})
        else:
            raise OperandError(f"unsupported index weight operation {operation!r}")

        spec = TUSHARE_DATASETS["tushare_index_weight"]
        existing_coverage = self._coverage_map(spec.name)
        next_coverage = dict(existing_coverage)
        tables: list[pa.Table] = []
        for index in indexes:
            intervals = _missing_for_continuity(existing_coverage.get(index), requested)
            for interval in intervals:
                for month in _month_starts(interval):
                    month_interval = _month_range(month, month)
                    start, end = month_interval.to_provider_inclusive()
                    table = self._fetch(
                        spec.name,
                        index_code=index,
                        start_date=start,
                        end_date=end,
                    )
                    if table.num_rows == 0:
                        raise RuntimeError(f"index_weight returned empty historical month for {index}")
                    tables.append(table)
                next_coverage[index] = _merge_interval(next_coverage.get(index), interval)
        return self._publish(spec, tables, next_coverage)

    def _daily_basic(self, operation: str, operands: dict[str, Any]) -> str:
        if operation == "update":
            _require_no_operands(operands)
            selectors = self.get_universe("tushare_daily_basic")
            if not selectors:
                raise OperandError("tushare_daily_basic update requires a configured universe")
            requested = DateRange(self.today, self.today + timedelta(days=1))
        elif operation in {"complete", "refresh"}:
            selectors = _string_array(operands, "symbols")
            requested = _timerange(operands, today=self.today)
            _require_keys(operands, {"symbols", "timerange"})
        else:
            raise OperandError(f"unsupported daily basic operation {operation!r}")

        trade_requirement = {
            "exchanges": ["SSE", "SZSE"],
            "timerange": _format_range(requested),
        }
        if self._reporter is not None and hasattr(self._reporter, "fulfill"):
            self._reporter.fulfill("tushare_trade_cal", trade_requirement)
        else:
            self._trade_cal("complete", trade_requirement)
        symbols = self._resolve_symbols(selectors, requested)
        spec = TUSHARE_DATASETS["tushare_daily_basic"]
        existing_coverage = self._coverage_map(spec.name)
        if operation == "refresh":
            for symbol in symbols:
                covered = existing_coverage.get(symbol)
                if covered is None or requested.start < covered.start or requested.end > covered.end:
                    raise OperandError(f"refresh range is outside coverage for {symbol}")
        next_coverage = dict(existing_coverage)
        tables: list[pa.Table] = []
        for symbol in symbols:
            intervals = [requested] if operation == "refresh" else _missing_for_continuity(
                existing_coverage.get(symbol), requested
            )
            for interval in intervals:
                start, end = interval.to_provider_inclusive()
                tables.append(
                    self._fetch(
                        spec.name,
                        ts_code=symbol,
                        start_date=start,
                        end_date=end,
                    )
                )
                next_coverage[symbol] = _merge_interval(next_coverage.get(symbol), interval)
        return self._publish(spec, tables, next_coverage)

    def _resolve_symbols(self, selectors: list[str], requested: DateRange) -> list[str]:
        direct: list[str] = []
        index_ranges: dict[str, DateRange] = {}
        for selector in selectors:
            if selector.startswith("CSI300"):
                index = "000300.SH"
                if "@" not in selector:
                    interval = _expand_to_months(requested)
                else:
                    _, suffix = selector.split("@", 1)
                    if suffix == "latest":
                        target = requested.end - timedelta(days=1)
                        interval = _month_range(target, target)
                    elif len(suffix) == 6 and suffix.isdigit():
                        target = date(int(suffix[:4]), int(suffix[4:]), 1)
                        interval = _month_range(target, target)
                    else:
                        raise OperandError(f"invalid constituent selector {selector!r}")
                current = index_ranges.get(index)
                index_ranges[index] = _merge_interval(current, interval)
            else:
                direct.append(selector)
        for index, interval in index_ranges.items():
            index_requirement = {"indexes": [index], "timerange": _format_range(interval)}
            if self._reporter is not None and hasattr(self._reporter, "fulfill"):
                self._reporter.fulfill("tushare_index_weight", index_requirement)
            else:
                self._index_weight("complete", index_requirement)
            rows = self.loader.dataset("tushare_index_weight").query(
                keys=[index],
                time_range=(interval.start, interval.end),
                columns=["con_code"],
                require_coverage=True,
            )
            direct.extend(rows.column("con_code").to_pylist())
        return list(dict.fromkeys(direct))

    def _coverage_map(self, dataset: str) -> dict[str, DateRange]:
        try:
            rows = self.loader.dataset(dataset).coverage().to_pylist()
        except (DatasetNotReadyError, UnsupportedCoverageError):
            return {}
        return {row["key"]: DateRange(row["start"], row["end"]) for row in rows}

    def _existing_table(self, spec: DatasetSpec) -> pa.Table | None:
        try:
            return self.loader.dataset(spec.name).query()
        except DatasetNotReadyError:
            return None

    def _publish(
        self,
        spec: DatasetSpec,
        new_tables: list[pa.Table],
        coverage: dict[str, DateRange],
    ) -> str:
        if not new_tables:
            return self.loader.dataset(spec.name).publication_id
        if self._reporter is not None:
            self._reporter.checkpoint()
        incoming = pa.concat_tables(new_tables) if len(new_tables) > 1 else new_tables[0]
        merged = _merge_tables(spec, self._existing_table(spec), incoming)
        return self.workspace.publisher(spec.name).publish(
            merged,
            coverage=[Coverage(key, value.start, value.end) for key, value in sorted(coverage.items())],
        )


def _merge_tables(spec: DatasetSpec, existing: pa.Table | None, incoming: pa.Table) -> pa.Table:
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    if existing is not None:
        for row in existing.to_pylist():
            rows[tuple(row[key] for key in spec.primary_key)] = row
    for row in incoming.to_pylist():
        rows[tuple(row[key] for key in spec.primary_key)] = row
    ordered = [rows[key] for key in sorted(rows)]
    return pa.Table.from_pylist(ordered, schema=spec.schema)


def _missing_for_continuity(existing: DateRange | None, requested: DateRange) -> list[DateRange]:
    if existing is None:
        return [requested]
    if requested.start >= existing.start and requested.end <= existing.end:
        return []
    if requested.end < existing.start:
        return [DateRange(requested.start, existing.start)]
    if requested.start > existing.end:
        return [DateRange(existing.end, requested.end)]
    intervals: list[DateRange] = []
    if requested.start < existing.start:
        intervals.append(DateRange(requested.start, existing.start))
    if requested.end > existing.end:
        intervals.append(DateRange(existing.end, requested.end))
    return intervals


def _merge_interval(existing: DateRange | None, incoming: DateRange) -> DateRange:
    if existing is None:
        return incoming
    return DateRange(min(existing.start, incoming.start), max(existing.end, incoming.end))


def _timerange(operands: dict[str, Any], *, today: date) -> DateRange:
    value = operands.get("timerange")
    if not isinstance(value, str):
        raise OperandError("timerange is required")
    return DateRange.parse(value, today=today)


def _string_array(operands: dict[str, Any], name: str) -> list[str]:
    value = operands.get(name)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise OperandError(f"{name} must be a nonempty string array")
    return list(dict.fromkeys(value))


def _require_no_operands(operands: dict[str, Any]) -> None:
    if operands:
        raise OperandError("update is parameterless")


def _require_keys(operands: dict[str, Any], allowed: set[str]) -> None:
    unexpected = set(operands) - allowed
    if unexpected:
        raise OperandError(f"unexpected operands: {sorted(unexpected)!r}")


def _canonical_index(value: str) -> str:
    if value == "CSI300":
        return "000300.SH"
    if value == "000300.SH":
        return value
    raise OperandError(f"unknown v1 index {value!r}")


def _expand_to_months(value: DateRange) -> DateRange:
    end_target = value.end - timedelta(days=1)
    return DateRange(value.start.replace(day=1), _next_month(end_target.replace(day=1)))


def _month_range(start: date, end: date) -> DateRange:
    return DateRange(start.replace(day=1), _next_month(end.replace(day=1)))


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def _month_starts(value: DateRange) -> list[date]:
    result: list[date] = []
    cursor = value.start.replace(day=1)
    while cursor < value.end:
        result.append(cursor)
        cursor = _next_month(cursor)
    return result


def _format_range(value: DateRange) -> str:
    return f"{value.start.isoformat()}:{value.end.isoformat()}"
