from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from types import MappingProxyType
from typing import Any, Iterable

from findata.contracts import DatasetSpec
from findata.storage import Workspace


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


SettingNormalizer = Callable[[Any, Workspace], Any]


@dataclass(frozen=True, slots=True)
class SettingSpec:
    schema: Mapping[str, Any]
    normalize: SettingNormalizer
    help: str


@dataclass(frozen=True, slots=True)
class DatasetPlugin:
    name: str
    provider: str
    spec: DatasetSpec
    storage_strategy: str
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
        if provider.runtime is None:
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
            raise PluginRegistrationError(f"entry point {point.name!r} did not return DatasetPlugin")
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
        if plugin.storage_strategy not in {"single-file-csv", "partitioned-parquet"}:
            raise PluginRegistrationError(f"unknown storage strategy {plugin.storage_strategy!r}")
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
            strategy=plugin.storage_strategy,
            spec=plugin.spec,
        )
