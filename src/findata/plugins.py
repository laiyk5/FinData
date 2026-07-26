from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from importlib.metadata import entry_points
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Protocol, runtime_checkable

from findata.contracts import (
    DatasetSpec,
    OperationReporter,
    OperationRequest,
    OperationWorker,
)
from findata.storage import Workspace

__all__ = [
    "DatasetPlugin",
    "DatasetSpec",
    "OperationReporter",
    "OperationRequest",
    "OperationWorker",
    "PluginRegistrationError",
    "ProviderPlugin",
    "ProviderRuntime",
    "SettingSpec",
    "discover_dataset_plugins",
    "discover_provider_plugins",
    "register_plugins",
    "validate_plugins",
    "validate_provider_plugins",
]


class PluginRegistrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderPlugin:
    provider_id: str
    configuration_schema: Mapping[str, Any]
    secret_fields: tuple[str, ...]
    rate_limit: int
    period: int
    runtime: object | None = None


@runtime_checkable
class ProviderRuntime(Protocol):
    """Behavior contract the server calls on a provider plugin's runtime object.

    The built-in Tushare runtime (findata.providers.tushare) is the reference
    implementation; findata.contracts documents the worker request and reporter.
    """

    def operation_worker(
        self,
        workspace: Path,
        *,
        mode: str,
        today: date,
        now: datetime | None,
    ) -> OperationWorker:
        """Return the pickle-safe worker callable executed in a task subprocess."""
        ...

    def normalize_operation(
        self,
        dataset: str,
        operation: str,
        operands: dict[str, Any],
        *,
        today: date,
    ) -> dict[str, Any]:
        """Canonicalize and validate operands; raises OperandError on bad input."""
        ...

    def plan_operation(
        self,
        workspace: Workspace,
        dataset: str,
        operation: str,
        operands: dict[str, Any],
        *,
        today: date,
    ) -> dict[str, Any]:
        """Return the dry-run plan for normalized operands without side effects."""
        ...

    def dataset_description(
        self,
        workspace: Workspace,
        dataset: str,
        *,
        provider_ready: bool,
    ) -> dict[str, Any]:
        """Return the describe/status payload for one dataset."""
        ...

    def operation_description(self, dataset: str, operation: str) -> dict[str, Any]:
        """Return the operand JSON schema and per-operand help for one operation."""
        ...

    def resolve_dependency(
        self,
        parent: str,
        target: str,
        requirement: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        """Map a dependency requirement to the fulfilling dataset and operands."""
        ...

    def ready(self, workspace: Workspace, mode: str) -> bool:
        """Whether the provider is configured well enough to accept work."""
        ...

    def is_mock(self, workspace: Workspace, mode: str) -> bool:
        """Whether the provider serves mock responses in this mode."""
        ...

    def probe(self, workspace: Workspace, *, today: date) -> None:
        """Run the optional authenticated readiness probe through the limiter."""
        ...


SettingNormalizer = Callable[[Any, Workspace], Any]


@dataclass(frozen=True, slots=True)
class SettingSpec:
    schema: Mapping[str, Any]
    normalize: SettingNormalizer
    help: str
    required: bool = False


@dataclass(frozen=True, slots=True)
class DatasetPlugin:
    name: str
    provider: str
    spec: DatasetSpec
    operations: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    settings: Mapping[str, SettingSpec] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))

    def normalize_setting(self, key: str, value: Any, *, workspace: Workspace) -> Any:
        try:
            setting = self.settings[key]
        except KeyError as exc:
            raise ValueError(f"unknown setting {key!r} for {self.name}") from exc
        return setting.normalize(value, workspace)


def discover_provider_plugins() -> list[ProviderPlugin]:
    discovered: list[ProviderPlugin] = []
    for point in entry_points(group="findata.providers"):
        loaded = point.load()
        value = loaded() if callable(loaded) and not isinstance(loaded, ProviderPlugin) else loaded
        if not isinstance(value, ProviderPlugin):
            raise PluginRegistrationError(
                f"entry point {point.name!r} did not return ProviderPlugin"
            )
        discovered.append(value)
    validate_provider_plugins(discovered)
    return discovered


def validate_provider_plugins(providers: Iterable[ProviderPlugin]) -> None:
    seen: set[str] = set()
    for provider in providers:
        if not provider.provider_id or provider.provider_id in seen:
            raise PluginRegistrationError(f"duplicate provider {provider.provider_id!r}")
        seen.add(provider.provider_id)
        if not isinstance(provider.configuration_schema, Mapping):
            raise PluginRegistrationError(
                f"provider {provider.provider_id!r} has malformed configuration schema"
            )
        if provider.rate_limit <= 0 or provider.period <= 0:
            raise PluginRegistrationError(
                f"provider {provider.provider_id!r} rate limit must be positive"
            )
        if provider.runtime is None or not isinstance(provider.runtime, ProviderRuntime):
            raise PluginRegistrationError(
                f"provider {provider.provider_id!r} has no readiness runtime"
            )


def discover_dataset_plugins(
    *, providers: Iterable[ProviderPlugin] | None = None
) -> list[DatasetPlugin]:
    discovered: list[DatasetPlugin] = []
    for point in entry_points(group="findata.datasets"):
        loaded = point.load()
        value = loaded() if callable(loaded) and not isinstance(loaded, DatasetPlugin) else loaded
        if not isinstance(value, DatasetPlugin):
            raise PluginRegistrationError(
                f"entry point {point.name!r} did not return DatasetPlugin"
            )
        discovered.append(value)
    validate_plugins(discovered, providers=providers)
    return discovered


def validate_plugins(
    plugins: Iterable[DatasetPlugin], *, providers: Iterable[ProviderPlugin] | None = None
) -> None:
    values = list(plugins)
    provider_ids = {provider.provider_id for provider in providers or ()}
    by_name: dict[str, DatasetPlugin] = {}
    for plugin in values:
        if plugin.name in by_name:
            raise PluginRegistrationError(f"duplicate dataset plugin {plugin.name!r}")
        if plugin.name != plugin.spec.name:
            raise PluginRegistrationError(f"plugin/spec name mismatch for {plugin.name!r}")
        if plugin.provider not in provider_ids:
            raise PluginRegistrationError(f"unknown provider {plugin.provider!r}")
        if "update" not in plugin.operations:
            raise PluginRegistrationError(f"{plugin.name} must declare a parameterless update")
        by_name[plugin.name] = plugin
    for plugin in values:
        missing = set(plugin.dependencies) - set(by_name)
        if missing:
            raise PluginRegistrationError(
                f"{plugin.name} has missing dependencies {sorted(missing)!r}"
            )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise PluginRegistrationError(f"dependency cycle includes {name!r}")
        if name in visited:
            return
        visiting.add(name)
        for dependency in by_name[name].dependencies:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in by_name:
        visit(name)


def register_plugins(
    workspace: Workspace,
    plugins: Iterable[DatasetPlugin],
    *,
    providers: Iterable[ProviderPlugin],
) -> None:
    values = list(plugins)
    validate_plugins(values, providers=providers)
    for plugin in values:
        workspace.register_dataset(
            plugin.name,
            spec=plugin.spec,
        )
