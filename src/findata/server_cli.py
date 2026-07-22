from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import signal
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import TextIO

import click

from findata import __version__
from findata.click_parser import DocumentedCommand, DocumentedGroup
from findata.server import FindataServer, initialize_workspace


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
    workspace = Path(args.workspace).expanduser().resolve()
    server = FindataServer(
        workspace,
        host=args.host,
        port=args.port,
        provider_mode=args.provider_mode,
    )
    stopped = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    server.start_background()
    provider = "mock" if server._provider_is_mock() else "not configured"
    if bool(getattr(stdout, "isatty", lambda: False)()):
        stdout.write(
            "✓ FinData server ready\n"
            f"  Version    {__version__}\n"
            f"  Workspace  {workspace}\n"
            f"  API        {server.base_url}\n"
            f"  Providers  tushare ({provider})\n"
        )
    else:
        stdout.write(
            f"FinData server ready version={__version__} workspace={workspace} "
            f"api={server.base_url} providers=tushare:{provider}\n"
        )
    stdout.flush()
    try:
        stopped.wait()
    finally:
        server.shutdown()
    return 0


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

    for command in (initialize, start):
        for parameter in command.params:
            if isinstance(parameter, click.Argument) and parameter.name == "workspace":
                parameter.help = "Workspace directory to create or run."

    return root


if __name__ == "__main__":
    raise SystemExit(main())
