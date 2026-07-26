from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
import re
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa

from findata.contracts import (
    DateRange,
    DatasetSpec,
    OperandError,
    OperationReporter,
    OperationRequest,
)
from findata.datasets.tushare import TUSHARE_DATASETS
from findata.loader import (
    DataLoader,
    DataLoaderError,
    DatasetNotReadyError,
    UnsupportedCoverageError,
)
from findata.providers.tushare import TushareClient, TushareHTTPTransport
from findata.datasets.tushare.publication import (
    PublicationWindow,
    daily_window,
    monthly_window,
)
from findata.toolkit import FileRateLimiter
from findata.storage import (
    DATABASE_NAME,
    Coverage,
    DataMutation,
    DatasetGate,
    Workspace,
    load_metadata,
)
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


@dataclass(frozen=True, slots=True)
class OperationWorker:
    """Pickle-safe task-process entry point for one configured workspace."""

    workspace: Path
    provider: str
    token: str
    today: str
    now: str | None = None

    def __call__(self, request: OperationRequest, context: OperationReporter) -> dict[str, Any]:
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
        self._row_count = 0
        self._checkpoint_count = 0
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

    def run(
        self, dataset: str, operation: str = "update", operands: dict[str, Any] | None = None
    ) -> OperationResult:
        started = monotonic()
        before_requests = self._request_count
        before_rows = self._row_count
        before_checkpoints = self._checkpoint_count
        values = dict(operands or {})
        # Execution and dry-run share validation and planning. Mutable state is
        # read again here so a previous preview is never treated as a reservation.
        plan_operation(self.workspace, dataset, operation, values, today=self.today)
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
        self._log(
            f"completed {dataset} {operation}: "
            f"{self._request_count - before_requests} requests, "
            f"{self._row_count - before_rows} rows, "
            f"{self._checkpoint_count - before_checkpoints} checkpoints "
            f"in {monotonic() - started:.1f}s → publication {publication}"
        )
        return OperationResult(
            dataset, operation, publication, self._request_count - before_requests
        )

    def _log(self, message: str) -> None:
        if self._reporter is not None:
            self._reporter.log(message)

    def _fetch(self, dataset: str, **params: Any) -> pa.Table:
        api_name = TUSHARE_DATASETS[dataset].api_name
        shape = f"{api_name}({', '.join(f'{key}={value}' for key, value in params.items())})"
        if self._reporter is not None:
            self._reporter.checkpoint()
            self._reporter.log(f"fetch {shape}")
            if hasattr(self._reporter, "stage"):
                self._reporter.stage(f"fetching:{dataset}")
            if hasattr(self._reporter, "begin_subtask"):
                self._reporter.begin_subtask(timeout=180)
        self._request_count += 1
        try:
            table = self.client.query(dataset, **params)
            self._row_count += table.num_rows
            if self._reporter is not None:
                self._reporter.checkpoint()
                self._reporter.log(f"fetched {shape}: {table.num_rows} rows")
            return table
        finally:
            if self._reporter is not None and hasattr(self._reporter, "end_subtask"):
                self._reporter.end_subtask()

    def _progress(self, current: int, total: int) -> None:
        if self._reporter is not None and hasattr(self._reporter, "progress"):
            self._reporter.progress(
                current,
                total,
                provider_requests=self._request_count,
                rows_fetched=self._row_count,
                checkpoints=self._checkpoint_count,
            )

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
        mutations: list[DataMutation] = []
        batch_started = monotonic()
        jobs = [
            (exchange, interval)
            for exchange in exchanges
            for interval in _missing_for_continuity(existing_coverage.get(exchange), requested)
        ]
        self._log(
            f"plan: {len(exchanges)} exchanges over "
            f"{requested.start.isoformat()}..{requested.end.isoformat()}"
        )
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
            mutations.append(
                DataMutation.replace_range(
                    table,
                    partition=exchange,
                    start=interval.start,
                    end=interval.end,
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
        statuses = dict.fromkeys(status for status, _ in jobs)
        exchanges = dict.fromkeys(exchange for _, exchange in jobs)
        self._log(
            f"plan: {len(statuses)} statuses × {len(exchanges)} exchanges = {len(jobs)} "
            "requests (complete-table replacement)"
        )
        self._progress(0, len(jobs))
        for completed, (status, exchange) in enumerate(jobs, start=1):
            table = self._fetch("tushare_stock_basic", list_status=status, exchange=exchange)
            if table.num_rows >= 6000:
                raise RuntimeError(f"stock_basic response may be truncated for {status}/{exchange}")
            tables.append(table)
            self._progress(completed, len(jobs))
        combined = pa.concat_tables(tables)
        if combined.num_rows == 0:
            raise RuntimeError("stock_basic merged snapshot is unexpectedly empty")
        if self._reporter is not None and hasattr(self._reporter, "stage"):
            self._reporter.stage("committing:tushare_stock_basic")
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
        mutations: list[DataMutation] = []
        batch_started = monotonic()
        self._log(f"plan: {len(indexes)} indexes")
        self._progress(0, len(indexes))
        for completed, reference in enumerate(indexes, start=1):
            code = _canonical_index(reference)
            table = self._fetch(spec.name, ts_code=code)
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
                spec.name,
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

        # Only dates at or past their publication window are due; a before-window
        # tail is pruned so coverage never extends over unresolved dates.
        latest_due = self.today
        if daily_window(self.today, self.now) == PublicationWindow.BEFORE:
            latest_due -= timedelta(days=1)
        due_end = min(requested.end, latest_due + timedelta(days=1))
        pruned = due_end < requested.end
        if due_end <= requested.start:
            if operation != "update":
                raise OperandError(
                    f"tushare_daily_basic {operation} range is entirely before the "
                    "publication window; nothing is due yet"
                )
            if self._reporter is not None:
                self._reporter.log("no daily_basic data is due yet")
            try:
                return self.loader.dataset("tushare_daily_basic").publication_id
            except DataLoaderError as exc:
                raise OperandError(
                    "tushare_daily_basic update has no due work and the dataset is "
                    "uninitialized; run complete with a historical range first"
                ) from exc
        requested = DateRange(requested.start, due_end)

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
                if (
                    covered is None
                    or requested.start < covered.start
                    or requested.end > covered.end
                ):
                    raise OperandError(f"refresh range is outside coverage for {symbol}")
        next_coverage = dict(existing_coverage)
        jobs = [
            (symbol, interval)
            for symbol in symbols
            for interval in (
                [requested]
                if operation == "refresh"
                else _missing_for_continuity(existing_coverage.get(symbol), requested)
            )
        ]
        open_dates = self._full_market_dates(jobs)
        span = f"{requested.start.isoformat()}..{requested.end.isoformat()}"
        clamp_note = "; requested end pruned to the due boundary" if pruned else ""
        if open_dates is not None:
            self._log(
                f"plan: {len(symbols)} symbols over {span} "
                f"({len(open_dates)} due open date{'s' if len(open_dates) != 1 else ''}); "
                f"request shape: full-market per date "
                f"({len(open_dates)} requests < {len(jobs)} per-symbol){clamp_note}"
            )
            publication = self._daily_basic_by_date(spec, jobs, open_dates, next_coverage)
        else:
            self._log(
                f"plan: {len(symbols)} symbols over {span}; request shape: per-symbol "
                f"({len(jobs)} requests; full-market per date unavailable or not cheaper)"
                f"{clamp_note}"
            )
            publication = self._daily_basic_by_symbol(spec, jobs, next_coverage)
        return publication or self.loader.dataset(spec.name).publication_id

    def _full_market_dates(self, jobs: list[tuple[str, DateRange]]) -> list[date] | None:
        """Open dates for a strictly cheaper full-market per-date plan, or None."""
        if not jobs:
            return None
        start = min(interval.start for _, interval in jobs)
        end = max(interval.end for _, interval in jobs)
        try:
            table = self.loader.dataset("tushare_trade_cal").query(
                keys=["SSE", "SZSE"],
                time_range=(start, end),
                columns=["cal_date"],
                filters=[("is_open", "=", True)],
                require_coverage=True,
            )
        except DataLoaderError:
            return None
        dates = sorted(
            {
                value
                for value in table.column("cal_date").to_pylist()
                if any(interval.start <= value < interval.end for _, interval in jobs)
            }
        )
        if not dates or len(dates) >= len(jobs):
            return None
        return dates

    def _daily_basic_by_symbol(
        self,
        spec: DatasetSpec,
        jobs: list[tuple[str, DateRange]],
        next_coverage: dict[str, DateRange],
    ) -> str | None:
        publication: str | None = None
        mutations: list[DataMutation] = []
        batch_started = monotonic()
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
                self._require_resolvable_empty(interval.end - timedelta(days=1))
            next_coverage[symbol] = _merge_interval(next_coverage.get(symbol), interval)
            mutations.append(
                DataMutation.replace_range(
                    table,
                    partition=symbol,
                    start=interval.start,
                    end=interval.end,
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
        return publication

    def _daily_basic_by_date(
        self,
        spec: DatasetSpec,
        jobs: list[tuple[str, DateRange]],
        open_dates: list[date],
        next_coverage: dict[str, DateRange],
    ) -> str | None:
        row_limit = int(spec.capabilities.get("row_limit", 6000))
        accumulated: list[list[dict[str, Any]]] = [[] for _ in jobs]
        self._progress(0, len(open_dates))
        for completed, target in enumerate(open_dates, start=1):
            relevant = [
                index
                for index, (_, interval) in enumerate(jobs)
                if interval.start <= target < interval.end
            ]
            table = self._fetch(spec.name, trade_date=target.strftime("%Y%m%d"))
            if table.num_rows >= row_limit:
                if self._reporter is not None:
                    self._reporter.log(
                        "daily_basic full-market response reached the declared "
                        f"{row_limit}-row limit on {target.isoformat()}; "
                        "falling back to per-symbol requests for that date"
                    )
                for index in relevant:
                    accumulated[index].extend(
                        self._daily_basic_symbol_date(spec, jobs[index][0], target).to_pylist()
                    )
            else:
                by_symbol: dict[str, list[dict[str, Any]]] = {}
                for row in table.to_pylist():
                    by_symbol.setdefault(str(row["ts_code"]), []).append(row)
                for index in relevant:
                    rows = by_symbol.get(jobs[index][0])
                    if rows is None:
                        self._require_resolvable_empty(target)
                    else:
                        accumulated[index].extend(rows)
            self._progress(completed, len(open_dates))
        publication: str | None = None
        mutations: list[DataMutation] = []
        batch_started = monotonic()
        for (symbol, interval), rows in zip(jobs, accumulated, strict=True):
            next_coverage[symbol] = _merge_interval(next_coverage.get(symbol), interval)
            mutations.append(
                DataMutation.replace_range(
                    pa.Table.from_pylist(rows, schema=spec.schema),
                    partition=symbol,
                    start=interval.start,
                    end=interval.end,
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
        if mutations:
            publication = self._commit_mutations(spec, mutations, next_coverage)
        return publication

    def _daily_basic_symbol_date(self, spec: DatasetSpec, symbol: str, target: date) -> pa.Table:
        stamp = target.strftime("%Y%m%d")
        table = self._fetch(spec.name, ts_code=symbol, start_date=stamp, end_date=stamp)
        if table.num_rows == 0:
            self._require_resolvable_empty(target)
        return table

    def _require_resolvable_empty(self, target: date) -> None:
        window = daily_window(target, self.now)
        if window != PublicationWindow.AFTER:
            raise RuntimeError(
                f"daily_basic empty result remains unresolved in {window.value}; "
                f"target is {window.value.split('-', 1)[0]} publication window"
            )

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

    def _commit_mutations(
        self,
        spec: DatasetSpec,
        mutations: list[DataMutation],
        coverage: dict[str, DateRange],
    ) -> str:
        if self._reporter is not None:
            self._reporter.checkpoint()
            if hasattr(self._reporter, "stage"):
                self._reporter.stage(f"committing:{spec.name}")
        publication = self._publisher(spec.name).commit(
            mutations,
            coverage=(
                [Coverage(key, value.start, value.end) for key, value in sorted(coverage.items())]
                if spec.time_field is not None
                else None
            ),
        )
        self._checkpoint_count += 1
        rows = sum(mutation.table.num_rows for mutation in mutations)
        self._log(
            f"committed checkpoint: {len(mutations)} scopes, {rows} rows, publication {publication}"
        )
        if spec.time_field is not None and coverage:
            start = min(value.start for value in coverage.values())
            end = max(value.end for value in coverage.values())
            self._log(f"coverage: {len(coverage)} keys, {start.isoformat()}..{end.isoformat()}")
        return publication

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
        arrays = sorted(
            {_normalize_index_reference(value) for value in _string_array(values, "indexes")}
        )
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
        arrays = sorted(
            {_normalize_index_reference(value) for value in _string_array(values, "indexes")}
        )
        key = "indexes"
    else:
        arrays = sorted(set(_string_array(values, "symbols")))
        key = "symbols"
    _require_keys(values, {key, "timerange"})
    return {key: arrays, "timerange": _format_range(timerange)}


def dataset_description(
    workspace: Workspace, dataset: str, *, provider_ready: bool
) -> dict[str, Any]:
    try:
        spec = TUSHARE_DATASETS[dataset]
    except KeyError as exc:
        raise OperandError(f"unknown dataset {dataset!r}") from exc
    dataset_root = workspace.datasets_root / dataset
    database = dataset_root / DATABASE_NAME
    state = "unregistered"
    publication_id = None
    if database.exists():
        with DatasetGate(dataset_root / "gate.lock", exclusive=False):
            metadata = load_metadata(database)
        state = str(metadata.get("state"))
        publication_id = metadata.get("publication_id")
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
        "storage": "duckdb",
        "state": state,
        "publication_id": publication_id,
        "operations": [operation_description(dataset, name) for name in _operation_names(dataset)],
    }


_OPERATION_HELP: dict[tuple[str, str], str] = {
    (
        "tushare_trade_cal",
        "update",
    ): "Extend both SSE and SZSE calendars through tomorrow; parameterless.",
    (
        "tushare_trade_cal",
        "complete",
    ): "Fetch the requested historical civil-date range for the selected exchanges.",
    (
        "tushare_stock_basic",
        "update",
    ): "Fetch the complete A-share security table and replace the committed snapshot; "
    "parameterless.",
    (
        "tushare_index_basic",
        "update",
    ): "Refresh the already-committed tracked indexes; parameterless.",
    (
        "tushare_index_basic",
        "complete",
    ): "Fetch the explicitly requested index references and merge them into the tracked table.",
    (
        "tushare_index_weight",
        "update",
    ): "Extend the configured indexes through the current month; requires "
    "dataset.tushare_index_weight.update_indexes.",
    (
        "tushare_index_weight",
        "complete",
    ): "Fetch every intersecting calendar month for the requested index references, extending "
    "continuous monthly coverage.",
    (
        "tushare_daily_basic",
        "update",
    ): "Resolve the configured symbols for the latest due trading date; requires "
    "dataset.tushare_daily_basic.update_symbols.",
    (
        "tushare_daily_basic",
        "complete",
    ): "Backfill or extend the requested symbols over the range; a disjoint range is extended "
    "toward existing coverage until the intervals abut.",
    (
        "tushare_daily_basic",
        "refresh",
    ): "Re-fetch the requested symbols strictly inside their existing resolved coverage.",
}

_OPERAND_HELP: dict[str, str] = {
    "symbols": "Tushare security codes like 600000.SH, or constituent selectors "
    "tushare:<ts_code>[@latest|@YYYYMM].",
    "indexes": "Index references spelled tushare:<ts_code>, for example tushare:000300.SH.",
    "exchanges": "Exchanges to maintain; SSE and/or SZSE.",
    "timerange": "Half-open YYYY-MM-DD:YYYY-MM-DD range; the end is exclusive and today "
    "resolves in the dataset timezone.",
}


def operation_description(dataset: str, operation: str) -> dict[str, Any]:
    if operation not in _operation_names(dataset):
        raise OperandError(f"unsupported operation {operation!r} for {dataset}")
    help_text = _OPERATION_HELP.get((dataset, operation), "")
    if operation == "update":
        return {"name": operation, "help": help_text, "required": [], "properties": {}}
    key = {
        "tushare_trade_cal": "exchanges",
        "tushare_index_basic": "indexes",
        "tushare_index_weight": "indexes",
        "tushare_daily_basic": "symbols",
    }[dataset]
    return {
        "name": operation,
        "help": help_text,
        "required": [key] if dataset == "tushare_index_basic" else [key, "timerange"],
        "properties": {
            key: {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "help": _OPERAND_HELP[key],
            },
            **(
                {}
                if dataset == "tushare_index_basic"
                else {
                    "timerange": {
                        "type": "string",
                        "format": "half-open-date-range",
                        "help": _OPERAND_HELP["timerange"],
                    }
                }
            ),
        },
    }


def plan_operation(
    workspace: Workspace,
    dataset: str,
    operation: str,
    operands: dict[str, Any],
    *,
    today: date,
) -> dict[str, Any]:
    """Build a read-only preview from normalized operands and committed local state."""
    normalized = normalize_operation(dataset, operation, operands, today=today)
    description = dataset_description(workspace, dataset, provider_ready=True)
    dependencies = [
        {
            "dataset": name,
            "state": dataset_description(workspace, name, provider_ready=True)["state"],
        }
        for name in description["dependencies"]
    ]
    strategy = "plugin operation"
    estimated_requests: int | None = None
    if operation == "update":
        strategy = "configured update"
    elif dataset == "tushare_daily_basic":
        symbols = list(normalized["symbols"])
        if all(not value.startswith("tushare:") for value in symbols):
            strategy = (
                "per-symbol bounded range or date-batched full-market, "
                "whichever needs fewer requests"
            )
            estimated_requests = len(symbols)
        else:
            strategy = "selector resolution required"
    elif dataset == "tushare_trade_cal":
        strategy = "one request per exchange"
        estimated_requests = len(normalized["exchanges"])
    elif dataset == "tushare_index_basic":
        strategy = "one request per index"
        estimated_requests = len(normalized["indexes"])
    elif dataset == "tushare_index_weight":
        strategy = "one request per uncovered index-month"

    return {
        "dry_run": True,
        "dataset": dataset,
        "operation": operation,
        "operands": normalized,
        "strategy": strategy,
        "estimated_provider_requests": estimated_requests,
        "dependencies": dependencies,
        "side_effects": False,
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


def _batch_due(
    mutations: list[DataMutation],
    started: float,
    *,
    request_limit: int,
) -> bool:
    return (
        len(mutations) >= request_limit
        or sum(mutation.table.nbytes for mutation in mutations) >= 256 * 1024 * 1024
        or monotonic() - started >= 60
    )


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
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
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
            "required": setting.required,
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
