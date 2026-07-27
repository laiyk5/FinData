"""Always-ready provider for mycompany/hello."""

from datetime import date
from findata.sdk import ProviderPlugin, ProviderRuntime
from findata.storage import Workspace


class HelloRuntime(ProviderRuntime):
    """Always-ready provider — no credentials required."""

    def ready(self, workspace: Workspace, mode: str) -> bool:
        return True

    def is_mock(self, workspace: Workspace, mode: str) -> bool:
        return True

    def probe(self, workspace: Workspace, *, today: date) -> None:
        pass


def provider_plugin() -> ProviderPlugin:
    return ProviderPlugin(
        provider_id="mycompany/hello",
        configuration_schema={"type": "object", "properties": {}},
        secret_fields=(),
        rate_limit=1000,
        period=60,
        runtime=HelloRuntime(),
    )
