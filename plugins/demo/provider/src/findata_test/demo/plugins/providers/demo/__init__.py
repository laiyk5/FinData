"""Demo provider plugin for findata."""

from findata_test.demo.plugins.providers.demo.provider import (
    DemoProviderRuntime,
    demo_provider_plugin,
)

__all__ = ["DemoProviderRuntime", "demo_provider_plugin"]
