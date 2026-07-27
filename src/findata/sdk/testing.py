"""Test utilities for plugin authors.

Usage::

    from findata.sdk.testing import RecordingReporter, FakeDatasetRuntime, create_test_workspace
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from findata.sdk.contracts import DatasetSpec
from findata.sdk.plugins import (
    DatasetPlugin,
    DatasetRuntimeBase,
    OperationWorker,
    ProviderPlugin,
)
from findata.storage import Workspace

__all__ = [
    "FakeDatasetRuntime",
    "RecordingReporter",
    "TestingProviderRuntime",
    "create_test_workspace",
    "make_provider_plugin",
    "make_dataset_plugin",
]


class RecordingReporter:
    """An ``OperationReporter`` that records every call for test assertions.

    Example::

        reporter = RecordingReporter()
        reporter.log("hello")
        reporter.diagnostic("warning", "MY_CODE", "something happened")
        assert "hello" in reporter.logs
        assert reporter.diagnostics[0]["code"] == "MY_CODE"
    """

    def __init__(self) -> None:
        self._logs: list[str] = []
        self._diagnostics: list[dict[str, Any]] = []
        self._stages: list[str] = []
        self._progress: list[tuple[float, float]] = []

    @property
    def logs(self) -> list[str]:
        return list(self._logs)

    @property
    def diagnostics(self) -> list[dict[str, Any]]:
        return list(self._diagnostics)

    @property
    def stages(self) -> list[str]:
        return list(self._stages)

    def checkpoint(self) -> None:
        return

    def log(self, message: str) -> None:
        self._logs.append(message)

    def diagnostic(
        self,
        severity: str,
        code: str,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
        count: int = 1,
    ) -> None:
        self._diagnostics.append(
            {
                "severity": severity,
                "code": code,
                "message": message,
                "context": dict(context) if context else {},
                "count": count,
            }
        )

    def progress(self, current: int | float, total: int | float, **metrics: int | float) -> None:
        self._progress.append((float(current), float(total)))

    def stage(self, value: str) -> None:
        self._stages.append(value)

    def waiting(self, reason: str) -> None:
        pass

    def running(self) -> None:
        pass

    def begin_subtask(self, *, timeout: float) -> None:
        pass

    def end_subtask(self) -> None:
        pass

    def fulfill(self, dataset: str, requirement: Mapping[str, Any]) -> Any:
        raise RuntimeError(f"RecordingReporter cannot fulfill {dataset}")


class FakeDatasetRuntime(DatasetRuntimeBase):
    """A ``DatasetRuntimeBase`` with a configurable worker for testing.

    Example::

        runtime = FakeDatasetRuntime(spec=MY_SPEC)
        assert isinstance(runtime, DatasetRuntime)
    """

    def __init__(
        self,
        *,
        spec: DatasetSpec,
        worker: OperationWorker | None = None,
        operations: tuple[str, ...] | None = None,
    ) -> None:
        self.spec = spec
        self._worker = worker
        if operations is not None:
            self.operations = operations

    def operation_worker(
        self,
        workspace: Path,
        *,
        mode: str,
        today: date,
        now: Any,
    ) -> Any:
        if self._worker is not None:
            return self._worker
        raise NotImplementedError(
            "FakeDatasetRuntime has no worker configured; "
            "pass worker= to the constructor or subclass"
        )


class TestingProviderRuntime:
    """A ``ProviderRuntime`` for tests that is always ready."""

    def ready(self, workspace: Workspace, mode: str) -> bool:
        return True

    def is_mock(self, workspace: Workspace, mode: str) -> bool:
        return True

    def probe(self, workspace: Workspace, *, today: date) -> None:
        pass


def make_provider_plugin(
    provider_id: str = "testing/provider",
    **overrides: Any,
) -> ProviderPlugin:
    """Build a minimal ``ProviderPlugin`` for tests."""
    kwargs: dict[str, Any] = dict(
        provider_id=provider_id,
        configuration_schema={"type": "object", "properties": {}},
        secret_fields=(),
        rate_limit=1000,
        period=60,
        runtime=TestingProviderRuntime(),
    )
    kwargs.update(overrides)
    return ProviderPlugin(**kwargs)  # type: ignore[arg-type]


def make_dataset_plugin(
    spec: DatasetSpec,
    provider: str = "testing/provider",
    **overrides: Any,
) -> DatasetPlugin:
    """Build a minimal ``DatasetPlugin`` for tests."""
    kwargs: dict[str, Any] = dict(
        name=spec.name,
        provider=provider,
        spec=spec,
        runtime=FakeDatasetRuntime(spec=spec),
        operations=("update",),
    )
    kwargs.update(overrides)
    return DatasetPlugin(**kwargs)  # type: ignore[arg-type]


@contextmanager
def create_test_workspace(
    *,
    plugins: Iterable[DatasetPlugin] | None = None,
    providers: Iterable[ProviderPlugin] | None = None,
    path: Path | None = None,
) -> Workspace:
    """Context manager that creates a temporary workspace with registered plugins.

    Example::

        with create_test_workspace(plugins=[my_plugin]) as ws:
            assert ws.get_config("plugins.blocked") is None
    """
    import atexit
    import shutil
    import tempfile

    root = path or Path(tempfile.mkdtemp(prefix="findata-test-"))
    atexit.register(lambda: shutil.rmtree(root, ignore_errors=True))

    ws = Workspace.init(root)
    resolved_providers = list(providers) if providers else [make_provider_plugin()]
    resolved_plugins = list(plugins) if plugins else []

    if resolved_plugins:
        from findata.sdk.plugins import register_plugins

        register_plugins(ws, resolved_plugins, providers=resolved_providers)

    try:
        yield ws
    finally:
        if path is None:
            shutil.rmtree(root, ignore_errors=True)
