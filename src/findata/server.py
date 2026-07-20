from __future__ import annotations

import fcntl
import hmac
import json
import os
import secrets
import threading
from dataclasses import asdict
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from findata.loader import DatasetNotReadyError
from findata.operations import OperationWorker, register_v1_datasets
from findata.storage import Workspace
from findata.taskrunner import QueueFullError, TaskNotFoundError, TaskRunner


class ServerAlreadyRunningError(RuntimeError):
    pass


def initialize_workspace(root: Path) -> Workspace:
    workspace = Workspace.init(root)
    register_v1_datasets(workspace)
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
        self.host = host
        self.port = port
        self.provider_mode = provider_mode
        self.today = today or date.today()
        self.token = (self.root / "token").read_text(encoding="utf-8").strip()
        self._lock_file: Any = None
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.taskrunner = TaskRunner(
            self.root,
            OperationWorker(
                workspace=self.root,
                provider=provider_mode,
                token="mock-token" if provider_mode == "mock" else "",
                today=self.today.isoformat(),
            ),
            global_concurrency=global_concurrency,
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
            self.taskrunner.start()
            handler = _handler_for(self)
            self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
            self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._thread.start()
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
                    self._send(
                        HTTPStatus.OK,
                        {
                            "status": "running",
                            "pid": os.getpid(),
                            "tasks": len(app.taskrunner.list_handles()),
                        },
                    )
                    return
                if method == "POST" and parts == ["v1", "tasks"]:
                    body = self._body()
                    handle = app.taskrunner.submit(
                        str(body["dataset"]),
                        str(body.get("operation") or "update"),
                        dict(body.get("operands") or {}),
                        owner=str(body.get("owner") or "api"),
                    )
                    self._send(HTTPStatus.ACCEPTED, {"handle_id": handle})
                    return
                if method == "GET" and parts == ["v1", "tasks"]:
                    items = app.taskrunner.list_handles(
                        dataset=_query_one(query, "dataset"),
                        status=_query_one(query, "status"),
                    )
                    self._send(HTTPStatus.OK, {"items": [asdict(item) for item in items]})
                    return
                if len(parts) >= 3 and parts[:2] == ["v1", "tasks"]:
                    handle_id = parts[2]
                    if method == "GET" and len(parts) == 3:
                        self._send(HTTPStatus.OK, asdict(app.taskrunner.status(handle_id)))
                        return
                    if method == "GET" and parts[3:] == ["logs"]:
                        self._send(HTTPStatus.OK, {"items": app.taskrunner.logs(handle_id)})
                        return
                    if method == "POST" and parts[3:] == ["cancel"]:
                        current = app.taskrunner.status(handle_id)
                        if current.status in {"succeeded", "failed", "canceled"}:
                            self._send(HTTPStatus.OK, asdict(current))
                        else:
                            result = app.taskrunner.cancel(handle_id)
                            self._send(HTTPStatus.OK, asdict(result))
                        return
                if method == "POST" and parts == ["v1", "config"]:
                    body = self._body()
                    app.workspace.set_config(str(body["key"]), body["value"])
                    self._send(HTTPStatus.OK, {"updated": True})
                    return
                if method == "GET" and parts == ["v1", "providers", "tushare", "check"]:
                    configured = app.workspace.get_config("provider.tushare.token")
                    ready = app.provider_mode == "mock" or _configured_secret_ready(configured)
                    self._send(HTTPStatus.OK, {"provider": "tushare", "ready": ready})
                    return
                if len(parts) == 4 and parts[:2] == ["v1", "datasets"] and parts[3:] == ["universe"]:
                    dataset = parts[2]
                    if method == "GET":
                        self._send(HTTPStatus.OK, {"selectors": app.workspace.get_universe(dataset)})
                    elif method == "PUT":
                        body = self._body()
                        app.workspace.set_universe(dataset, list(body.get("selectors") or []))
                        self._send(HTTPStatus.OK, {"selectors": app.workspace.get_universe(dataset)})
                    return
                self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except QueueFullError as exc:
                self._send(HTTPStatus.CONFLICT, {"error": str(exc)})
            except TaskNotFoundError:
                self._send(HTTPStatus.NOT_FOUND, {"error": "task_not_found"})
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


def _configured_secret_ready(value: Any) -> bool:
    if isinstance(value, dict) and isinstance(value.get("env"), str):
        return bool(os.environ.get(value["env"]))
    return bool(value)


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
