from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path
from typing import TextIO

from findata import __version__
from findata.server import FindataServer, initialize_workspace


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = argparse.ArgumentParser(prog="findata-server")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("workspace")
    start = commands.add_parser("start")
    start.add_argument("workspace")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8765)
    start.add_argument("--provider-mode", choices=("real", "mock"), default="real")
    args = parser.parse_args(argv)
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


if __name__ == "__main__":
    raise SystemExit(main())
