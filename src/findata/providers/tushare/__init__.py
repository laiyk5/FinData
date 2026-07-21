from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

import pyarrow as pa

from findata.contracts import DatasetDataError
from findata.datasets.tushare import TUSHARE_DATASETS
from findata.plugins import ProviderPlugin
from findata.rate_limit import FileRateLimiter
from findata.storage import Workspace


class TushareProviderRuntime:
    def operation_worker(
        self, workspace: Any, *, mode: str, today: Any, now: Any
    ) -> Any:
        from findata.datasets.tushare.operations import OperationWorker

        return OperationWorker(
            workspace=workspace,
            provider=mode,
            token="mock-token" if mode == "mock" else "",
            today=today.isoformat(),
            now=now.isoformat(),
        )

    def normalize_operation(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        from findata.datasets.tushare.operations import normalize_operation

        return normalize_operation(*args, **kwargs)

    def dataset_description(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        from findata.datasets.tushare.operations import dataset_description

        return dataset_description(*args, **kwargs)

    def operation_description(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        from findata.datasets.tushare.operations import operation_description

        return operation_description(*args, **kwargs)

    def resolve_dependency(self, *args: Any, **kwargs: Any) -> tuple[str, dict[str, object]]:
        from findata.datasets.tushare.operations import resolve_v1_dependency

        return resolve_v1_dependency(*args, **kwargs)

    def token(self, workspace: Workspace) -> str:
        configured = workspace.get_config("provider.tushare.token")
        if isinstance(configured, dict) and isinstance(configured.get("env"), str):
            return os.environ.get(configured["env"], "")
        return str(configured or "")

    def is_mock(self, workspace: Workspace, forced_mode: str) -> bool:
        from findata.testing.tushare import is_mock_token

        return forced_mode == "mock" or is_mock_token(self.token(workspace))

    def ready(self, workspace: Workspace, forced_mode: str) -> bool:
        return self.is_mock(workspace, forced_mode) or bool(self.token(workspace))

    def probe(self, workspace: Workspace, *, today: Any) -> None:
        limiter = FileRateLimiter(
            workspace.root / "providers" / "tushare-rate.json",
            limit=int(workspace.get_config("provider.tushare.rate_limit", 500)),
            period=60,
        )
        client = TushareClient(
            token=self.token(workspace),
            transport=TushareHTTPTransport(),
            permit=limiter.acquire,
        )
        day = today.strftime("%Y%m%d")
        client.query("tushare_trade_cal", exchange="SSE", start_date=day, end_date=day)


def tushare_provider_plugin() -> ProviderPlugin:
    return ProviderPlugin(
        provider_id="tushare",
        configuration_schema={
            "type": "object",
            "properties": {
                "token": {"type": ["string", "object"]},
                "rate_limit": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=("token",),
        rate_limit=500,
        period=60,
        runtime=TushareProviderRuntime(),
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

    def query(self, dataset: str, **params: Any) -> pa.Table:
        spec = TUSHARE_DATASETS[dataset]
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
                time.sleep(self._retry_delay * (2 ** (attempt - 1)))
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
