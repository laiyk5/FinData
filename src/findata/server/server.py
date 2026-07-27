from __future__ import annotations

import fcntl
import hmac
import json
import logging
import os
import secrets
import threading
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, date, datetime, time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from findata.server.cron import CronManager
from findata.server.events import EventStore
from findata.identifiers import AmbiguousIdentifierError, IdentifierNotFoundError
from findata.sdk.loader import DataLoader, DatasetNotReadyError, UnsupportedCoverageError
from findata.server.presentation import default_display_timezone
from findata.storage import DATABASE_NAME, Workspace
from findata.sdk.plugins import (
    DatasetPlugin,
    PluginWorkerDispatcher,
    ProviderPlugin,
    apply_plugin_blocklist,
    discover_dataset_plugins_safe,
    discover_provider_plugins_safe,
    plugin_blocklist,
    plugin_load_errors,
    register_plugins,
)
from findata.server.taskrunner import DatasetBusyError, QueueFullError, TaskNotFoundError, TaskRunner

logger = logging.getLogger(__name__)


class ServerAlreadyRunningError(RuntimeError):
    pass


WEBUI_ROOT = Path(__file__).resolve().parent / "webui"

_WEBUI_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def initialize_workspace(root: Path) -> Workspace:
    workspace = Workspace.init(root)
    providers = discover_provider_plugins_safe()
    plugins = discover_dataset_plugins_safe(providers=providers)
    _log_plugin_load_errors()
    plugins, providers = apply_plugin_blocklist(
        plugins,
        providers,
        plugin_blocklist(workspace),
        warn=logger.warning,
    )
    register_plugins(
        workspace,
        plugins,
        providers=providers,
    )
    token_path = Path(root) / "token"
    if not token_path.exists():
        descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(secrets.token_urlsafe(48) + "\n")
            file.flush()
            os.fsync(file.fileno())
    return workspace


def _log_plugin_load_errors() -> None:
    for group, errors in plugin_load_errors().items():
        for error in errors:
            logger.warning(
                "Plugin load error [%s] %s: %s — %s",
                error.entry_point_group,
                error.entry_point_name,
                error.error_type,
                error.error_message,
            )


class FindataServer:
    def __init__(
        self,
        workspace: Path,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        provider_mode: str = "real",
        today: date | None = None,
        global_concurrency: int = 2,
    ) -> None:
        self.root = Path(workspace)
        self.workspace = initialize_workspace(self.root)
        self.started_at = datetime.now(UTC).timestamp()
        discovered_providers = discover_provider_plugins_safe()
        discovered_datasets = discover_dataset_plugins_safe(providers=discovered_providers)
        _log_plugin_load_errors()
        discovered_datasets, discovered_providers = apply_plugin_blocklist(
            discovered_datasets,
            discovered_providers,
            plugin_blocklist(self.workspace),
            warn=logger.warning,
        )
        self.providers = {item.provider_id: item for item in discovered_providers}
        self.plugins = {item.name: item for item in discovered_datasets}
        self._secret_keys = secret_config_keys(self.providers.values())
        self.host = host
        self.port = port
        self.provider_mode = provider_mode
        self.today = today or date.today()
        operation_now = (
            datetime.combine(today, time(18), ZoneInfo("Asia/Shanghai"))
            if today is not None
            else datetime.now(ZoneInfo("Asia/Shanghai"))
        )
        self.token = (self.root / "token").read_text(encoding="utf-8").strip()
        self._lock_file: Any = None
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._cron_thread: threading.Thread | None = None
        self._cron_stop = threading.Event()
        self.events = EventStore(self.root)
        self.taskrunner = TaskRunner(
            self.root,
            PluginWorkerDispatcher(
                self.root,
                mode=provider_mode,
                today=self.today.isoformat(),
                now=operation_now.isoformat(),
            ),
            global_concurrency=global_concurrency,
            event_sink=self.events.record,
            dependency_resolver=self._resolve_dependency,
            execution_context=self._execution_context,
        )
        self.cron = CronManager(
            self.workspace,
            self.events,
            submit=lambda dataset, operation, operands: self.taskrunner.submit(
                dataset, operation, operands, owner="cron"
            ),
            provider_ready=lambda dataset: self._provider_ready(self.plugins[dataset].provider),
            update_ready=self._update_ready,
            suggested={
                name: plugin.schedule
                for name, plugin in self.plugins.items()
                if plugin.schedule is not None
            },
        )

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("server is not running")
        return f"http://{self.host}:{self._httpd.server_port}"

    def start_background(self) -> None:
        if self._httpd is not None:
            return
        self._acquire_lock()
        try:
            self.workspace.recover_storage()
            self.taskrunner.start()
            self.cron.recover()
            handler = _handler_for(self)
            self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
            self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._thread.start()
            self._cron_stop.clear()
            self._cron_thread = threading.Thread(target=self._cron_loop, daemon=True)
            self._cron_thread.start()
            _write_json(
                self.root / "server.json",
                {"host": self.host, "port": self._httpd.server_port, "pid": os.getpid()},
                mode=0o600,
            )
        except BaseException:
            self.taskrunner.shutdown()
            self._release_lock()
            raise

    def serve_forever(self) -> None:
        self.start_background()
        assert self._thread is not None
        try:
            self._thread.join()
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self) -> None:
        self._cron_stop.set()
        if self._cron_thread is not None:
            self._cron_thread.join(timeout=2)
            self._cron_thread = None
        self.cron.note_shutdown()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.taskrunner.shutdown()
        (self.root / "server.json").unlink(missing_ok=True)
        self._release_lock()

    def _cron_loop(self) -> None:
        while not self._cron_stop.wait(1.0):
            try:
                self.cron.tick()
            except Exception as exc:
                self.events.record("cron_loop_error", "error", f"cron scheduler tick failed: {exc}")

    def _provider_ready(self, provider_id: str) -> bool:
        runtime = self.providers[provider_id].runtime
        assert runtime is not None
        return bool(runtime.ready(self.workspace, self.provider_mode))

    def _provider_is_mock(self, provider_id: str) -> bool:
        runtime = self.providers[provider_id].runtime
        assert runtime is not None
        return bool(runtime.is_mock(self.workspace, self.provider_mode))

    def provider_summaries(self) -> list[dict[str, object]]:
        """Credential-free readiness for every registered provider."""
        return [
            {
                "name": provider_id,
                "ready": self._provider_ready(provider_id),
                "mode": "mock" if self._provider_is_mock(provider_id) else "real",
            }
            for provider_id in self.providers
        ]

    def _dataset_status(self, dataset: str) -> dict[str, object]:
        """Committed maintenance state for one dataset, distinct from its static contract."""
        try:
            plugin = self.plugins[dataset]
        except KeyError as exc:
            raise ValueError(f"unknown dataset {dataset!r}") from exc
        status: dict[str, object] = {
            "name": dataset,
            "provider": plugin.provider,
            "provider_ready": self._provider_ready(plugin.provider),
            "update_ready": self._update_ready(dataset),
            "state": "uninitialized",
            "publication_id": None,
            "covered_keys": None,
            "coverage_start": None,
            "coverage_end": None,
            "storage_bytes": _dataset_storage_bytes(self.workspace, dataset),
        }
        reader = DataLoader(self.workspace.root).dataset(dataset)
        try:
            publication_id = reader.publication_id
        except DatasetNotReadyError:
            return status
        status["state"] = "ready"
        status["publication_id"] = publication_id
        try:
            coverage = reader.coverage()
        except UnsupportedCoverageError:
            coverage = None
        if coverage is not None and coverage.num_rows:
            status["covered_keys"] = len(set(coverage.column("key").to_pylist()))
            status["coverage_start"] = str(min(coverage.column("start").to_pylist()))
            status["coverage_end"] = str(max(coverage.column("end").to_pylist()))
        elif coverage is not None:
            status["covered_keys"] = 0
        return status

    def _update_ready(self, dataset: str) -> bool:
        return bool(self._runtime_for_dataset(dataset).update_ready(self.workspace))

    def _execution_context(self, dataset: str) -> dict[str, Any]:
        snapshot = self.workspace.config_snapshot()
        prefix = f"dataset.{dataset}."
        return {
            "configuration_revision": snapshot["revision"],
            "settings": {
                key: value for key, value in snapshot["values"].items() if key.startswith(prefix)
            },
        }

    def _runtime_for_dataset(self, dataset: str) -> Any:
        try:
            return self.plugins[dataset].runtime
        except KeyError as exc:
            raise ValueError(f"unknown dataset {dataset!r}") from exc

    def _match_dataset(self, parts: list[str]) -> tuple[str, list[str]] | None:
        """Greedy-match a registered dataset name against path parts (longest first)."""
        for name in sorted(self.plugins, key=lambda item: item.count("/"), reverse=True):
            name_parts = name.split("/")
            if parts[: len(name_parts)] == name_parts:
                return name, parts[len(name_parts) :]
        return None

    def _match_provider(self, parts: list[str]) -> tuple[str, list[str]] | None:
        """Greedy-match a registered provider name against path parts (longest first)."""
        for name in sorted(self.providers, key=lambda item: item.count("/"), reverse=True):
            name_parts = name.split("/")
            if parts[: len(name_parts)] == name_parts:
                return name, parts[len(name_parts) :]
        return None

    def _resolve_dependency(
        self, parent: str, target: str, requirement: dict[str, object]
    ) -> tuple[str, dict[str, object]]:
        return self._runtime_for_dataset(parent).resolve_dependency(target, requirement)

    def _acquire_lock(self) -> None:
        lock_path = self.root / "server.lock"
        self._lock_file = lock_path.open("a+b")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_file.close()
            self._lock_file = None
            raise ServerAlreadyRunningError(f"workspace {self.root} already has a server") from exc

    def _release_lock(self) -> None:
        if self._lock_file is not None:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None


def _handler_for(app: FindataServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "findata/0.1"

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._dispatch("PUT")

        def do_DELETE(self) -> None:  # noqa: N802
            self._dispatch("DELETE")

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/v1"):
                # Static WebUI assets carry no secrets and are served without the
                # token; every /v1 request below still requires it.
                if method == "GET":
                    self._serve_webui(parsed.path)
                else:
                    self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            if not self._authenticated():
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            parts = [unquote(part) for part in parsed.path.split("/") if part]
            query = parse_qs(parsed.query)
            try:
                if method == "GET" and parts == ["v1", "system", "health"]:
                    self._send(
                        HTTPStatus.OK,
                        _system_health(app),
                    )
                elif method == "GET" and parts == ["v1", "system", "status"]:
                    runtime = app.taskrunner.runtime_status()
                    self._send(
                        HTTPStatus.OK,
                        {
                            "status": "running",
                            "pid": os.getpid(),
                            "workspace": str(app.root),
                            "started_at": app.started_at,
                            "version": _server_version(),
                            "workspace_disk": _workspace_disk_usage(app.root),
                            "tasks": len(app.taskrunner.list_handles()),
                            **runtime,
                        },
                    )
                    return
                if method == "POST" and parts == ["v1", "tasks"]:
                    body = self._body()
                    dataset = str(body["dataset"])
                    try:
                        provider_id = app.plugins[dataset].provider
                    except KeyError as exc:
                        raise ValueError(f"unknown dataset {dataset!r}") from exc
                    if not app._provider_ready(provider_id):
                        raise ValueError(f"provider {provider_id} is not ready")
                    operation = str(body.get("operation") or "update")
                    operands = app._runtime_for_dataset(dataset).normalize_operation(
                        operation,
                        dict(body.get("operands") or {}),
                        today=app.today,
                    )
                    handle = app.taskrunner.submit(
                        dataset,
                        operation,
                        operands,
                        owner=str(body.get("owner") or "api"),
                    )
                    self._send(
                        HTTPStatus.ACCEPTED,
                        {
                            "handle_id": handle,
                            "execution_id": app.taskrunner.status(handle).execution_id,
                        },
                    )
                    return
                if method == "POST" and parts[:2] == ["v1", "datasets"] and len(parts) > 2:
                    plan_match = app._match_dataset(parts[2:])
                    if (
                        plan_match is not None
                        and len(plan_match[1]) == 3
                        and plan_match[1][0] == "operations"
                        and plan_match[1][2] == "plan"
                    ):
                        dataset, operation = plan_match[0], plan_match[1][1]
                        body = self._body()
                        runtime = app._runtime_for_dataset(dataset)
                        operands = runtime.normalize_operation(
                            operation,
                            dict(body.get("operands") or {}),
                            today=app.today,
                        )
                        self._send(
                            HTTPStatus.OK,
                            runtime.plan_operation(
                                app.workspace,
                                operation,
                                operands,
                                today=app.today,
                            ),
                        )
                        return
                if method == "GET" and parts == ["v1", "tasks"]:
                    items = app.taskrunner.list_handles(
                        dataset=_query_one(query, "dataset"),
                        status=_query_one(query, "status"),
                    )
                    if _query_one(query, "all") != "true":
                        active = [
                            item
                            for item in items
                            if item.status not in {"succeeded", "failed", "canceled"}
                        ]
                        terminal = [
                            item
                            for item in items
                            if item.status in {"succeeded", "failed", "canceled"}
                        ][:50]
                        items = active + terminal
                    self._send(
                        HTTPStatus.OK,
                        {"items": [_task_payload(item) for item in items]},
                    )
                    return
                if len(parts) >= 3 and parts[:2] == ["v1", "tasks"]:
                    handle_id = parts[2]
                    if method == "GET" and len(parts) == 3:
                        record, subscriber_count = app.taskrunner.status_with_subscriber_count(
                            handle_id
                        )
                        value = _task_payload(record)
                        value["subscriber_count"] = subscriber_count
                        self._send(HTTPStatus.OK, value)
                        return
                    if method == "GET" and parts[3:] == ["logs"]:
                        resolved = app.taskrunner.status(handle_id).handle_id
                        self._send(
                            HTTPStatus.OK,
                            {"handle_id": resolved, "items": app.taskrunner.logs(resolved)},
                        )
                        return
                    if method == "POST" and parts[3:] == ["cancel"]:
                        current = app.taskrunner.status(handle_id)
                        if current.status in {"succeeded", "failed", "canceled"}:
                            payload = _task_payload(current)
                            payload["already_terminal"] = True
                            self._send(HTTPStatus.OK, payload)
                        else:
                            result = app.taskrunner.cancel(current.handle_id)
                            self._send(HTTPStatus.OK, asdict(result))
                        return
                    if method == "POST" and parts[3:] == ["retry"]:
                        retained = app.taskrunner.retained_request(handle_id)
                        handle = app.taskrunner.submit(
                            str(retained["dataset"]),
                            str(retained["operation"]),
                            dict(retained["operands"]),
                            owner="retry",
                        )
                        self._send(
                            HTTPStatus.ACCEPTED,
                            {
                                "handle_id": handle,
                                "execution_id": app.taskrunner.status(handle).execution_id,
                                "retried_from": retained["handle_id"],
                            },
                        )
                        return
                    if method == "GET" and parts[3:] == ["explain"]:
                        record, subscribers = app.taskrunner.status_with_subscriber_count(handle_id)
                        logs = app.taskrunner.logs(record.handle_id)
                        diagnostics = [
                            item
                            for item in logs
                            if isinstance(item, dict) and item.get("type") == "task.diagnostic"
                        ]
                        self._send(
                            HTTPStatus.OK,
                            {
                                "handle_id": record.handle_id,
                                "dataset": record.dataset,
                                "operation": record.operation,
                                "status": record.status,
                                "reason": record.reason or record.error,
                                "diagnostics": diagnostics,
                                "subscriber_count": subscribers,
                                "inspection": {
                                    "status": f"findata task status {record.handle_id}",
                                    "logs": f"findata task logs {record.handle_id}",
                                    "retry": f"findata task retry {record.handle_id}",
                                },
                            },
                        )
                        return
                if method == "POST" and parts == ["v1", "config"]:
                    body = self._body()
                    key = str(body["key"])
                    value = body["value"]
                    if _reserved_config_key(key):
                        raise ValueError(f"configuration key {key!r} is reserved")
                    if key == "display.timezone":
                        try:
                            ZoneInfo(str(value))
                        except ZoneInfoNotFoundError as exc:
                            raise ValueError(f"unknown IANA timezone {value!r}") from exc
                    if key.startswith("dataset."):
                        plugin = _dataset_plugin_for_key(app, key)
                        value = plugin.normalize_setting(key, value, workspace=app.workspace)
                    app.workspace.set_config(key, value)
                    self._send(
                        HTTPStatus.OK,
                        {
                            "updated": True,
                            "key": key,
                            "value": _redact(key, value, app._secret_keys),
                            "revision": app.workspace.config_snapshot()["revision"],
                        },
                    )
                    return
                if method == "GET" and parts == ["v1", "config"]:
                    key = _query_one(query, "key")
                    values = {
                        name: value
                        for name, value in app.workspace.list_config().items()
                        if not _reserved_config_key(name)
                    }
                    if key is not None:
                        if key not in values:
                            self._send(
                                HTTPStatus.NOT_FOUND,
                                {"error": f"configuration key {key!r} is not set"},
                            )
                        else:
                            self._send(
                                HTTPStatus.OK,
                                {
                                    "key": key,
                                    "value": _redact(key, values[key], app._secret_keys),
                                },
                            )
                    else:
                        self._send(
                            HTTPStatus.OK,
                            {
                                "values": {
                                    name: _redact(name, value, app._secret_keys)
                                    for name, value in values.items()
                                }
                            },
                        )
                    return
                if method == "DELETE" and len(parts) >= 3 and parts[:2] == ["v1", "config"]:
                    key = "/".join(parts[2:])
                    if _reserved_config_key(key):
                        raise ValueError(f"configuration key {key!r} is reserved")
                    if key.startswith("dataset."):
                        _dataset_plugin_for_key(app, key)
                    self._send(HTTPStatus.OK, {"removed": app.workspace.unset_config(key)})
                    return
                if method == "GET" and parts == ["v1", "config", "keys"]:
                    self._send(HTTPStatus.OK, {"items": _declared_config_keys(app)})
                    return
                if method == "GET" and parts == ["v1", "providers"]:
                    self._send(
                        HTTPStatus.OK,
                        {
                            "items": [
                                {
                                    "name": provider_id,
                                    "ready": bool(
                                        provider.runtime.ready(app.workspace, app.provider_mode)
                                    ),
                                    "mode": (
                                        "mock"
                                        if provider.runtime.is_mock(
                                            app.workspace, app.provider_mode
                                        )
                                        else "real"
                                    ),
                                    "secret_fields": list(provider.secret_fields),
                                }
                                for provider_id, provider in app.providers.items()
                            ]
                        },
                    )
                    return
                if method == "GET" and parts[:2] == ["v1", "providers"] and len(parts) > 2:
                    provider_match = app._match_provider(parts[2:])
                    if provider_match is None:
                        raise ValueError(f"unknown provider {'/'.join(parts[2:])!r}")
                    provider_id, provider_tail = provider_match
                    provider = app.providers[provider_id]
                    runtime = provider.runtime
                    assert runtime is not None
                    if provider_tail == ["check"]:
                        ready = bool(runtime.ready(app.workspace, app.provider_mode))
                        mock = bool(runtime.is_mock(app.workspace, app.provider_mode))
                        if ready and not mock:
                            runtime.probe(app.workspace, today=app.today)
                        self._send(
                            HTTPStatus.OK,
                            {
                                "provider": provider_id,
                                "ready": ready,
                                "authenticated": (ready and not mock) if not mock else None,
                                "mode": "mock" if mock else "real",
                            },
                        )
                        return
                    if not provider_tail:
                        configured = app.provider_mode == "mock" or any(
                            app.workspace.get_config(f"provider.{provider_id}.{field}") is not None
                            for field in provider.secret_fields
                        )
                        self._send(
                            HTTPStatus.OK,
                            {
                                "name": provider_id,
                                "ready": bool(runtime.ready(app.workspace, app.provider_mode)),
                                "configured": configured,
                                "mode": (
                                    "mock"
                                    if runtime.is_mock(app.workspace, app.provider_mode)
                                    else "real"
                                ),
                            },
                        )
                        return
                if method == "GET" and parts == ["v1", "datasets"]:
                    self._send(
                        HTTPStatus.OK,
                        {
                            "items": [
                                app._runtime_for_dataset(name).dataset_description(
                                    app.workspace,
                                    provider_ready=app._provider_ready(app.plugins[name].provider),
                                )
                                for name in app.plugins
                            ]
                        },
                    )
                    return
                dataset_route = (
                    parts[2:] if parts[:2] == ["v1", "datasets"] and len(parts) > 2 else None
                )
                dataset_match = (
                    app._match_dataset(dataset_route) if dataset_route is not None else None
                )
                if method == "POST" and dataset_match is not None and dataset_match[1] == ["reset"]:
                    body = self._body()
                    if body.get("confirm") is not True:
                        raise ValueError("dataset reset requires confirm=true")
                    dataset = dataset_match[0]
                    plugin = app.plugins[dataset]
                    with app.taskrunner.reserve_dataset_reset(dataset):
                        app.workspace.reset_dataset(dataset, spec=plugin.spec)
                    self._send(
                        HTTPStatus.OK,
                        {"dataset": dataset, "state": "uninitialized", "reset": True},
                    )
                    return
                if method == "GET" and parts == ["v1", "datasets", "status"]:
                    self._send(
                        HTTPStatus.OK,
                        {"items": [app._dataset_status(name) for name in app.plugins]},
                    )
                    return
                if method == "GET" and dataset_match is not None:
                    dataset, tail = dataset_match
                    if not tail:
                        self._send(
                            HTTPStatus.OK,
                            app._runtime_for_dataset(dataset).dataset_description(
                                app.workspace,
                                provider_ready=app._provider_ready(app.plugins[dataset].provider),
                            ),
                        )
                        return
                    if tail == ["status"]:
                        self._send(HTTPStatus.OK, app._dataset_status(dataset))
                        return
                    if tail == ["operations"]:
                        description = app._runtime_for_dataset(dataset).dataset_description(
                            app.workspace,
                            provider_ready=app._provider_ready(app.plugins[dataset].provider),
                        )
                        self._send(HTTPStatus.OK, {"items": description["operations"]})
                        return
                    if len(tail) == 2 and tail[0] == "operations":
                        self._send(
                            HTTPStatus.OK,
                            app._runtime_for_dataset(dataset).operation_description(tail[1]),
                        )
                        return
                if dataset_route is not None and dataset_match is None:
                    raise ValueError(f"unknown dataset {'/'.join(dataset_route)!r}")
                if method == "GET" and parts == ["v1", "cron"]:
                    self._send(
                        HTTPStatus.OK, {"items": [asdict(job) for job in app.cron.list_jobs()]}
                    )
                    return
                if len(parts) > 2 and parts[:2] == ["v1", "cron"]:
                    cron_match = app._match_dataset(parts[2:])
                    if cron_match is None or len(cron_match[1]) != 1:
                        self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                        return
                    dataset, (action,) = cron_match
                    if method == "POST" and action == "enable":
                        self._send(HTTPStatus.OK, asdict(app.cron.enable(dataset)))
                    elif method == "POST" and action == "disable":
                        self._send(HTTPStatus.OK, asdict(app.cron.disable(dataset)))
                    elif method == "PUT" and action == "schedule":
                        body = self._body()
                        self._send(
                            HTTPStatus.OK,
                            asdict(
                                app.cron.set_schedule(
                                    dataset, str(body["expression"]), str(body["timezone"])
                                )
                            ),
                        )
                    elif method == "POST" and action == "reset":
                        self._send(HTTPStatus.OK, asdict(app.cron.reset(dataset)))
                    else:
                        self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                if method == "GET" and parts == ["v1", "events"]:
                    since_text = _query_one(query, "since")
                    items = app.events.list_events(
                        unread=_query_one(query, "unread") == "true",
                        since=float(since_text) if since_text else None,
                        severity=_query_one(query, "severity"),
                    )
                    self._send(HTTPStatus.OK, {"items": [asdict(item) for item in items]})
                    return
                if method == "POST" and parts == ["v1", "events", "ack"]:
                    body = self._body()
                    if body.get("all"):
                        count = app.events.ack_all()
                        response = {"acknowledged": count}
                    else:
                        event_id, already = app.events.ack(str(body["event_id"]))
                        response = {
                            "acknowledged": 1,
                            "already_acknowledged": already,
                            "event_id": event_id,
                        }
                    self._send(HTTPStatus.OK, response)
                    return
                self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except QueueFullError as exc:
                self._send(HTTPStatus.CONFLICT, {"error": str(exc)})
            except DatasetBusyError as exc:
                self._send(HTTPStatus.CONFLICT, {"error": str(exc)})
            except AmbiguousIdentifierError as exc:
                self._send(HTTPStatus.CONFLICT, {"error": str(exc)})
            except TaskNotFoundError as exc:
                operand = exc.args[0] if exc.args else "unknown"
                self._send(
                    HTTPStatus.NOT_FOUND,
                    {"error": f"no retained task matches {operand!r}"},
                )
            except IdentifierNotFoundError as exc:
                operand = exc.args[0] if exc.args else "unknown"
                self._send(
                    HTTPStatus.NOT_FOUND,
                    {"error": f"no retained event matches {operand!r}"},
                )
            except (KeyError, TypeError, ValueError) as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

        def _authenticated(self) -> bool:
            expected = f"Bearer {app.token}"
            provided = self.headers.get("Authorization", "")
            return hmac.compare_digest(provided, expected)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise ValueError("request body too large")
            value = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(value, dict):
                raise ValueError("request body must be an object")
            return value

        def _serve_webui(self, raw_path: str) -> None:
            if not WEBUI_ROOT.is_dir():
                self._send(
                    HTTPStatus.NOT_FOUND,
                    {"error": "web UI is not built; run npm run build in web/"},
                )
                return
            root = WEBUI_ROOT.resolve()
            candidate = (root / raw_path.lstrip("/")).resolve()
            if not candidate.is_file() or root not in candidate.parents:
                # Unknown and unsafe paths fall back to the SPA entry point.
                candidate = root / "index.html"
            content_type = _WEBUI_CONTENT_TYPES.get(
                candidate.suffix.lower(), "application/octet-stream"
            )
            payload = candidate.read_bytes()
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            if candidate.name == "index.html":
                self.send_header("Cache-Control", "no-cache")
            elif raw_path.startswith("/assets/"):
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(payload)

        def _send(self, status: HTTPStatus, value: Any) -> None:
            payload = json.dumps(value, separators=(",", ":"), default=str).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def _task_payload(record: Any) -> dict[str, Any]:
    value = asdict(record)
    counts = value.get("diagnostic_counts")
    if isinstance(counts, dict) and not any(counts.values()):
        value.pop("diagnostic_counts", None)
    return value


def _system_health(app: FindataServer) -> dict[str, Any]:
    """Aggregate health summary for ``/v1/system/health``."""
    load_errors = plugin_load_errors()
    total_errors = sum(len(errs) for errs in load_errors.values())
    dataset_list = [
        {
            "name": name,
            "provider": plugin.provider,
            "state": app._dataset_status(name).get("state", "unknown"),
        }
        for name, plugin in sorted(app.plugins.items())
    ]
    return {
        "status": "running",
        "version": _server_version(),
        "workspace": str(app.root),
        "providers": app.provider_summaries(),
        "datasets": dataset_list,
        "plugin_errors": total_errors,
    }


def _server_version() -> str:
    try:
        return package_version("findata")
    except PackageNotFoundError:
        return "dev"


def _workspace_disk_usage(root: Path) -> dict[str, Any]:
    """On-disk size of the workspace itself, broken down by top-level entry."""
    sizes: dict[str, int] = {}
    total = 0
    for entry in sorted(root.iterdir()):
        size = _path_size(entry)
        if size:
            sizes[entry.name] = size
            total += size
    breakdown = [
        {"name": name, "bytes": size}
        for name, size in sorted(sizes.items(), key=lambda item: -item[1])
    ]
    return {"total_bytes": total, "breakdown": breakdown}


def _path_size(path: Path) -> int:
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    size = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for filename in filenames:
            candidate = Path(dirpath) / filename
            if candidate.is_symlink():
                continue
            try:
                size += candidate.stat().st_size
            except OSError:
                continue
    return size


def _dataset_storage_bytes(workspace: Workspace, dataset: str) -> int | None:
    """On-disk size of the dataset's database (main file plus WAL), if present."""
    dataset_root = workspace.datasets_root / dataset
    database = dataset_root / DATABASE_NAME
    if not database.exists():
        return None
    total = database.stat().st_size
    wal = dataset_root / f"{DATABASE_NAME}.wal"
    if wal.exists():
        total += wal.stat().st_size
    return total


def secret_config_keys(providers: Iterable[Any]) -> frozenset[str]:
    """Configuration keys declared secret by registered provider plugins."""
    return frozenset(
        f"provider.{provider.provider_id}.{field}"
        for provider in providers
        for field in provider.secret_fields
    )


RESERVED_CONFIG_PREFIXES = ("cron.",)


def _reserved_config_key(key: str) -> bool:
    """Internal state keys (for example cron.jobs) are not user configuration."""
    return any(key.startswith(prefix) for prefix in RESERVED_CONFIG_PREFIXES)


CORE_CONFIG_KEYS: dict[str, dict[str, Any]] = {
    "display.timezone": {
        "schema": {"type": "string", "format": "iana-timezone"},
        "help": "Display timezone for human CLI output (IANA name, for example Asia/Shanghai).",
    },
}


def _dataset_plugin_for_key(app: FindataServer, key: str) -> DatasetPlugin:
    """Resolve a dataset.<name>.* configuration key to its owning plugin."""
    if not key.startswith("dataset."):
        raise ValueError(f"unknown dataset setting {key!r}")
    remainder = key[len("dataset.") :]
    for name in sorted(app.plugins, key=len, reverse=True):
        if remainder.startswith(f"{name}."):
            plugin = app.plugins[name]
            if key not in plugin.settings:
                declared = ", ".join(sorted(plugin.settings)) or "none"
                raise ValueError(
                    f"unknown setting {key!r} for {plugin.name}; declared settings: {declared}"
                )
            return plugin
    raise ValueError(f"unknown dataset setting {key!r}")


def _provider_key_help(provider: ProviderPlugin, field: str) -> str:
    if field in provider.secret_fields:
        return (
            f"{field.replace('_', ' ').capitalize()} for the {provider.provider_id} provider "
            "(secret; set via --env or --stdin)."
        )
    if field == "rate_limit":
        return (
            f"Rate limit for the {provider.provider_id} provider "
            f"(requests per {provider.period} seconds)."
        )
    return f"{field.replace('_', ' ').capitalize()} for the {provider.provider_id} provider."


def _declared_config_keys(app: FindataServer) -> list[dict[str, Any]]:
    """Declared configuration keys: core, provider, and plugin dataset settings."""
    items: list[dict[str, Any]] = []

    def item(key: str, help_text: str, schema: Any, *, secret: bool) -> dict[str, Any]:
        return {
            "key": key,
            "help": help_text,
            "schema": dict(schema) if isinstance(schema, Mapping) else {},
            "configured": app.workspace.get_config(key) is not None,
            "secret": secret,
        }

    for key, declaration in CORE_CONFIG_KEYS.items():
        entry = item(key, str(declaration["help"]), declaration["schema"], secret=False)
        if key == "display.timezone":
            entry["default"] = default_display_timezone()
        items.append(entry)
    for provider_id, provider in sorted(app.providers.items()):
        properties = provider.configuration_schema.get("properties", {})
        if not isinstance(properties, Mapping):
            continue
        for field, schema in properties.items():
            key = f"provider.{provider_id}.{field}"
            entry = item(
                key,
                _provider_key_help(provider, str(field)),
                schema,
                secret=field in provider.secret_fields,
            )
            if field == "rate_limit":
                entry["default"] = provider.rate_limit
            items.append(entry)
    for name, plugin in sorted(app.plugins.items()):
        for key, setting in sorted(plugin.settings.items()):
            entry = item(key, setting.help, setting.schema, secret=False)
            entry["required"] = setting.required
            items.append(entry)
    return items


def _redact(key: str, value: Any, secret_keys: frozenset[str] = frozenset()) -> Any:
    if isinstance(value, dict) and set(value) == {"env"}:
        # An environment-variable reference names a variable, not a secret.
        return value
    if key in secret_keys:
        return "<redacted>"
    lowered = key.lower()
    if any(word in lowered for word in ("token", "secret", "password", "credential")):
        return "<redacted>"
    return value


def _query_one(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _write_json(path: Path, value: dict[str, Any], *, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(value, file, separators=(",", ":"))
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
