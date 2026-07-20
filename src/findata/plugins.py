from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Iterable

from findata.contracts import DatasetSpec
from findata.storage import Workspace


class PluginRegistrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DatasetPlugin:
    name: str
    provider: str
    spec: DatasetSpec
    storage_strategy: str
    operations: tuple[str, ...]
    dependencies: tuple[str, ...] = ()


def discover_dataset_plugins() -> list[DatasetPlugin]:
    discovered: list[DatasetPlugin] = []
    for point in entry_points(group="findata.datasets"):
        loaded = point.load()
        value = loaded() if callable(loaded) and not isinstance(loaded, DatasetPlugin) else loaded
        if not isinstance(value, DatasetPlugin):
            raise PluginRegistrationError(f"entry point {point.name!r} did not return DatasetPlugin")
        discovered.append(value)
    if not discovered:
        from findata.datasets.tushare import builtin_plugins

        discovered = builtin_plugins()
    validate_plugins(discovered)
    return discovered


def validate_plugins(plugins: Iterable[DatasetPlugin]) -> None:
    values = list(plugins)
    by_name: dict[str, DatasetPlugin] = {}
    for plugin in values:
        if plugin.name in by_name:
            raise PluginRegistrationError(f"duplicate dataset plugin {plugin.name!r}")
        if plugin.name != plugin.spec.name:
            raise PluginRegistrationError(f"plugin/spec name mismatch for {plugin.name!r}")
        if plugin.provider != "tushare":
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


def register_plugins(workspace: Workspace, plugins: Iterable[DatasetPlugin]) -> None:
    values = list(plugins)
    validate_plugins(values)
    for plugin in values:
        workspace.register_dataset(
            plugin.name,
            strategy=plugin.storage_strategy,
            spec=plugin.spec,
        )
