from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    output_format = _extract_format(arguments)
    parser = _parser()
    try:
        args = parser.parse_args(arguments)
        client = _Client(Path(args.workspace))
        result = _execute(client, args)
        _print_result(result, output_format, stdout)
        return 0 if not (isinstance(result, dict) and result.get("status") in {"failed", "canceled"}) else 1
    except (ValueError, RuntimeError, HTTPError, URLError) as exc:
        stderr.write(f"findata: {exc}\n")
        return 1


def _execute(client: _Client, args: argparse.Namespace) -> object:
    if args.group == "config" and args.action == "set":
        if args.env:
            value: object = {"env": args.env}
        elif args.stdin:
            value = sys.stdin.readline().rstrip("\n")
        elif args.value is not None:
            value = args.value
        else:
            raise ValueError("config set requires a value, --env, or --stdin")
        return client.request("POST", "/v1/config", {"key": args.key, "value": value})
    if args.group == "config" and args.action in {"get", "ls"}:
        suffix = f"?{urlencode({'key': args.key})}" if getattr(args, "key", None) else ""
        return client.request("GET", f"/v1/config{suffix}")
    if args.group == "config" and args.action == "unset":
        return client.request("DELETE", f"/v1/config/{args.key}")
    if args.group == "provider" and args.action == "check":
        return client.request("GET", f"/v1/providers/{args.name}/check")
    if args.group == "dataset" and args.action == "universe":
        if args.universe_action == "set":
            return client.request(
                "PUT", f"/v1/datasets/{args.dataset}/universe", {"selectors": args.selectors}
            )
        return client.request("GET", f"/v1/datasets/{args.dataset}/universe")
    if args.group == "task" and args.action == "run":
        operands = _params(args.param)
        submitted = client.request(
            "POST",
            "/v1/tasks",
            {"dataset": args.dataset, "operation": args.operation, "operands": operands},
        )
        if not args.wait:
            return submitted
        handle = submitted["handle_id"]
        while True:
            status = client.request("GET", f"/v1/tasks/{handle}")
            if status["status"] in {"succeeded", "failed", "canceled"}:
                return status
            time.sleep(0.02)
    if args.group == "task" and args.action == "ls":
        query = {key: value for key, value in {"dataset": args.dataset, "status": args.status}.items() if value}
        suffix = f"?{urlencode(query)}" if query else ""
        return client.request("GET", f"/v1/tasks{suffix}")
    if args.group == "task" and args.action == "status":
        return client.request("GET", f"/v1/tasks/{args.handle}")
    if args.group == "task" and args.action == "logs":
        return client.request("GET", f"/v1/tasks/{args.handle}/logs")
    if args.group == "task" and args.action == "cancel":
        return client.request("POST", f"/v1/tasks/{args.handle}/cancel", {})
    if args.group == "cron":
        if args.action == "ls":
            return client.request("GET", "/v1/cron")
        if args.action in {"enable", "disable", "reset"}:
            return client.request("POST", f"/v1/cron/{args.dataset}/{args.action}", {})
        if args.action == "set":
            return client.request(
                "PUT",
                f"/v1/cron/{args.dataset}/schedule",
                {"expression": args.expression, "timezone": args.timezone},
            )
    if args.group == "events":
        if args.action == "ls":
            query: dict[str, str] = {}
            if args.unread:
                query["unread"] = "true"
            if args.severity:
                query["severity"] = args.severity
            if args.since:
                query["since"] = str(time.time() - _duration_seconds(args.since))
            suffix = f"?{urlencode(query)}" if query else ""
            return client.request("GET", f"/v1/events{suffix}")
        return client.request(
            "POST", "/v1/events/ack", {"all": args.all, "event_id": args.event_id}
        )
    if args.group == "system" and args.action == "status":
        return client.request("GET", "/v1/system/status")
    raise ValueError("unsupported command")


class _Client:
    def __init__(self, workspace: Path) -> None:
        try:
            server = json.loads((workspace / "server.json").read_text(encoding="utf-8"))
            self.token = (workspace / "token").read_text(encoding="utf-8").strip()
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"no running server for workspace {workspace}") from exc
        self.base_url = f"http://{server['host']}:{server['port']}"

    def request(self, method: str, path: str, body: object | None = None) -> dict[str, object]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"server returned {exc.code}: {detail}") from exc
        if not isinstance(result, dict):
            raise RuntimeError("server returned a non-object response")
        return result


def _params(values: list[str]) -> dict[str, object]:
    result: dict[str, object] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"invalid --param {value!r}")
        key, item = value.split("=", 1)
        if not key:
            raise ValueError("parameter name cannot be empty")
        if key in result:
            existing = result[key]
            result[key] = [*existing, item] if isinstance(existing, list) else [existing, item]
        else:
            result[key] = item
    return result


def _extract_format(arguments: list[str]) -> str:
    if "--json" in arguments:
        arguments.remove("--json")
        return "json"
    if "--format" in arguments:
        index = arguments.index("--format")
        try:
            value = arguments[index + 1]
        except IndexError as exc:
            raise ValueError("--format requires a value") from exc
        del arguments[index : index + 2]
        if value not in {"human", "json", "jsonl"}:
            raise ValueError(f"unsupported format {value!r}")
        return value
    return "human"


def _print_result(value: object, output_format: str, stdout: TextIO) -> None:
    if output_format in {"json", "jsonl"}:
        stdout.write(json.dumps(value, separators=(",", ":"), default=str) + "\n")
    elif isinstance(value, dict):
        stdout.write(json.dumps(value, indent=2, default=str) + "\n")
    else:
        stdout.write(f"{value}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="findata")
    parser.add_argument("--workspace", required=True)
    groups = parser.add_subparsers(dest="group", required=True)

    config = groups.add_parser("config").add_subparsers(dest="action", required=True)
    config_set = config.add_parser("set")
    config_set.add_argument("key")
    config_set.add_argument("value", nargs="?")
    config_set.add_argument("--env")
    config_set.add_argument("--stdin", action="store_true")
    config_get = config.add_parser("get")
    config_get.add_argument("key", nargs="?")
    config.add_parser("ls")
    config_unset = config.add_parser("unset")
    config_unset.add_argument("key")

    provider = groups.add_parser("provider").add_subparsers(dest="action", required=True)
    provider_check = provider.add_parser("check")
    provider_check.add_argument("name")

    dataset = groups.add_parser("dataset").add_subparsers(dest="action", required=True)
    universe = dataset.add_parser("universe")
    universe_actions = universe.add_subparsers(dest="universe_action", required=True)
    universe_set = universe_actions.add_parser("set")
    universe_set.add_argument("dataset")
    universe_set.add_argument("selectors", nargs="+")
    universe_get = universe_actions.add_parser("get")
    universe_get.add_argument("dataset")

    task = groups.add_parser("task").add_subparsers(dest="action", required=True)
    run = task.add_parser("run")
    run.add_argument("dataset")
    run.add_argument("operation", nargs="?", default="update")
    run.add_argument("--param", action="append", default=[])
    run.add_argument("--wait", action="store_true")
    listing = task.add_parser("ls")
    listing.add_argument("--dataset")
    listing.add_argument("--status")
    for action in ("status", "logs", "cancel"):
        command = task.add_parser(action)
        command.add_argument("handle")

    cron = groups.add_parser("cron").add_subparsers(dest="action", required=True)
    cron.add_parser("ls")
    for action in ("enable", "disable", "reset"):
        command = cron.add_parser(action)
        command.add_argument("dataset")
    cron_set = cron.add_parser("set")
    cron_set.add_argument("dataset")
    cron_set.add_argument("--expression", required=True)
    cron_set.add_argument("--timezone", required=True)

    events = groups.add_parser("events").add_subparsers(dest="action", required=True)
    events_ls = events.add_parser("ls")
    events_ls.add_argument("--unread", action="store_true")
    events_ls.add_argument("--since")
    events_ls.add_argument("--severity", choices=("info", "warning", "error"))
    events_ack = events.add_parser("ack")
    events_ack.add_argument("event_id", nargs="?")
    events_ack.add_argument("--all", action="store_true")

    system = groups.add_parser("system").add_subparsers(dest="action", required=True)
    system.add_parser("status")
    return parser


def _duration_seconds(value: str) -> float:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if len(value) < 2 or value[-1] not in units:
        raise ValueError("duration must end in s, m, h, or d")
    try:
        amount = float(value[:-1])
    except ValueError as exc:
        raise ValueError(f"invalid duration {value!r}") from exc
    if amount < 0:
        raise ValueError("duration cannot be negative")
    return amount * units[value[-1]]


if __name__ == "__main__":
    raise SystemExit(main())
