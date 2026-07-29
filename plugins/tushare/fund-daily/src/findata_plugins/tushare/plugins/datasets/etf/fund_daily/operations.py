"""Operation engine and runtime for the findata-plugins/tushare_fund_daily dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

import pyarrow.compute as pc

from findata.sdk.contracts import DateRange, DatasetSpec, OperandError, OperationReporter
from findata.sdk.loader import DataLoaderError
from findata.storage import DataMutation, Workspace
from findata_plugins.tushare.shared.engine import (
    _OPERAND_HELP,
    _batch_due,
    _format_range,
    _merge_interval,
    _missing_for_continuity,
    _require_keys,
    _require_no_operands,
    _string_array,
    _timerange,
    OperationWorker,
    TushareClient,
    TushareDatasetService,
    dataset_storage_state,
)
from findata_plugins.tushare.plugins.datasets.etf.fund_daily import FUND_DAILY_SPEC


class FundDailyDatasetService(TushareDatasetService):
    """Synchronous operation engine for the findata-plugins/tushare_fund_daily dataset."""

    spec = FUND_DAILY_SPEC

    def _plan(self, operation: str, values: dict[str, Any]) -> None:
        plan_operation(self.workspace, operation, values, today=self.today)

    def _dispatch(self, operation: str, values: dict[str, Any]) -> str:
        return self._fund_daily(operation, values)

    def _fund_daily(self, operation: str, operands: dict[str, Any]) -> str:
        if operation == "update":
            _require_no_operands(operands)
            selectors = self._update_setting(self.spec.name) or ["stored"]
            existing_coverage = self._coverage_map(self.spec.name)
            if "stored" in selectors:
                selectors = [
                    *[selector for selector in selectors if selector != "stored"],
                    *sorted(existing_coverage),
                ]
            if not selectors:
                self._log("no stored symbols; fund_daily update has no work")
                return None
            requested = DateRange(self.today, self.today + timedelta(days=1))
        elif operation in {"complete", "refresh"}:
            if operation == "complete" and "symbols" not in operands:
                operands = {**operands, "symbols": ["all"]}
            selectors = _string_array(operands, "symbols")
            requested = _timerange(operands, today=self.today)
            _require_keys(operands, {"symbols", "timerange"})
            existing_coverage = self._coverage_map(self.spec.name)
        else:
            raise OperandError(f"unsupported fund daily operation {operation!r}")

        if "all" in selectors and selectors != ["all"]:
            raise OperandError("the 'all' selector must be used on its own")

        latest_due = self.today
        due_end = min(requested.end, latest_due + timedelta(days=1))
        if due_end <= requested.start:
            if operation != "update":
                raise OperandError(
                    "findata-plugins/tushare_fund_daily range is entirely before the "
                    "publication window; nothing is due yet"
                )
            try:
                return self.loader.dataset(self.spec.name).publication_id
            except DataLoaderError:
                raise OperandError(
                    "findata-plugins/tushare_fund_daily update has no due work and the dataset is "
                    "uninitialized; run complete with a historical range first"
                ) from None
        requested = DateRange(requested.start, due_end)

        spec = self.spec
        if selectors == ["all"]:
            if operation == "refresh":
                raise OperandError("the 'all' selector is only supported by complete and update")
            self._log(f"plan: entire fund market over {requested.start.isoformat()}..{requested.end.isoformat()}; request shape: full-market per date")
            publication = self._fund_daily_all_by_date(spec, requested, existing_coverage)
            return publication or self.loader.dataset(spec.name).publication_id
        if operation == "refresh":
            for symbol in selectors:
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
            for symbol in selectors
            for interval in (
                [requested]
                if operation == "refresh"
                else _missing_for_continuity(existing_coverage.get(symbol), requested)
            )
        ]
        span = f"{requested.start.isoformat()}..{requested.end.isoformat()}"
        self._log(
            f"plan: {len(selectors)} fund(s) over {span} "
            f"({len(jobs)} job(s))"
        )
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
                self._log(f"no fund_daily data for {symbol} in {start}..{end}")
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
        return publication or self.loader.dataset(spec.name).publication_id

    def _fund_daily_all_by_date(
        self,
        spec: DatasetSpec,
        requested: DateRange,
        existing_coverage: dict[str, DateRange],
    ) -> str | None:
        next_coverage = dict(existing_coverage)
        mutations: list[DataMutation] = []
        publication: str | None = None
        batch_started = monotonic()
        targets = [
            requested.start + timedelta(days=offset)
            for offset in range((requested.end - requested.start).days)
            if (requested.start + timedelta(days=offset)).weekday() < 5
        ]
        self._progress(0, len(targets))
        for completed, target in enumerate(targets, start=1):
            table = self._fetch(trade_date=target.strftime("%Y%m%d"))
            interval = DateRange(target, target + timedelta(days=1))
            for symbol in sorted(set(table.column("ts_code").to_pylist())):
                symbol_table = table.filter(pc.equal(table["ts_code"], symbol))
                next_coverage[symbol] = _merge_interval(next_coverage.get(symbol), interval)
                mutations.append(
                    DataMutation.replace_range(
                        symbol_table,
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
            self._progress(completed, len(targets))
        if mutations:
            publication = self._commit_mutations(spec, mutations, next_coverage)
        return publication


@dataclass(frozen=True, slots=True)
class FundDailyOperationWorker(OperationWorker):
    """Pickle-safe task-process worker for the fund daily dataset."""

    def service(
        self,
        workspace: Workspace,
        client: TushareClient,
        *,
        today: date,
        now: datetime,
        reporter: OperationReporter,
        settings: dict[str, Any],
    ) -> FundDailyDatasetService:
        return FundDailyDatasetService(
            workspace,
            client,
            today=today,
            now=now,
            reporter=reporter,
            settings=settings,
        )


@dataclass(frozen=True, slots=True)
class FundDailyDatasetRuntime:
    """Dataset-scoped behavior for the Tushare fund daily plugin."""

    def operation_worker(
        self,
        workspace: Path,
        *,
        mode: str,
        today: date,
        now: datetime | None,
    ) -> FundDailyOperationWorker:
        return FundDailyOperationWorker(
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
    ) -> FundDailyDatasetService:
        return FundDailyDatasetService(
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
            f"dataset {FUND_DAILY_SPEC.name!r} has no declared dependency on {target!r}"
        )

    def update_ready(self, workspace: Workspace) -> bool:
        return True


_OPERATION_NAMES = ["update", "complete", "refresh"]
_OPERATION_HELP: dict[str, str] = {
    "update": "Extend the fund codes already covered by this dataset through the latest due "
    "trading date. Configure update_symbols to maintain a different or broader selector.",
    "complete": "Backfill or extend the requested fund codes over the range.",
    "refresh": "Re-fetch the requested fund codes strictly inside their existing resolved coverage.",
}


def normalize_operation(
    operation: str,
    operands: dict[str, Any],
    *,
    today: date,
) -> dict[str, Any]:
    values = dict(operands)
    if operation not in _OPERATION_NAMES:
        raise OperandError(f"unsupported operation {operation!r} for {FUND_DAILY_SPEC.name}")
    if operation == "update":
        _require_no_operands(values)
        return {}
    if operation == "complete" and "symbols" not in values:
        values["symbols"] = ["all"]
    timerange = _timerange(values, today=today)
    arrays = sorted(set(_string_array(values, "symbols")))
    if "all" in arrays and arrays != ["all"]:
        raise OperandError("the 'all' selector must be used on its own")
    if operation == "refresh" and arrays == ["all"]:
        raise OperandError("the 'all' selector is only supported by complete and update")
    _require_keys(values, {"symbols", "timerange"})
    return {"symbols": arrays, "timerange": _format_range(timerange)}


def dataset_description(workspace: Workspace, *, provider_ready: bool) -> dict[str, Any]:
    state, publication_id = dataset_storage_state(workspace, FUND_DAILY_SPEC.name)
    return {
        "name": FUND_DAILY_SPEC.name,
        "provider": "findata-plugins/tushare",
        "provider_ready": provider_ready,
        "capabilities": dict(FUND_DAILY_SPEC.capabilities),
        "dependencies": [],
        "settings": [
            {
                "key": f"dataset.{FUND_DAILY_SPEC.name}.update_symbols",
                "schema": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string"},
                },
                "help": "Fund codes maintained by update.",
                "required": False,
                "default": ["stored"],
                "configured": workspace.get_config(
                    f"dataset.{FUND_DAILY_SPEC.name}.update_symbols"
                )
                is not None,
            },
        ],
        "storage": "duckdb",
        "state": state,
        "publication_id": publication_id,
        "operations": [operation_description(name) for name in _OPERATION_NAMES],
    }


def operation_description(operation: str) -> dict[str, Any]:
    if operation not in _OPERATION_NAMES:
        raise OperandError(f"unsupported operation {operation!r} for {FUND_DAILY_SPEC.name}")
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
                "help": "Use all to fetch the entire fund market for every trading date in the range.",
                **({"default": ["all"]} if operation == "complete" else {}),
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
    normalized = normalize_operation(operation, operands, today=today)
    strategy = "stored-coverage update" if operation == "update" else "plugin operation"
    estimated_requests: int | None = None
    if operation != "update":
        estimated_requests = len(normalized.get("symbols", []))
    return {
        "dry_run": True,
        "dataset": FUND_DAILY_SPEC.name,
        "operation": operation,
        "operands": normalized,
        "strategy": strategy,
        "estimated_provider_requests": estimated_requests,
        "dependencies": [],
        "side_effects": False,
    }
