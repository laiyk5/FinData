"""Shared Tushare operation plumbing used by the per-dataset plugin packages."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from importlib.metadata import entry_points
import json
from pathlib import Path
import re
from time import monotonic, sleep
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pyarrow as pa

from findata.contracts import (
    DatasetDataError,
    DateRange,
    DatasetSpec,
    OperandError,
    OperationReporter,
    OperationRequest,
)
from findata.loader import (
    DataLoader,
    DatasetNotReadyError,
    UnsupportedCoverageError,
)
from findata.plugins import DatasetPlugin
from findata.toolkit import FileRateLimiter
from findata.storage import (
    DATABASE_NAME,
    Coverage,
    DataMutation,
    DatasetGate,
    Workspace,
    load_metadata,
)
from findata_plugins.shared.testing import (
    MOCK_TOKEN,
    MockTushareTransport,
    is_mock_token,
    transport_from_mock_token,
)


class TushareAPIError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(f"Tushare API error {code}: {message}")


class ProviderProtocolError(RuntimeError):
    """A provider response does not match its registered contract."""


Transport = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class TushareHTTPTransport:
    """Credentialed transport. Tests must inject a mock unless humans opt in."""

    def __init__(self, endpoint: str = "https://api.tushare.pro", timeout: float = 30.0) -> None:
        self.endpoint = endpoint
        self.timeout = timeout

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = Request(
            self.endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
            result = json.loads(response.read().decode("utf-8"))
        if not isinstance(result, Mapping):
            raise ProviderProtocolError("Tushare response root is not an object")
        return result


class TushareClient:
    def __init__(
        self,
        *,
        token: str,
        transport: Transport,
        permit: Callable[[], None] | None = None,
        max_attempts: int = 3,
        retry_delay: float = 0.25,
    ) -> None:
        if not token:
            raise ValueError("Tushare token is required")
        self._token = token
        self._transport = transport
        self._permit = permit
        if max_attempts <= 0 or retry_delay < 0:
            raise ValueError("retry settings are invalid")
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay

    def __repr__(self) -> str:
        return f"{type(self).__name__}(token=<redacted>, transport={self._transport!r})"

    @property
    def checkpoint_request_limit(self) -> int | None:
        value = getattr(self._transport, "checkpoint_request_limit", None)
        return value if isinstance(value, int) and value > 0 else None

    def query(self, spec: DatasetSpec, **params: Any) -> pa.Table:
        payload = {
            "api_name": spec.api_name,
            "token": self._token,
            "params": dict(params),
            "fields": ",".join(spec.provider_fields),
        }
        for attempt in range(1, self._max_attempts + 1):
            if self._permit is not None:
                self._permit()
            try:
                response = self._transport(payload)
                break
            except (URLError, TimeoutError):
                if attempt == self._max_attempts:
                    raise
                sleep(self._retry_delay * (2 ** (attempt - 1)))
        if not isinstance(response, Mapping):
            raise ProviderProtocolError("Tushare response root is not an object")
        code = response.get("code")
        if not isinstance(code, int):
            raise ProviderProtocolError("Tushare response code is missing or invalid")
        if code != 0:
            message = str(response.get("msg") or "unknown error").replace(self._token, "<redacted>")
            raise TushareAPIError(code, message)
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise ProviderProtocolError("Tushare response data is missing or invalid")
        fields = data.get("fields")
        items = data.get("items")
        if not isinstance(fields, list) or not isinstance(items, list):
            raise ProviderProtocolError("Tushare response fields/items are missing or invalid")
        try:
            return spec.table_from_response(fields, items)
        except DatasetDataError as exc:
            raise ProviderProtocolError(str(exc)) from exc


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

    def service(
        self,
        workspace: Workspace,
        client: TushareClient,
        *,
        today: date,
        now: datetime,
        reporter: OperationReporter,
        settings: dict[str, Any],
    ) -> "TushareDatasetService":
        """Build the dataset's own operation engine; one subclass per dataset."""
        raise NotImplementedError

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
            configured = workspace.get_config("provider.findata-plugins/tushare.token")
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
        rate_limit = int(workspace.get_config("provider.findata-plugins/tushare.rate_limit", 500))
        limiter = FileRateLimiter(
            workspace.root / "providers" / "tushare-rate.json",
            limit=rate_limit,
            period=60,
        )

        def permit() -> None:
            limiter.acquire(checkpoint=context.checkpoint, waiting=context.waiting)
            context.running()

        service = self.service(
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
            str(request["operation"]),
            dict(request.get("operands") or {}),
        )
        context.checkpoint()
        context.log(f"published {result.publication_id}")
        return asdict(result)


class TushareDatasetService:
    """Synchronous operation engine used by task processes and deterministic tests.

    Each Tushare dataset package subclasses this base with its own spec,
    planning, and operation logic; ``run`` executes exactly that one dataset.
    """

    spec: DatasetSpec

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
        suffix = (
            "update_indexes"
            if dataset == "findata-plugins/tushare_index_weight"
            else "update_symbols"
        )
        key = f"dataset.{dataset}.{suffix}"
        value = (
            self._settings.get(key, [])
            if self._settings is not None
            else self.workspace.get_config(key, [])
        )
        return list(value) if isinstance(value, list) else []

    def _plan(self, operation: str, values: dict[str, Any]) -> None:
        """Validate and preview the operation; implemented per dataset."""
        raise NotImplementedError

    def _dispatch(self, operation: str, values: dict[str, Any]) -> str:
        """Execute the operation for this service's dataset; returns a publication."""
        raise NotImplementedError

    def _dependency_service(self, dataset: str) -> "TushareDatasetService":
        """Sibling engine fulfilling a declared dependency, found via entry points.

        Dataset packages never import each other; the sibling runtime is
        discovered through its installed entry point and builds its own
        service around this service's workspace and client.
        """
        runtime = _load_dataset_plugin(dataset).runtime
        return runtime.operation_service(
            self.workspace,
            self.client,
            today=self.today,
            now=self.now,
            settings=self._settings,
        )

    def _fulfill(self, dataset: str, requirement: dict[str, Any]) -> None:
        if self._reporter is not None and hasattr(self._reporter, "fulfill"):
            self._reporter.fulfill(dataset, requirement)
            return
        service = self._dependency_service(dataset)
        service._dispatch("complete", dict(requirement))
        self._request_count += service._request_count
        self._row_count += service._row_count
        self._checkpoint_count += service._checkpoint_count

    def run(
        self, operation: str = "update", operands: dict[str, Any] | None = None
    ) -> OperationResult:
        started = monotonic()
        before_requests = self._request_count
        before_rows = self._row_count
        before_checkpoints = self._checkpoint_count
        values = dict(operands or {})
        # Execution and dry-run share validation and planning. Mutable state is
        # read again here so a previous preview is never treated as a reservation.
        self._plan(operation, values)
        publication = self._dispatch(operation, values)
        self._log(
            f"completed {self.spec.name} {operation}: "
            f"{self._request_count - before_requests} requests, "
            f"{self._row_count - before_rows} rows, "
            f"{self._checkpoint_count - before_checkpoints} checkpoints "
            f"in {monotonic() - started:.1f}s → publication {publication}"
        )
        return OperationResult(
            self.spec.name, operation, publication, self._request_count - before_requests
        )

    def _log(self, message: str) -> None:
        if self._reporter is not None:
            self._reporter.log(message)

    def _fetch(self, **params: Any) -> pa.Table:
        spec = self.spec
        shape = f"{spec.api_name}({', '.join(f'{key}={value}' for key, value in params.items())})"
        if self._reporter is not None:
            self._reporter.checkpoint()
            self._reporter.log(f"fetch {shape}")
            if hasattr(self._reporter, "stage"):
                self._reporter.stage(f"fetching:{spec.name}")
            if hasattr(self._reporter, "begin_subtask"):
                self._reporter.begin_subtask(timeout=180)
        self._request_count += 1
        try:
            table = self.client.query(spec, **params)
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

    def _ensure_index_metadata(self, references: list[str]) -> None:
        missing: list[str] = []
        for reference in references:
            code = _canonical_index(reference)
            try:
                found = (
                    self.loader.dataset("findata-plugins/tushare_index_basic")
                    .query(filters=[("ts_code", "=", code)])
                    .num_rows
                )
            except DatasetNotReadyError:
                found = 0
            if not found:
                missing.append(reference)
        if not missing:
            return
        self._fulfill("findata-plugins/tushare_index_basic", {"indexes": missing})

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


def dataset_storage_state(workspace: Workspace, dataset: str) -> tuple[str, Any]:
    """Registered state and publication ID read from the dataset database metadata."""
    dataset_root = workspace.datasets_root / dataset
    database = dataset_root / DATABASE_NAME
    state = "unregistered"
    publication_id = None
    if database.exists():
        with DatasetGate(dataset_root / "gate.lock", exclusive=False):
            metadata = load_metadata(database)
        state = str(metadata.get("state"))
        publication_id = metadata.get("publication_id")
    return state, publication_id


def _load_dataset_plugin(dataset: str) -> DatasetPlugin:
    """Load the installed dataset plugin with the given full name via entry points."""
    for point in entry_points(group="findata.datasets"):
        loaded = point.load()
        plugin = loaded() if callable(loaded) and not isinstance(loaded, DatasetPlugin) else loaded
        if plugin.name == dataset:
            return plugin
    raise KeyError(dataset)


def dependency_states(workspace: Workspace, datasets: list[str]) -> list[dict[str, Any]]:
    """Read-only registered state of each declared dependency, via entry points."""
    return [
        {
            "dataset": name,
            "state": _load_dataset_plugin(name).runtime.dataset_description(
                workspace, provider_ready=True
            )["state"],
        }
        for name in datasets
    ]


_OPERAND_HELP: dict[str, str] = {
    "symbols": "Tushare security codes like 600000.SH, or constituent selectors "
    "tushare:<ts_code>[@latest|@YYYYMM].",
    "indexes": "Index references spelled tushare:<ts_code>, for example tushare:000300.SH.",
    "exchanges": "Exchanges to maintain; SSE and/or SZSE.",
    "timerange": "Half-open YYYY-MM-DD:YYYY-MM-DD range; the end is exclusive and today "
    "resolves in the dataset timezone.",
}


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


_SECURITY = re.compile(r"^[0-9]{6}\.(?:SH|SZ|BJ)$")
_INDEX_REFERENCE = re.compile(r"^tushare:([^@]+)(?:@(latest|[0-9]{6}))?$")


def _setting_array(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("setting must be a nonempty JSON array")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError("setting entries must be nonempty strings")
    return list(dict.fromkeys(value))


def _materialized(workspace: Any, code: str) -> bool:
    try:
        return (
            DataLoader(workspace.root)
            .dataset("findata-plugins/tushare_index_basic")
            .query(filters=[("ts_code", "=", code)])
            .num_rows
            > 0
        )
    except DatasetNotReadyError:
        return False


def _index_code(value: str, *, allow_suffix: bool) -> str:
    match = _INDEX_REFERENCE.fullmatch(value)
    if match is None or (not allow_suffix and match.group(2) is not None):
        raise ValueError(f"invalid Tushare index reference {value!r}")
    suffix = match.group(2)
    if suffix and suffix != "latest" and not 1 <= int(suffix[4:]) <= 12:
        raise ValueError(f"invalid Tushare index reference {value!r}")
    return match.group(1)
