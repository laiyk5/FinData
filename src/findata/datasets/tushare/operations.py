from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
import re
from typing import Protocol
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa

from findata.contracts import DateRange, DatasetSpec, OperandError
from findata.datasets.tushare import TUSHARE_DATASETS
from findata.loader import DataLoader, DatasetNotReadyError, UnsupportedCoverageError
from findata.providers.tushare import TushareClient, TushareHTTPTransport
from findata.publication import PublicationWindow, daily_window, monthly_window
from findata.rate_limit import FileRateLimiter
from findata.storage import Coverage, Workspace
from findata.testing.tushare import (
    MOCK_TOKEN,
    MockTushareTransport,
    is_mock_token,
    transport_from_mock_token,
)
from findata.toolkit import ConstituentRequest, resolve_constituents


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

    def begin_subtask(self, *, timeout: float) -> None: ...

    def end_subtask(self) -> None: ...

    def waiting(self, reason: str) -> None: ...

    def running(self) -> None: ...

    def progress(self, current: int | float, total: int | float) -> None: ...

    def stage(self, value: str) -> None: ...


@dataclass(frozen=True, slots=True)
class OperationWorker:
    """Pickle-safe task-process entry point for one configured workspace."""

    workspace: Path
    provider: str
    token: str
    today: str
    now: str | None = None

    def __call__(self, request: dict[str, object], context: OperationReporter) -> dict[str, Any]:
        current_date = date.fromisoformat(self.today)
        current_time = (
            datetime.fromisoformat(self.now)
            if self.now
            else datetime.combine(current_date, time(16), ZoneInfo("Asia/Shanghai"))
        )
        workspace = Workspace(Path(self.workspace))
        if self.provider == "mock":
            transport = MockTushareTransport(today=current_date)
            token = self.token or MOCK_TOKEN
        elif self.provider == "real":
            configured = workspace.get_config("provider.tushare.token")
            if isinstance(configured, dict) and isinstance(configured.get("env"), str):
                import os

                token = os.environ.get(configured["env"], "")
            else:
                token = str(configured or self.token)
            transport = (
                transport_from_mock_token(token, today=current_date)
                if is_mock_token(token)
                else TushareHTTPTransport()
            )
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
            now=current_time,
            reporter=context,
            settings=dict(request.get("settings") or {}),
        )
        context.log(f"starting {request['dataset']} {request['operation']}")
        context.stage("starting")
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
    from findata.plugins import (
        discover_dataset_plugins,
        discover_provider_plugins,
        register_plugins,
    )

    providers = discover_provider_plugins()
    register_plugins(
        workspace,
        discover_dataset_plugins(providers=providers),
        providers=providers,
    )


def resolve_v1_dependency(
    parent_dataset: str,
    target_dataset: str,
    requirement: dict[str, object],
) -> tuple[str, dict[str, object]]:
    allowed = {
        "tushare_daily_basic": {
            "tushare_trade_cal",
            "tushare_index_basic",
            "tushare_index_weight",
        },
        "tushare_index_weight": {"tushare_index_basic"},
    }
    if target_dataset not in allowed.get(parent_dataset, set()):
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
        now: datetime | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.workspace = workspace
        self.client = client
        self.today = today
        self.now = now or datetime.combine(today, time(16), ZoneInfo("Asia/Shanghai"))
        self.loader = DataLoader(workspace.root)
        self._request_count = 0
        self._reporter = reporter
        self._settings = dict(settings) if settings is not None else None

    def _update_setting(self, dataset: str) -> list[str]:
        suffix = "update_indexes" if dataset == "tushare_index_weight" else "update_symbols"
        key = f"dataset.{dataset}.{suffix}"
        value = (
            self._settings.get(key, [])
            if self._settings is not None
            else self.workspace.get_config(key, [])
        )
        return list(value) if isinstance(value, list) else []

    def run(self, dataset: str, operation: str = "update", operands: dict[str, Any] | None = None) -> OperationResult:
        before = self._request_count
        values = dict(operands or {})
        if dataset == "tushare_trade_cal":
            publication = self._trade_cal(operation, values)
        elif dataset == "tushare_stock_basic":
            publication = self._stock_basic(operation, values)
        elif dataset == "tushare_index_basic":
            publication = self._index_basic(operation, values)
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
            if hasattr(self._reporter, "stage"):
                self._reporter.stage(f"fetching:{dataset}")
            if hasattr(self._reporter, "begin_subtask"):
                self._reporter.begin_subtask(timeout=180)
        self._request_count += 1
        try:
            table = self.client.query(dataset, **params)
            if self._reporter is not None:
                self._reporter.checkpoint()
            return table
        finally:
            if self._reporter is not None and hasattr(self._reporter, "end_subtask"):
                self._reporter.end_subtask()

    def _progress(self, current: int, total: int) -> None:
        if self._reporter is not None and hasattr(self._reporter, "progress"):
            self._reporter.progress(current, total)

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
        next_coverage = dict(existing_coverage)
        publication: str | None = None
        jobs = [
            (exchange, interval)
            for exchange in exchanges
            for interval in _missing_for_continuity(existing_coverage.get(exchange), requested)
        ]
        self._progress(0, len(jobs))
        for completed, (exchange, interval) in enumerate(jobs, start=1):
            start, end = interval.to_provider_inclusive()
            table = self._fetch(
                spec.name,
                exchange=exchange,
                start_date=start,
                end_date=end,
            )
            if table.num_rows == 0:
                raise RuntimeError(f"trade_cal returned empty due interval for {exchange}")
            next_coverage[exchange] = _merge_interval(next_coverage.get(exchange), interval)
            publication = self._publish(spec, [table], next_coverage)
            self._progress(completed, len(jobs))
        return publication or self._publish(spec, [], next_coverage)

    def _stock_basic(self, operation: str, operands: dict[str, Any]) -> str:
        if operation != "update":
            raise OperandError("tushare_stock_basic supports only update")
        _require_no_operands(operands)
        tables: list[pa.Table] = []
        jobs = [
            (status, exchange)
            for status in ("L", "D", "P", "G")
            for exchange in ("SSE", "SZSE", "BSE")
        ]
        self._progress(0, len(jobs))
        for completed, (status, exchange) in enumerate(jobs, start=1):
            table = self._fetch(
                "tushare_stock_basic", list_status=status, exchange=exchange
            )
            if table.num_rows >= 6000:
                raise RuntimeError(
                    f"stock_basic response may be truncated for {status}/{exchange}"
                )
            tables.append(table)
            self._progress(completed, len(jobs))
        combined = pa.concat_tables(tables)
        if combined.num_rows == 0:
            raise RuntimeError("stock_basic merged snapshot is unexpectedly empty")
        if self._reporter is not None and hasattr(self._reporter, "stage"):
            self._reporter.stage("publishing:tushare_stock_basic")
        return self._publisher("tushare_stock_basic").publish(
            _merge_tables(TUSHARE_DATASETS["tushare_stock_basic"], None, combined)
        )

    def _index_basic(self, operation: str, operands: dict[str, Any]) -> str:
        spec = TUSHARE_DATASETS["tushare_index_basic"]
        if operation == "update":
            _require_no_operands(operands)
            existing = self._existing_table(spec)
            if existing is None or existing.num_rows == 0:
                raise OperandError(
                    "tushare_index_basic update has no tracked indexes; run complete first"
                )
            indexes = [f"tushare:{code}" for code in existing.column("ts_code").to_pylist()]
        elif operation == "complete":
            indexes = _string_array(operands, "indexes")
            _require_keys(operands, {"indexes"})
        else:
            raise OperandError(f"unsupported index basic operation {operation!r}")

        publication: str | None = None
        self._progress(0, len(indexes))
        for completed, reference in enumerate(indexes, start=1):
            code = _canonical_index(reference)
            table = self._fetch(spec.name, ts_code=code)
            returned = table.column("ts_code").to_pylist() if table.num_rows else []
            if returned != [code]:
                raise RuntimeError(
                    f"index_basic returned no exact metadata match for {reference}"
                )
            publication = self._publisher(spec.name).publish(
                _merge_tables(spec, self._existing_table(spec), table)
            )
            self._progress(completed, len(indexes))
        assert publication is not None
        return publication

    def _index_weight(self, operation: str, operands: dict[str, Any]) -> str:
        if operation == "update":
            _require_no_operands(operands)
            references = self._update_setting("tushare_index_weight")
            if not references:
                raise OperandError("tushare_index_weight update requires update_indexes")
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

        spec = TUSHARE_DATASETS["tushare_index_weight"]
        existing_coverage = self._coverage_map(spec.name)
        next_coverage = dict(existing_coverage)
        publication: str | None = None
        jobs: list[tuple[str, date]] = []
        current_month = self.today.replace(day=1)
        for index in indexes:
            months = {
                month
                for interval in _missing_for_continuity(
                    existing_coverage.get(index), requested
                )
                for month in _month_starts(interval)
            }
            if requested.start <= current_month < requested.end:
                months.add(current_month)
            jobs.extend((index, month) for month in sorted(months))
        self._progress(0, len(jobs))
        for completed, (index, month) in enumerate(jobs, start=1):
            month_interval = _month_range(month, month)
            start, end = month_interval.to_provider_inclusive()
            if monthly_window(month, self.now) == PublicationWindow.BEFORE:
                raise RuntimeError(f"index_weight month is before publication window for {index}")
            table = self._fetch(
                spec.name,
                index_code=index,
                start_date=start,
                end_date=end,
            )
            next_coverage[index] = _merge_interval(
                next_coverage.get(index), month_interval
            )
            publication = self._publish_index_weight_month(
                spec, table, index=index, month=month, coverage=next_coverage
            )
            self._progress(completed, len(jobs))
        return publication or self._publish(spec, [], next_coverage)

    def _publish_index_weight_month(
        self,
        spec: DatasetSpec,
        table: pa.Table,
        *,
        index: str,
        month: date,
        coverage: dict[str, DateRange],
    ) -> str:
        existing = self._existing_table(spec)
        if table.num_rows and existing is not None:
            rows = [
                row
                for row in existing.to_pylist()
                if not (
                    row["index_code"] == index
                    and row["effective_month"] == month
                )
            ]
            existing = pa.Table.from_pylist(rows, schema=spec.schema)
        merged = _merge_tables(spec, existing, table)
        if self._reporter is not None:
            self._reporter.checkpoint()
            if hasattr(self._reporter, "stage"):
                self._reporter.stage(f"publishing:{spec.name}")
        return self._publisher(spec.name).publish(
            merged,
            coverage=[
                Coverage(key, value.start, value.end)
                for key, value in sorted(coverage.items())
            ],
        )

    def _daily_basic(self, operation: str, operands: dict[str, Any]) -> str:
        if operation == "update":
            _require_no_operands(operands)
            selectors = self._update_setting("tushare_daily_basic")
            if not selectors:
                raise OperandError("tushare_daily_basic update requires update_symbols")
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
        publication: str | None = None
        jobs = [
            (symbol, interval)
            for symbol in symbols
            for interval in (
                [requested]
                if operation == "refresh"
                else _missing_for_continuity(existing_coverage.get(symbol), requested)
            )
        ]
        self._progress(0, len(jobs))
        for completed, (symbol, interval) in enumerate(jobs, start=1):
            start, end = interval.to_provider_inclusive()
            table = self._fetch(
                spec.name,
                ts_code=symbol,
                start_date=start,
                end_date=end,
            )
            if table.num_rows == 0:
                latest_target = interval.end - timedelta(days=1)
                window = daily_window(latest_target, self.now)
                if window != PublicationWindow.AFTER:
                    raise RuntimeError(
                        f"daily_basic empty result remains unresolved in {window.value}; "
                        f"target is {window.value.split('-', 1)[0]} publication window"
                    )
            next_coverage[symbol] = _merge_interval(next_coverage.get(symbol), interval)
            publication = self._publish(spec, [table], next_coverage)
            self._progress(completed, len(jobs))
        return publication or self._publish(spec, [], next_coverage)

    def _resolve_symbols(self, selectors: list[str], requested: DateRange) -> list[str]:
        direct: list[str] = []
        index_ranges: dict[str, DateRange] = {}
        index_selections: dict[str, str] = {}
        for selector in selectors:
            if selector.startswith("tushare:"):
                reference, separator, suffix = selector.partition("@")
                self._ensure_index_metadata([reference])
                if not separator:
                    interval = requested
                    selection = "range_union"
                else:
                    if suffix == "latest":
                        target = requested.end - timedelta(days=1)
                        interval = DateRange(target, target + timedelta(days=1))
                        selection = "latest"
                    elif len(suffix) == 6 and suffix.isdigit():
                        target = date(int(suffix[:4]), int(suffix[4:]), 1)
                        interval = _month_range(target, target)
                        selection = "latest"
                    else:
                        raise OperandError(f"invalid constituent selector {selector!r}")
                current = index_ranges.get(reference)
                index_ranges[reference] = _merge_interval(current, interval)
                if selection == "range_union" or reference not in index_selections:
                    index_selections[reference] = selection
            else:
                direct.append(selector)
        for reference, interval in index_ranges.items():
            index = _canonical_index(reference)

            def fulfill(request: ConstituentRequest) -> None:
                fetch_start = _previous_month(request.start.replace(day=1))
                fetch_end = _next_month((request.end - timedelta(days=1)).replace(day=1))
                requirement: dict[str, object] = {
                    "indexes": [reference],
                    "timerange": f"{fetch_start.isoformat()}:{fetch_end.isoformat()}",
                }
                if self._reporter is not None and hasattr(self._reporter, "fulfill"):
                    self._reporter.fulfill("tushare_index_weight", requirement)
                else:
                    self._index_weight("complete", requirement)

            direct.extend(
                resolve_constituents(
                    self.loader,
                    ConstituentRequest(
                        "tushare_index_weight",
                        index,
                        "con_code",
                        interval.start,
                        interval.end,
                        effective_date_column="trade_date",
                        selection=index_selections[reference],
                    ),
                    fulfill=fulfill,
                )
            )
        return list(dict.fromkeys(direct))

    def _ensure_index_metadata(self, references: list[str]) -> None:
        missing: list[str] = []
        for reference in references:
            code = _canonical_index(reference)
            try:
                found = (
                    self.loader.dataset("tushare_index_basic")
                    .query(filters=[("ts_code", "=", code)])
                    .num_rows
                )
            except DatasetNotReadyError:
                found = 0
            if not found:
                missing.append(reference)
        if not missing:
            return
        requirement = {"indexes": missing}
        if self._reporter is not None and hasattr(self._reporter, "fulfill"):
            self._reporter.fulfill("tushare_index_basic", requirement)
        else:
            self._index_basic("complete", requirement)

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
            if hasattr(self._reporter, "stage"):
                self._reporter.stage(f"publishing:{spec.name}")
        incoming = pa.concat_tables(new_tables) if len(new_tables) > 1 else new_tables[0]
        merged = _merge_tables(spec, self._existing_table(spec), incoming)
        return self._publisher(spec.name).publish(
            merged,
            coverage=[Coverage(key, value.start, value.end) for key, value in sorted(coverage.items())],
        )

    def _publisher(self, dataset: str):
        if self._reporter is None:
            return self.workspace.publisher(dataset)
        return self.workspace.publisher(
            dataset,
            checkpoint=self._reporter.checkpoint,
            waiting=self._reporter.waiting,
            acquired=self._reporter.running,
        )


def normalize_operation(
    dataset: str,
    operation: str,
    operands: dict[str, Any],
    *,
    today: date,
) -> dict[str, Any]:
    if dataset not in TUSHARE_DATASETS:
        raise OperandError(f"unknown dataset {dataset!r}")
    values = dict(operands)
    if operation not in _operation_names(dataset):
        raise OperandError(f"unsupported operation {operation!r} for {dataset}")
    if operation == "update":
        _require_no_operands(values)
        return {}
    if dataset == "tushare_index_basic":
        arrays = sorted({_normalize_index_reference(value) for value in _string_array(values, "indexes")})
        _require_keys(values, {"indexes"})
        return {"indexes": arrays}
    timerange = _timerange(values, today=today)
    if dataset == "tushare_trade_cal":
        if timerange.end > today + timedelta(days=1):
            raise OperandError("future trade-calendar intervals cannot be completed")
        arrays = sorted(_string_array(values, "exchanges"))
        if set(arrays) - {"SSE", "SZSE"}:
            raise OperandError("trade calendar supports only SSE and SZSE")
        key = "exchanges"
    elif dataset == "tushare_index_weight":
        arrays = sorted({_normalize_index_reference(value) for value in _string_array(values, "indexes")})
        key = "indexes"
    else:
        arrays = sorted(set(_string_array(values, "symbols")))
        key = "symbols"
    _require_keys(values, {key, "timerange"})
    return {key: arrays, "timerange": _format_range(timerange)}


def dataset_description(workspace: Workspace, dataset: str, *, provider_ready: bool) -> dict[str, Any]:
    try:
        spec = TUSHARE_DATASETS[dataset]
    except KeyError as exc:
        raise OperandError(f"unknown dataset {dataset!r}") from exc
    manifest = workspace.datasets_root / dataset / "manifest.json"
    state = "unregistered"
    publication_id = None
    if manifest.exists():
        import json

        content = json.loads(manifest.read_text(encoding="utf-8"))
        state = str(content.get("state"))
        publication_id = content.get("publication_id")
    return {
        "name": dataset,
        "provider": "tushare",
        "provider_ready": provider_ready,
        "capabilities": dict(spec.capabilities),
        "dependencies": {
            "tushare_daily_basic": [
                "tushare_trade_cal",
                "tushare_index_basic",
                "tushare_index_weight",
            ],
            "tushare_index_weight": ["tushare_index_basic"],
        }.get(dataset, []),
        "settings": _dataset_settings(workspace, dataset),
        "storage": (
            "partitioned-parquet"
            if spec.time_field and dataset != "tushare_trade_cal"
            else "single-file-csv"
        ),
        "state": state,
        "publication_id": publication_id,
        "operations": [operation_description(dataset, name) for name in _operation_names(dataset)],
    }


def operation_description(dataset: str, operation: str) -> dict[str, Any]:
    if operation not in _operation_names(dataset):
        raise OperandError(f"unsupported operation {operation!r} for {dataset}")
    if operation == "update":
        return {"name": operation, "required": [], "properties": {}}
    key = {
        "tushare_trade_cal": "exchanges",
        "tushare_index_basic": "indexes",
        "tushare_index_weight": "indexes",
        "tushare_daily_basic": "symbols",
    }[dataset]
    return {
        "name": operation,
        "required": [key] if dataset == "tushare_index_basic" else [key, "timerange"],
        "properties": {
            key: {"type": "array", "items": {"type": "string"}, "minItems": 1},
            **(
                {}
                if dataset == "tushare_index_basic"
                else {"timerange": {"type": "string", "format": "half-open-date-range"}}
            ),
        },
    }


def _operation_names(dataset: str) -> list[str]:
    try:
        return {
            "tushare_trade_cal": ["update", "complete"],
            "tushare_stock_basic": ["update"],
            "tushare_index_basic": ["update", "complete"],
            "tushare_index_weight": ["update", "complete"],
            "tushare_daily_basic": ["update", "complete", "refresh"],
        }[dataset]
    except KeyError as exc:
        raise OperandError(f"unknown dataset {dataset!r}") from exc


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
    return _normalize_index_reference(value).split(":", 1)[1]


def _normalize_index_reference(value: str) -> str:
    if not value.startswith("tushare:") or "@" in value:
        raise OperandError(f"invalid Tushare index reference {value!r}")
    code = value.split(":", 1)[1]
    if not re.fullmatch(r"[A-Za-z0-9]+\.[A-Za-z]+", code):
        raise OperandError(f"invalid Tushare index reference {value!r}")
    return f"tushare:{code}"


def _dataset_settings(workspace: Workspace, dataset: str) -> list[dict[str, Any]]:
    from findata.datasets.tushare import builtin_plugins

    plugin = next(item for item in builtin_plugins() if item.name == dataset)
    return [
        {
            "key": key,
            "schema": dict(setting.schema),
            "help": setting.help,
            "configured": workspace.get_config(key) is not None,
        }
        for key, setting in plugin.settings.items()
    ]


def _expand_to_months(value: DateRange) -> DateRange:
    end_target = value.end - timedelta(days=1)
    return DateRange(value.start.replace(day=1), _next_month(end_target.replace(day=1)))


def _month_range(start: date, end: date) -> DateRange:
    return DateRange(start.replace(day=1), _next_month(end.replace(day=1)))


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def _previous_month(value: date) -> date:
    return date(value.year - (value.month == 1), (value.month - 2) % 12 + 1, 1)


def _month_starts(value: DateRange) -> list[date]:
    result: list[date] = []
    cursor = value.start.replace(day=1)
    while cursor < value.end:
        result.append(cursor)
        cursor = _next_month(cursor)
    return result


def _format_range(value: DateRange) -> str:
    return f"{value.start.isoformat()}:{value.end.isoformat()}"
