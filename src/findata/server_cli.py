from __future__ import annotations

import argparse
from pathlib import Path

from findata.server import FindataServer, initialize_workspace


def main(argv: list[str] | None = None) -> int:
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
        initialize_workspace(Path(args.workspace))
        return 0
    server = FindataServer(
        Path(args.workspace),
        host=args.host,
        port=args.port,
        provider_mode=args.provider_mode,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

