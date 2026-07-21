from __future__ import annotations

import fcntl
import hmac
import json
import os
import secrets
import threading
from dataclasses import asdict
from datetime import date, datetime, time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from findata.cron import CronManager
from findata.events import EventStore
from findata.identifiers import AmbiguousIdentifierError, IdentifierNotFoundError
from findata.storage import Workspace
from findata.plugins import discover_dataset_plugins, discover_provider_plugins, register_plugins
from findata.taskrunner import QueueFullError, TaskNotFoundError, TaskRunner


class ServerAlreadyRunningError(RuntimeError):
    pass


def initialize_workspace(root: Path) -> Workspace:
    workspace = Workspace.init(root)
    providers = discover_provider_plugins()
    register_plugins(
        workspace,
        discover_dataset_plugins(providers=providers),
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
        self.providers = {item.provider_id: item for item in discover_provider_plugins()}
        self.plugins = {
            item.name: item
            for item in discover_dataset_plugins(providers=self.providers.values())
        }
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
        runtime = self.providers["tushare"].runtime
        assert runtime is not None
        self.taskrunner = TaskRunner(
            self.root,
            runtime.operation_worker(
                self.root,
                mode=provider_mode,
                today=self.today,
                now=operation_now,
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
            provider_ready=lambda _dataset: self._provider_ready(),
            update_ready=self._update_ready,
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
                self.events.record(
                    "cron_loop_error", "error", f"cron scheduler tick failed: {exc}"
                )

    def _provider_ready(self) -> bool:
        runtime = self.providers["tushare"].runtime
        assert runtime is not None
        return bool(runtime.ready(self.workspace, self.provider_mode))

    def _provider_is_mock(self) -> bool:
        runtime = self.providers["tushare"].runtime
        assert runtime is not None
        return bool(runtime.is_mock(self.workspace, self.provider_mode))

    def _update_ready(self, dataset: str) -> bool:
        if dataset == "tushare_index_weight":
            return bool(
                self.workspace.get_config("dataset.tushare_index_weight.update_indexes")
            )
        if dataset == "tushare_daily_basic":
            return bool(
                self.workspace.get_config("dataset.tushare_daily_basic.update_symbols")
            )
        if dataset == "tushare_index_basic":
            manifest = self.workspace.datasets_root / dataset / "manifest.json"
            return manifest.exists() and json.loads(manifest.read_text())["state"] == "ready"
        return True

    def _execution_context(self, dataset: str) -> dict[str, Any]:
        snapshot = self.workspace.config_snapshot()
        prefix = f"dataset.{dataset}."
        return {
            "configuration_revision": snapshot["revision"],
            "settings": {
                key: value
                for key, value in snapshot["values"].items()
                if key.startswith(prefix)
            },
        }

    def _runtime_for_dataset(self, dataset: str) -> Any:
        try:
            provider_id = self.plugins[dataset].provider
            runtime = self.providers[provider_id].runtime
        except KeyError as exc:
            raise ValueError(f"unknown dataset {dataset!r}") from exc
        assert runtime is not None
        return runtime

    def _resolve_dependency(
        self, parent: str, target: str, requirement: dict[str, object]
    ) -> tuple[str, dict[str, object]]:
        return self._runtime_for_dataset(parent).resolve_dependency(
            parent, target, requirement
        )

    def _probe_tushare(self) -> None:
        runtime = self.providers["tushare"].runtime
        assert runtime is not None
        runtime.probe(self.workspace, today=self.today)

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
            if not self._authenticated():
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            query = parse_qs(parsed.query)
            try:
                if method == "GET" and parts == ["v1", "system", "status"]:
                    runtime = app.taskrunner.runtime_status()
                    self._send(
                        HTTPStatus.OK,
                        {
                            "status": "running",
                            "pid": os.getpid(),
                            "tasks": len(app.taskrunner.list_handles()),
                            **runtime,
                        },
                    )
                    return
                if method == "POST" and parts == ["v1", "tasks"]:
                    body = self._body()
                    if not app._provider_ready():
                        raise ValueError("provider tushare is not ready")
                    dataset = str(body["dataset"])
                    operation = str(body.get("operation") or "update")
                    operands = app._runtime_for_dataset(dataset).normalize_operation(
                        dataset,
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
                if method == "GET" and parts == ["v1", "tasks"]:
                    items = app.taskrunner.list_handles(
                        dataset=_query_one(query, "dataset"),
                        status=_query_one(query, "status"),
                    )
                    if _query_one(query, "all") != "true":
                        active = [item for item in items if item.status not in {"succeeded", "failed", "canceled"}]
                        terminal = [item for item in items if item.status in {"succeeded", "failed", "canceled"}][:50]
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
                            self._send(HTTPStatus.OK, _task_payload(current))
                        else:
                            result = app.taskrunner.cancel(current.handle_id)
                            self._send(HTTPStatus.OK, asdict(result))
                        return
                if method == "POST" and parts == ["v1", "config"]:
                    body = self._body()
                    key = str(body["key"])
                    value = body["value"]
                    if key == "display.timezone":
                        ZoneInfo(str(value))
                    if key.startswith("dataset."):
                        components = key.split(".", 2)
                        if len(components) != 3 or components[1] not in app.plugins:
                            raise ValueError(f"unknown dataset setting {key!r}")
                        value = app.plugins[components[1]].normalize_setting(
                            key, value, workspace=app.workspace
                        )
                    app.workspace.set_config(key, value)
                    self._send(
                        HTTPStatus.OK,
                        {
                            "updated": True,
                            "key": key,
                            "value": _redact(key, value),
                            "revision": app.workspace.config_snapshot()["revision"],
                        },
                    )
                    return
                if method == "GET" and parts == ["v1", "config"]:
                    key = _query_one(query, "key")
                    values = app.workspace.list_config()
                    if key is not None:
                        if key not in values:
                            self._send(HTTPStatus.NOT_FOUND, {"error": "config_not_found"})
                        else:
                            self._send(HTTPStatus.OK, {"key": key, "value": _redact(key, values[key])})
                    else:
                        self._send(
                            HTTPStatus.OK,
                            {"values": {name: _redact(name, value) for name, value in values.items()}},
                        )
                    return
                if method == "DELETE" and len(parts) == 3 and parts[:2] == ["v1", "config"]:
                    key = parts[2]
                    if key.startswith("dataset."):
                        components = key.split(".", 2)
                        if (
                            len(components) != 3
                            or components[1] not in app.plugins
                            or key not in app.plugins[components[1]].settings
                        ):
                            raise ValueError(f"unknown dataset setting {key!r}")
                    self._send(HTTPStatus.OK, {"removed": app.workspace.unset_config(parts[2])})
                    return
                if method == "GET" and len(parts) == 4 and parts[:2] == ["v1", "providers"] and parts[3] == "check":
                    provider_id = parts[2]
                    try:
                        provider = app.providers[provider_id]
                    except KeyError as exc:
                        raise ValueError(f"unknown provider {provider_id!r}") from exc
                    runtime = provider.runtime
                    assert runtime is not None
                    ready = bool(runtime.ready(app.workspace, app.provider_mode))
                    mock = bool(runtime.is_mock(app.workspace, app.provider_mode))
                    if ready and not mock:
                        runtime.probe(app.workspace, today=app.today)
                    self._send(
                        HTTPStatus.OK,
                        {
                            "provider": provider_id,
                            "ready": ready,
                            "authenticated": ready and not mock,
                            "mode": "mock" if mock else "real",
                        },
                    )
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
                                }
                                for provider_id, provider in app.providers.items()
                            ]
                        },
                    )
                    return
                if method == "GET" and len(parts) == 3 and parts[:2] == ["v1", "providers"]:
                    provider_id = parts[2]
                    try:
                        provider = app.providers[provider_id]
                    except KeyError as exc:
                        raise ValueError(f"unknown provider {provider_id!r}") from exc
                    runtime = provider.runtime
                    assert runtime is not None
                    configured = app.workspace.get_config(f"provider.{provider_id}.token")
                    self._send(
                        HTTPStatus.OK,
                        {
                            "name": provider_id,
                            "ready": bool(runtime.ready(app.workspace, app.provider_mode)),
                            "configured": configured is not None or app.provider_mode == "mock",
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
                                    app.workspace, name, provider_ready=app._provider_ready()
                                )
                                for name in app.plugins
                            ]
                        },
                    )
                    return
                if method == "GET" and len(parts) == 3 and parts[:2] == ["v1", "datasets"]:
                    self._send(
                        HTTPStatus.OK,
                        app._runtime_for_dataset(parts[2]).dataset_description(
                            app.workspace, parts[2], provider_ready=app._provider_ready()
                        ),
                    )
                    return
                if method == "GET" and len(parts) == 4 and parts[:2] == ["v1", "datasets"]:
                    dataset, action = parts[2], parts[3]
                    if action in {"operations", "status"}:
                        description = app._runtime_for_dataset(dataset).dataset_description(
                            app.workspace, dataset, provider_ready=app._provider_ready()
                        )
                        self._send(
                            HTTPStatus.OK,
                            description if action == "status" else {"items": description["operations"]},
                        )
                        return
                if (
                    method == "GET"
                    and len(parts) == 5
                    and parts[:2] == ["v1", "datasets"]
                    and parts[3] == "operations"
                ):
                    self._send(
                        HTTPStatus.OK,
                        app._runtime_for_dataset(parts[2]).operation_description(
                            parts[2], parts[4]
                        ),
                    )
                    return
                if method == "GET" and parts == ["v1", "cron"]:
                    self._send(HTTPStatus.OK, {"items": [asdict(job) for job in app.cron.list_jobs()]})
                    return
                if len(parts) >= 4 and parts[:2] == ["v1", "cron"]:
                    dataset, action = parts[2], parts[3]
                    if method == "POST" and action == "enable":
                        self._send(HTTPStatus.OK, asdict(app.cron.enable(dataset)))
                    elif method == "POST" and action == "disable":
                        self._send(HTTPStatus.OK, asdict(app.cron.disable(dataset)))
                    elif method == "PUT" and action == "schedule":
                        body = self._body()
                        self._send(
                            HTTPStatus.OK,
                            asdict(app.cron.set_schedule(dataset, str(body["expression"]), str(body["timezone"]))),
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
                        event_id = app.events.ack(str(body["event_id"]))
                        count = 1
                        response = {"acknowledged": count, "event_id": event_id}
                    self._send(HTTPStatus.OK, response)
                    return
                self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except QueueFullError as exc:
                self._send(HTTPStatus.CONFLICT, {"error": str(exc)})
            except AmbiguousIdentifierError as exc:
                self._send(HTTPStatus.CONFLICT, {"error": str(exc)})
            except TaskNotFoundError:
                self._send(HTTPStatus.NOT_FOUND, {"error": "task_not_found"})
            except IdentifierNotFoundError:
                self._send(HTTPStatus.NOT_FOUND, {"error": "event_not_found"})
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


def _redact(key: str, value: Any) -> Any:
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
