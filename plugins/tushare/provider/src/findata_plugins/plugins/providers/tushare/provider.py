from __future__ import annotations

import os
from datetime import date

from findata.plugins import ProviderPlugin, ProviderRuntime
from findata.toolkit import FileRateLimiter
from findata.storage import Workspace
from findata_plugins.shared.engine import (
    ProviderProtocolError,
    TushareAPIError,
    TushareHTTPTransport,
)


class TushareProviderRuntime(ProviderRuntime):
    def token(self, workspace: Workspace) -> str:
        configured = workspace.get_config("provider.findata-plugins/tushare.token")
        if isinstance(configured, dict) and isinstance(configured.get("env"), str):
            return os.environ.get(configured["env"], "")
        return str(configured or "")

    def is_mock(self, workspace: Workspace, mode: str) -> bool:
        from findata_plugins.shared.testing import is_mock_token

        return mode == "mock" or is_mock_token(self.token(workspace))

    def ready(self, workspace: Workspace, mode: str) -> bool:
        return self.is_mock(workspace, mode) or bool(self.token(workspace))

    def probe(self, workspace: Workspace, *, today: date) -> None:
        limiter = FileRateLimiter(
            workspace.root / "providers" / "tushare-rate.json",
            limit=int(workspace.get_config("provider.findata-plugins/tushare.rate_limit", 500)),
            period=60,
        )
        token = self.token(workspace)
        day = today.strftime("%Y%m%d")
        # A readiness probe needs no dataset contract, only an authenticated
        # response from the provider's own API.
        payload = {
            "api_name": "trade_cal",
            "token": token,
            "params": {"exchange": "SSE", "start_date": day, "end_date": day},
            "fields": "exchange,cal_date,is_open",
        }
        limiter.acquire()
        response = TushareHTTPTransport()(payload)
        code = response.get("code")
        if not isinstance(code, int):
            raise ProviderProtocolError("Tushare response code is missing or invalid")
        if code != 0:
            message = str(response.get("msg") or "unknown error").replace(token, "<redacted>")
            raise TushareAPIError(code, message)


def tushare_provider_plugin() -> ProviderPlugin:
    return ProviderPlugin(
        provider_id="findata-plugins/tushare",
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
