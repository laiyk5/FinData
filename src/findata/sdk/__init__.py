"""Plugin SDK — single import surface for plugin authors.

Usage::

    from findata.sdk import (
        Coverage, DataLoader, DataMutation, DatasetPlugin, DatasetRuntimeBase,
        DatasetSpec, DateRange, OperandError, OperationReporter, OperationRequest,
        OperationWorker, PluginLoadError, PluginRegistrationError, ProviderPlugin,
        ProviderRuntime, SettingSpec, Workspace,
        discover_dataset_plugins, discover_dataset_plugins_safe,
        discover_provider_plugins, discover_provider_plugins_safe,
        plugin_blocklist, plugin_load_errors,
        register_plugins, validate_plugins, validate_provider_plugins,
    )
"""

from findata.sdk.contracts import (
    DatasetSpec,
    DateRange,
    OperandError,
    OperationReporter,
    OperationRequest,
    OperationWorker,
)
from findata.sdk.loader import DataLoader
from findata.sdk.plugins import (
    DatasetPlugin,
    DatasetRuntime,
    DatasetRuntimeBase,
    PluginLoadError,
    PluginRegistrationError,
    ProviderPlugin,
    ProviderRuntime,
    SettingSpec,
    discover_dataset_plugins,
    discover_dataset_plugins_safe,
    discover_provider_plugins,
    discover_provider_plugins_safe,
    plugin_blocklist,
    plugin_load_errors,
    register_plugins,
    validate_plugins,
    validate_provider_plugins,
)
from findata.storage import Coverage, DataMutation, Workspace

__all__ = [
    "Coverage",
    "DataLoader",
    "DataMutation",
    "DatasetPlugin",
    "DatasetRuntime",
    "DatasetRuntimeBase",
    "DatasetSpec",
    "DateRange",
    "OperandError",
    "OperationReporter",
    "OperationRequest",
    "OperationWorker",
    "PluginLoadError",
    "PluginRegistrationError",
    "ProviderPlugin",
    "ProviderRuntime",
    "SettingSpec",
    "Workspace",
    "discover_dataset_plugins",
    "discover_dataset_plugins_safe",
    "discover_provider_plugins",
    "discover_provider_plugins_safe",
    "plugin_blocklist",
    "plugin_load_errors",
    "register_plugins",
    "validate_plugins",
    "validate_provider_plugins",
]
