from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import json
import signal
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import click

from findata import __version__
from findata.cli.click_parser import DocumentedCommand, DocumentedGroup
from findata.sdk.plugins import plugin_load_errors
from findata.server.server import FindataServer, ServerAlreadyRunningError, initialize_workspace


_LOCAL_OPENER = build_opener(ProxyHandler({}))


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            args = _command_tree().main(
                args=argv,
                prog_name="findata-server",
                standalone_mode=False,
            )
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.UsageError as exc:
        stderr.write(f"Error: {exc.format_message()}\n")
        stderr.flush()
        return 2
    if isinstance(args, int):
        return args
    if args is None:
        stderr.write("Error: a command is required\n")
        stderr.flush()
        return 2
    if args.command == "init":
        workspace = Path(args.workspace).expanduser().resolve()
        initialize_workspace(workspace)
        stdout.write(f"Initialized FinData workspace at {workspace}\n")
        stdout.flush()
        return 0
    if args.command == "token":
        workspace = Path(args.workspace).expanduser().resolve()
        token_path = workspace / "token"
        if not token_path.is_file():
            stderr.write(
                f"Error: no workspace token at {token_path}; "
                "run findata-server init <workspace> first\n"
            )
            stderr.flush()
            return 1
        stdout.write(token_path.read_text(encoding="utf-8"))
        stdout.flush()
        return 0
    workspace = Path(args.workspace).expanduser().resolve()
    if args.command == "status":
        status, detail = _server_status(workspace)
        stream = stdout if status == "running" else stderr
        stream.write(detail + "\n")
        stream.flush()
        return 0 if status == "running" else 1
    if args.command == "stop":
        status, detail = _stop_server(workspace)
        stream = stdout if status == "stopped" else stderr
        stream.write(detail + "\n")
        stream.flush()
        return 0 if status == "stopped" else 1
    if args.command == "restart":
        status, detail = _stop_server(workspace, allow_not_running=True)
        if status == "error":
            stderr.write(detail + "\n")
            stderr.flush()
            return 1
        if status == "stopped":
            stdout.write(detail + "\n")
            stdout.flush()
    return _serve(workspace, args, stdout=stdout, stderr=stderr)


def _serve(workspace: Path, args: SimpleNamespace, *, stdout: TextIO, stderr: TextIO) -> int:
    stopped = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        server = FindataServer(
            workspace,
            host=args.host,
            port=args.port,
            provider_mode=args.provider_mode,
        )
        server.start_background()
    except ServerAlreadyRunningError:
        stderr.write(f"Error: workspace {workspace} already has a running server\n")
        stderr.flush()
        return 1
    except OSError as exc:
        stderr.write(f"Error: cannot start the server: {exc}\n")
        stderr.flush()
        return 1
    summaries = server.provider_summaries()
    labels = {
        str(item["name"]): (
            "mock" if item["mode"] == "mock" else "ready" if item["ready"] else "not configured"
        )
        for item in summaries
    }
    load_errors = plugin_load_errors()
    total_errors = sum(len(errors) for errors in load_errors.values())
    if bool(getattr(stdout, "isatty", lambda: False)()):
        lines = [
            "✓ FinData server ready\n",
            f"  Version    {__version__}\n",
            f"  Workspace  {workspace}\n",
            f"  Token      {workspace / 'token'}\n",
            f"  API        {server.base_url}\n",
            f"  Providers  {', '.join(f'{name} ({label})' for name, label in labels.items())}\n",
        ]
        if total_errors:
            lines.append(
                f"  Plugins    {total_errors} failed to load"
                " (use `findata plugin check <name>` to inspect)\n"
            )
        stdout.write("".join(lines))
    else:
        suffix = f" load_errors={total_errors}" if total_errors else ""
        stdout.write(
            f"FinData server ready version={__version__} workspace={workspace} "
            f"api={server.base_url} "
            f"providers={','.join(f'{name}:{label}' for name, label in labels.items())}"
            f"{suffix}\n"
        )
    stdout.flush()
    try:
        while not stopped.wait(0.1):
            if not (workspace / "server.json").exists():
                break
    finally:
        server.shutdown()
    return 0


def _server_status(workspace: Path) -> tuple[str, str]:
    try:
        descriptor = _server_descriptor(workspace)
    except RuntimeError as exc:
        return "stopped", str(exc)
    try:
        status = _server_request(descriptor, workspace, "GET", "/v1/system/status")
    except RuntimeError as exc:
        return "error", f"server descriptor is stale or unreachable: {exc}"
    return (
        "running",
        "FinData server running "
        f"pid={status.get('pid')} workspace={status.get('workspace')} "
        f"api=http://{descriptor['host']}:{descriptor['port']}",
    )


def _stop_server(workspace: Path, *, allow_not_running: bool = False) -> tuple[str, str]:
    try:
        descriptor = _server_descriptor(workspace)
    except RuntimeError as exc:
        if allow_not_running:
            return "not_running", str(exc)
        return "error", str(exc)
    try:
        response = _server_request(descriptor, workspace, "POST", "/v1/system/stop", {})
    except RuntimeError as exc:
        return "error", f"server descriptor is stale or unreachable: {exc}"
    if response.get("status") != "stopping":
        return "error", "server did not accept the shutdown request"
    deadline = time.monotonic() + 5
    descriptor_path = workspace / "server.json"
    while descriptor_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    if descriptor_path.exists():
        return "error", "server accepted shutdown but did not stop within 5 seconds"
    return "stopped", f"Stopped FinData server for {workspace}"


def _server_descriptor(workspace: Path) -> dict[str, object]:
    path = workspace / "server.json"
    try:
        descriptor = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"no running server for workspace {workspace}") from exc
    if not isinstance(descriptor, dict) or not isinstance(descriptor.get("host"), str) or not isinstance(
        descriptor.get("port"), int
    ):
        raise RuntimeError(f"invalid server descriptor at {path}")
    return descriptor


def _server_request(
    descriptor: dict[str, object], workspace: Path, method: str, path: str, body: object | None = None
) -> dict[str, object]:
    try:
        token = (workspace / "token").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"cannot read workspace token: {exc}") from exc
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        f"http://{descriptor['host']}:{descriptor['port']}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with _LOCAL_OPENER.open(request, timeout=2) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"server returned {exc.code}") from exc
    except (URLError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc
    if not isinstance(result, dict):
        raise RuntimeError("server returned a non-object response")
    return result


def _command_tree() -> click.Group:
    @click.group(name="findata-server", cls=DocumentedGroup)
    @click.version_option(version=__version__, prog_name="findata-server")
    def root() -> None:
        """Initialize and run a local FinData server."""

    @root.command(
        "init",
        cls=DocumentedCommand,
        help="Create a secured local workspace and register built-in datasets.",
    )
    @click.argument("workspace", type=click.Path(path_type=Path))
    def initialize(workspace: Path) -> SimpleNamespace:
        return SimpleNamespace(command="init", workspace=workspace)

    @root.command(
        "token",
        cls=DocumentedCommand,
        help="Print the workspace API token, for example to sign in to the Web UI.",
    )
    @click.argument("workspace", type=click.Path(path_type=Path))
    def token(workspace: Path) -> SimpleNamespace:
        return SimpleNamespace(command="token", workspace=workspace)

    @root.command(
        "start",
        cls=DocumentedCommand,
        help="Run the authenticated local API and task service in the foreground.",
    )
    @click.argument("workspace", type=click.Path(path_type=Path))
    @click.option(
        "--host",
        default="127.0.0.1",
        show_default=True,
        help="Interface on which the local HTTP API listens.",
    )
    @click.option(
        "--port",
        type=click.IntRange(0, 65535),
        default=8765,
        show_default=True,
        help="TCP port for the local API; 0 selects an ephemeral port.",
    )
    @click.option(
        "--provider-mode",
        type=click.Choice(["real", "mock"]),
        default="real",
        show_default=True,
        help="Use real provider APIs or deterministic local mock responses.",
    )
    def start(workspace: Path, host: str, port: int, provider_mode: str) -> SimpleNamespace:
        return SimpleNamespace(
            command="start",
            workspace=workspace,
            host=host,
            port=port,
            provider_mode=provider_mode,
        )

    @root.command("status", cls=DocumentedCommand, help="Show whether this workspace server is running.")
    @click.argument("workspace", type=click.Path(path_type=Path))
    def status(workspace: Path) -> SimpleNamespace:
        return SimpleNamespace(command="status", workspace=workspace)

    @root.command("stop", cls=DocumentedCommand, help="Gracefully stop this workspace's running server.")
    @click.argument("workspace", type=click.Path(path_type=Path))
    def stop(workspace: Path) -> SimpleNamespace:
        return SimpleNamespace(command="stop", workspace=workspace)

    @root.command(
        "restart",
        cls=DocumentedCommand,
        help="Gracefully stop this workspace server, then run it in the foreground.",
    )
    @click.argument("workspace", type=click.Path(path_type=Path))
    @click.option(
        "--host",
        default="127.0.0.1",
        show_default=True,
        help="Interface on which the local HTTP API listens.",
    )
    @click.option(
        "--port",
        type=click.IntRange(0, 65535),
        default=8765,
        show_default=True,
        help="TCP port for the local API; 0 selects an ephemeral port.",
    )
    @click.option(
        "--provider-mode",
        type=click.Choice(["real", "mock"]),
        default="real",
        show_default=True,
        help="Use real provider APIs or deterministic local mock responses.",
    )
    def restart(workspace: Path, host: str, port: int, provider_mode: str) -> SimpleNamespace:
        return SimpleNamespace(
            command="restart",
            workspace=workspace,
            host=host,
            port=port,
            provider_mode=provider_mode,
        )

    for command, verb in (
        (initialize, "create"),
        (start, "run"),
        (token, "inspect"),
        (status, "inspect"),
        (stop, "stop"),
        (restart, "restart"),
    ):
        for parameter in command.params:
            if isinstance(parameter, click.Argument) and parameter.name == "workspace":
                parameter.help = f"Workspace directory to {verb}."

    return root


if __name__ == "__main__":
    raise SystemExit(main())
