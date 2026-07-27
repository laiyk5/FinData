"""Operation engine and runtime for the findata-plugins/tushare_daily_basic dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

import pyarrow as pa

from findata.contracts import DateRange, DatasetSpec, OperandError, OperationReporter
from findata.loader import DataLoaderError
from findata.storage import DataMutation, Workspace
from findata.toolkit import ConstituentRequest, resolve_constituents
from findata_plugins.shared.engine import (
    _OPERAND_HELP,
    _batch_due,
    _canonical_index,
    _format_range,
    _merge_interval,
    _missing_for_continuity,
    _month_range,
    _next_month,
    _previous_month,
    _require_keys,
    _require_no_operands,
    _string_array,
    _timerange,
    OperationWorker,
    TushareClient,
    TushareDatasetService,
    dataset_storage_state,
    dependency_states,
)
from findata_plugins.shared.publication import PublicationWindow, daily_window
from findata_plugins.plugins.datasets.tushare_daily_basic import DAILY_BASIC_SPEC


class DailyBasicDatasetService(TushareDatasetService):
    """Synchronous operation engine for the findata-plugins/tushare_daily_basic dataset."""

    spec = DAILY_BASIC_SPEC

    def _plan(self, operation: str, values: dict[str, Any]) -> None:
        plan_operation(self.workspace, operation, values, today=self.today)

    def _dispatch(self, operation: str, values: dict[str, Any]) -> str:
        return self._daily_basic(operation, values)

    def _daily_basic(self, operation: str, operands: dict[str, Any]) -> str:
        if operation == "update":
            _require_no_operands(operands)
            selectors = self._update_setting(self.spec.name)
            if not selectors:
                raise OperandError(
                    "findata-plugins/tushare_daily_basic update requires update_symbols"
                )
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
                    f"findata-plugins/tushare_daily_basic {operation} range is entirely before the "
                    "publication window; nothing is due yet"
                )
            if self._reporter is not None:
                self._reporter.log("no daily_basic data is due yet")
            try:
                return self.loader.dataset(self.spec.name).publication_id
            except DataLoaderError as exc:
                raise OperandError(
                    "findata-plugins/tushare_daily_basic update has no due work and the dataset is "
                    "uninitialized; run complete with a historical range first"
                ) from exc
        requested = DateRange(requested.start, due_end)

        trade_requirement = {
            "exchanges": ["SSE", "SZSE"],
            "timerange": _format_range(requested),
        }
        self._fulfill("findata-plugins/tushare_trade_cal", trade_requirement)
        symbols = self._resolve_symbols(selectors, requested)
        spec = self.spec
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
            table = self.loader.dataset("findata-plugins/tushare_trade_cal").query(
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
            table = self._fetch(trade_date=target.strftime("%Y%m%d"))
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
        table = self._fetch(ts_code=symbol, start_date=stamp, end_date=stamp)
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
                self._fulfill("findata-plugins/tushare_index_weight", requirement)

            direct.extend(
                resolve_constituents(
                    self.loader,
                    ConstituentRequest(
                        "findata-plugins/tushare_index_weight",
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


@dataclass(frozen=True, slots=True)
class DailyBasicOperationWorker(OperationWorker):
    """Pickle-safe task-process worker for the daily basic dataset."""

    def service(
        self,
        workspace: Workspace,
        client: TushareClient,
        *,
        today: date,
        now: datetime,
        reporter: OperationReporter,
        settings: dict[str, Any],
    ) -> DailyBasicDatasetService:
        return DailyBasicDatasetService(
            workspace,
            client,
            today=today,
            now=now,
            reporter=reporter,
            settings=settings,
        )


@dataclass(frozen=True, slots=True)
class DailyBasicDatasetRuntime:
    """Dataset-scoped behavior for the Tushare daily basic plugin."""

    def operation_worker(
        self,
        workspace: Path,
        *,
        mode: str,
        today: date,
        now: datetime | None,
    ) -> DailyBasicOperationWorker:
        return DailyBasicOperationWorker(
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
    ) -> DailyBasicDatasetService:
        return DailyBasicDatasetService(
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
        if target not in {
            "findata-plugins/tushare_trade_cal",
            "findata-plugins/tushare_index_basic",
            "findata-plugins/tushare_index_weight",
        }:
            raise ValueError(
                f"dataset {DAILY_BASIC_SPEC.name!r} has no declared dependency on {target!r}"
            )
        return "complete", dict(requirement)

    def update_ready(self, workspace: Workspace) -> bool:
        return bool(
            workspace.get_config("dataset.findata-plugins/tushare_daily_basic.update_symbols")
        )


_OPERATION_NAMES = ["update", "complete", "refresh"]

_OPERATION_HELP: dict[str, str] = {
    "update": "Resolve the configured symbols for the latest due trading date; requires "
    "dataset.findata-plugins/tushare_daily_basic.update_symbols.",
    "complete": "Backfill or extend the requested symbols over the range; a disjoint range is "
    "extended toward existing coverage until the intervals abut.",
    "refresh": "Re-fetch the requested symbols strictly inside their existing resolved coverage.",
}


def normalize_operation(
    operation: str,
    operands: dict[str, Any],
    *,
    today: date,
) -> dict[str, Any]:
    values = dict(operands)
    if operation not in _OPERATION_NAMES:
        raise OperandError(f"unsupported operation {operation!r} for {DAILY_BASIC_SPEC.name}")
    if operation == "update":
        _require_no_operands(values)
        return {}
    timerange = _timerange(values, today=today)
    arrays = sorted(set(_string_array(values, "symbols")))
    _require_keys(values, {"symbols", "timerange"})
    return {"symbols": arrays, "timerange": _format_range(timerange)}


def dataset_description(workspace: Workspace, *, provider_ready: bool) -> dict[str, Any]:
    state, publication_id = dataset_storage_state(workspace, DAILY_BASIC_SPEC.name)
    return {
        "name": DAILY_BASIC_SPEC.name,
        "provider": "findata-plugins/tushare",
        "provider_ready": provider_ready,
        "capabilities": dict(DAILY_BASIC_SPEC.capabilities),
        "dependencies": [
            "findata-plugins/tushare_trade_cal",
            "findata-plugins/tushare_index_basic",
            "findata-plugins/tushare_index_weight",
        ],
        "settings": _dataset_settings(workspace),
        "storage": "duckdb",
        "state": state,
        "publication_id": publication_id,
        "operations": [operation_description(name) for name in _OPERATION_NAMES],
    }


def operation_description(operation: str) -> dict[str, Any]:
    if operation not in _OPERATION_NAMES:
        raise OperandError(f"unsupported operation {operation!r} for {DAILY_BASIC_SPEC.name}")
    help_text = _OPERATION_HELP[operation]
    if operation == "update":
        return {"name": operation, "help": help_text, "required": [], "properties": {}}
    return {
        "name": operation,
        "help": help_text,
        "required": ["symbols", "timerange"],
        "properties": {
            "symbols": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "help": _OPERAND_HELP["symbols"],
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
    dependencies = dependency_states(workspace, description["dependencies"])
    strategy = "plugin operation"
    estimated_requests: int | None = None
    if operation == "update":
        strategy = "configured update"
    else:
        symbols = list(normalized["symbols"])
        if all(not value.startswith("tushare:") for value in symbols):
            strategy = (
                "per-symbol bounded range or date-batched full-market, "
                "whichever needs fewer requests"
            )
            estimated_requests = len(symbols)
        else:
            strategy = "selector resolution required"

    return {
        "dry_run": True,
        "dataset": DAILY_BASIC_SPEC.name,
        "operation": operation,
        "operands": normalized,
        "strategy": strategy,
        "estimated_provider_requests": estimated_requests,
        "dependencies": dependencies,
        "side_effects": False,
    }


def _dataset_settings(workspace: Workspace) -> list[dict[str, Any]]:
    from findata_plugins.plugins.datasets.tushare_daily_basic import daily_basic_plugin

    return [
        {
            "key": key,
            "schema": dict(setting.schema),
            "help": setting.help,
            "required": setting.required,
            "configured": workspace.get_config(key) is not None,
        }
        for key, setting in daily_basic_plugin().settings.items()
    ]
