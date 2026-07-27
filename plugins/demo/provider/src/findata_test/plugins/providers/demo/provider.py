"""Demo provider — always ready, never rate-limited, generates fake data."""

from __future__ import annotations

from datetime import date

from findata.sdk.plugins import ProviderPlugin, ProviderRuntime
from findata.storage import Workspace


class DemoProviderRuntime(ProviderRuntime):
    """A provider runtime that requires no credentials and is always ready."""

    def ready(self, workspace: Workspace, mode: str) -> bool:
        return True

    def is_mock(self, workspace: Workspace, mode: str) -> bool:
        return True

    def probe(self, workspace: Workspace, *, today: date) -> None:
        pass


def demo_provider_plugin() -> ProviderPlugin:
    return ProviderPlugin(
        provider_id="findata-test/demo",
        configuration_schema={
            "type": "object",
            "properties": {
                "greeting": {
                    "type": "string",
                    "description": "Optional greeting for log messages",
                },
            },
        },
        secret_fields=(),
        rate_limit=1000,
        period=60,
        runtime=DemoProviderRuntime(),
    )
