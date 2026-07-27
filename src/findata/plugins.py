from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from importlib.metadata import entry_points
import logging
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Protocol, runtime_checkable

from findata.contracts import (
    DatasetSpec,
    OperationReporter,
    OperationRequest,
    OperationWorker,
    plugin_namespace,
    validate_dataset_name,
)
from findata.storage import Workspace

__all__ = [
    "DatasetPlugin",
    "DatasetRuntime",
    "DatasetSpec",
    "OperationReporter",
    "OperationRequest",
    "OperationWorker",
    "PluginRegistrationError",
    "PluginWorkerDispatcher",
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


logger = logging.getLogger(__name__)

PLUGIN_BLOCKLIST_KEY = "plugins.blocked"


def plugin_blocklist(workspace: Workspace) -> list[str]:
    """Read the workspace plugin blocklist (dataset full names or provider IDs)."""
    value = workspace.get_config(PLUGIN_BLOCKLIST_KEY, [])
    if not isinstance(value, list):
        logger.warning("%s must be a JSON array; ignoring it", PLUGIN_BLOCKLIST_KEY)
        return []
    return [str(item) for item in value]


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
    """Provider-scoped behavior contract for a provider plugin's runtime object.

    Dataset-scoped behavior lives on DatasetRuntime; the built-in Tushare
    runtimes (findata_tushare_provider.provider and the findata_tushare_*
    dataset packages) are the reference implementations.
    """

    def ready(self, workspace: Workspace, mode: str) -> bool:
        """Whether the provider is configured well enough to accept work."""
        ...

    def is_mock(self, workspace: Workspace, mode: str) -> bool:
        """Whether the provider serves mock responses in this mode."""
        ...

    def probe(self, workspace: Workspace, *, today: date) -> None:
        """Run the optional authenticated readiness probe through the limiter."""
        ...


@runtime_checkable
class DatasetRuntime(Protocol):
    """Dataset-scoped behavior contract carried by every DatasetPlugin."""

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
        *,
        provider_ready: bool,
    ) -> dict[str, Any]:
        """Return the describe/status payload for this dataset."""
        ...

    def operation_description(self, operation: str) -> dict[str, Any]:
        """Return the operand JSON schema and per-operand help for one operation."""
        ...

    def resolve_dependency(
        self,
        target: str,
        requirement: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        """Map a dependency requirement to the fulfilling dataset and operands."""
        ...

    def update_ready(self, workspace: Workspace) -> bool:
        """Whether settings and committed state allow parameterless update."""
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
    runtime: DatasetRuntime
    operations: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    settings: Mapping[str, SettingSpec] = field(default_factory=dict)
    schedule: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))

    def normalize_setting(self, key: str, value: Any, *, workspace: Workspace) -> Any:
        try:
            setting = self.settings[key]
        except KeyError as exc:
            raise ValueError(f"unknown setting {key!r} for {self.name}") from exc
        return setting.normalize(value, workspace)


def _entry_namespace(point: Any) -> str:
    """The plugin namespace implied by an entry point's module (top-level package)."""
    return str(point.module).split(".", 1)[0].replace("_", "-")


def _validate_entry_name(point: Any, full_name: str, *, kind: str) -> None:
    """A plugin's full name must be `<module namespace>/<entry-point name>`."""
    expected = f"{_entry_namespace(point)}/{point.name}"
    if full_name != expected:
        raise PluginRegistrationError(
            f"{kind} full name {full_name!r} must be {expected!r} to match the "
            "namespace of the package it lives in"
        )


def discover_provider_plugins() -> list[ProviderPlugin]:
    discovered: list[ProviderPlugin] = []
    for point in entry_points(group="findata.providers"):
        loaded = point.load()
        value = loaded() if callable(loaded) and not isinstance(loaded, ProviderPlugin) else loaded
        if not isinstance(value, ProviderPlugin):
            raise PluginRegistrationError(
                f"entry point {point.name!r} did not return ProviderPlugin"
            )
        _validate_entry_name(point, value.provider_id, kind="provider")
        discovered.append(value)
    validate_provider_plugins(discovered)
    return discovered


def validate_provider_plugins(providers: Iterable[ProviderPlugin]) -> None:
    seen: set[str] = set()
    for provider in providers:
        if not provider.provider_id or provider.provider_id in seen:
            raise PluginRegistrationError(f"duplicate provider {provider.provider_id!r}")
        seen.add(provider.provider_id)
        validate_dataset_name(provider.provider_id)
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
        _validate_entry_name(point, value.name, kind="dataset")
        discovered.append(value)
    # Entry points from several distributions arrive in installation order;
    # sort by dataset name so listing and registration order stay deterministic.
    discovered.sort(key=lambda plugin: plugin.name)
    provider_ids = {provider.provider_id for provider in providers or ()}
    resolved = _resolve_dependencies(discovered, provider_ids)
    validate_plugins(resolved, providers=providers)
    return resolved


def _resolve_dependencies(
    plugins: Iterable[DatasetPlugin], provider_ids: set[str] | None = None
) -> list[DatasetPlugin]:
    """Resolve namespace-relative dependency and provider names (idempotent)."""
    values = list(plugins)
    known = {plugin.name for plugin in values}
    provider_ids = provider_ids or set()
    resolved: list[DatasetPlugin] = []
    for plugin in values:
        namespace = plugin_namespace(plugin.name)
        full = tuple(
            dependency if dependency in known else f"{namespace}/{dependency}"
            for dependency in plugin.dependencies
        )
        provider = plugin.provider
        if provider not in provider_ids and f"{namespace}/{provider}" in provider_ids:
            provider = f"{namespace}/{provider}"
        resolved.append(replace(plugin, dependencies=full, provider=provider))
    return resolved


def validate_plugins(
    plugins: Iterable[DatasetPlugin], *, providers: Iterable[ProviderPlugin] | None = None
) -> None:
    provider_ids = {provider.provider_id for provider in providers or ()}
    values = _resolve_dependencies(list(plugins), provider_ids)
    by_name: dict[str, DatasetPlugin] = {}
    for plugin in values:
        if plugin.name in by_name:
            raise PluginRegistrationError(f"duplicate dataset plugin {plugin.name!r}")
        validate_dataset_name(plugin.name)
        if plugin.name != plugin.spec.name:
            raise PluginRegistrationError(f"plugin/spec name mismatch for {plugin.name!r}")
        if not isinstance(plugin.runtime, DatasetRuntime):
            raise PluginRegistrationError(f"{plugin.name} runtime does not satisfy DatasetRuntime")
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


def apply_plugin_blocklist(
    plugins: Iterable[DatasetPlugin],
    providers: Iterable[ProviderPlugin],
    blocked: Iterable[str],
    *,
    warn: Callable[[str], None] | None = None,
) -> tuple[list[DatasetPlugin], list[ProviderPlugin]]:
    """Filter installed plugins by a workspace blocklist.

    A blocked plugin still mounts when an unblocked plugin requires it (a
    dataset's declared dependencies, or any mounted dataset's provider); each
    such repair and each unknown blocklist entry produces a warning.
    """
    entries = set(blocked)
    all_providers = list(providers)
    all_plugins = _resolve_dependencies(
        list(plugins), {provider.provider_id for provider in all_providers}
    )

    def note(message: str) -> None:
        if warn is not None:
            warn(message)

    known = {plugin.name for plugin in all_plugins} | {
        provider.provider_id for provider in all_providers
    }
    for entry in sorted(entries - known):
        note(f"plugins.blocked entry {entry!r} matches no installed plugin")

    by_name = {plugin.name: plugin for plugin in all_plugins}
    mounted: dict[str, DatasetPlugin] = {
        plugin.name: plugin for plugin in all_plugins if plugin.name not in entries
    }

    def require(name: str, *, required_by: str) -> None:
        plugin = by_name.get(name)
        if plugin is None:
            return
        if name not in mounted:
            note(
                f"plugins.blocked entry {name!r} is ineffective: it is required by {required_by!r}"
            )
            mounted[name] = plugin
        for dependency in plugin.dependencies:
            require(dependency, required_by=name)

    for name in list(mounted):
        require(name, required_by=name)

    provider_ids = {plugin.provider for plugin in mounted.values()}
    active_providers: list[ProviderPlugin] = []
    for provider in all_providers:
        if provider.provider_id in provider_ids:
            if provider.provider_id in entries:
                note(
                    f"plugins.blocked entry {provider.provider_id!r} is ineffective: "
                    "mounted datasets require it"
                )
            active_providers.append(provider)
    return list(mounted.values()), active_providers


def register_plugins(
    workspace: Workspace,
    plugins: Iterable[DatasetPlugin],
    *,
    providers: Iterable[ProviderPlugin],
) -> None:
    values = _resolve_dependencies(
        list(plugins), {provider.provider_id for provider in providers}
    )
    validate_plugins(values, providers=providers)
    for plugin in values:
        workspace.register_dataset(
            plugin.name,
            spec=plugin.spec,
        )


@dataclass(frozen=True, slots=True)
class PluginWorkerDispatcher:
    """Pickle-safe worker resolving each task's dataset plugin at dispatch time.

    Resolution happens inside the task child process, so a plugin installed after
    server start is picked up by the next dispatch without a restart.
    """

    workspace: Path
    mode: str
    today: str
    now: str | None = None

    def __call__(self, request: OperationRequest, context: OperationReporter) -> Mapping[str, Any]:
        dataset = str(request["dataset"])
        providers = discover_provider_plugins()
        discovered = discover_dataset_plugins(providers=providers)
        discovered, providers = apply_plugin_blocklist(
            discovered,
            providers,
            plugin_blocklist(Workspace(self.workspace)),
            warn=logger.warning,
        )
        plugins = {plugin.name: plugin for plugin in discovered}
        try:
            runtime = plugins[dataset].runtime
        except KeyError as exc:
            raise ValueError(f"unknown dataset {dataset!r}") from exc
        worker = runtime.operation_worker(
            self.workspace,
            mode=self.mode,
            today=date.fromisoformat(self.today),
            now=datetime.fromisoformat(self.now) if self.now is not None else None,
        )
        return worker(request, context)
