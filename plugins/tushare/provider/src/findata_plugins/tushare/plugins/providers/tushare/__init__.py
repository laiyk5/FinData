"""Official Tushare provider plugin for findata."""

from findata_plugins.tushare.plugins.providers.tushare.provider import (
    TushareProviderRuntime,
    tushare_provider_plugin,
)

__all__ = ["TushareProviderRuntime", "tushare_provider_plugin"]
