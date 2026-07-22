from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from findata import __version__
from findata.presentation import CLIOutput


class CLIUsageError(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIUsageError(message)


@dataclass(frozen=True, slots=True)
class TaskDetached(Exception):
    handle: str


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    environ: Mapping[str, str] | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    environment = os.environ if environ is None else environ
    output_format = "human"
    color_mode = "auto"
    try:
        output_format = _extract_format(arguments)
        color_mode = _extract_color(arguments)
        quiet, verbose, progress_enabled = _extract_presentation(arguments)
        output = CLIOutput(
            output_format=output_format,
            color_mode=color_mode,
            stdout=stdout,
            stderr=stderr,
            environ=environment,
            quiet=quiet,
            verbose=verbose,
            progress_enabled=progress_enabled,
        )
        _normalize_aliases(arguments)
        parser = _parser()
        args = parser.parse_args(arguments)
        if args.group == "completion":
            stdout.write(_completion_script(args.shell))
            return 0
        if args.group == "_complete":
            try:
                completion_client = _Client(resolve_workspace(args.workspace, environ=environment))
                items = _dynamic_completion(completion_client, list(args.words))
            except (RuntimeError, HTTPError, URLError, ValueError):
                items = _static_completion(list(args.words))
            stdout.write("".join(f"{item}\n" for item in items))
            return 0
        _validate_cli_args(args, output_format=output_format)
        client = _Client(resolve_workspace(args.workspace, environ=environment))
        if output_format == "human":
            configured_timezone = client.optional_config("display.timezone")
            if isinstance(configured_timezone, str):
                output.set_display_timezone(configured_timezone)
        result = _execute(client, args, output=output, stdin=stdin)
        output.finish_progress()
        if _render_nonfollowing_task_logs(args, result, output=output):
            return 0
        handle = (
            str(result.get("handle_id"))
            if isinstance(result, Mapping) and result.get("handle_id")
            else None
        )
        output.finish_diagnostics(handle)
        output.result(result, record_type=_result_record_type(args))
        return (
            0
            if not (isinstance(result, dict) and result.get("status") in {"failed", "canceled"})
            else 1
        )
    except TaskDetached as exc:
        output.detached(exc.handle)
        return 130
    except CLIUsageError as exc:
        output.finish_progress()
        output.error(str(exc))
        return 2
    except (ValueError, RuntimeError, HTTPError, URLError) as exc:
        output = locals().get("output") or CLIOutput(
            output_format=output_format,
            color_mode=color_mode,
            stdout=stdout,
            stderr=stderr,
            environ=environment,
            quiet=locals().get("quiet", False),
            verbose=locals().get("verbose", False),
            progress_enabled=locals().get("progress_enabled", True),
        )
        output.finish_progress()
        output.error(str(exc))
        return 1


def _execute(
    client: _Client,
    args: argparse.Namespace,
    *,
    output: CLIOutput,
    stdin: TextIO = sys.stdin,
) -> object:
    if args.group == "config" and args.action == "set":
        if args.env:
            value: object = {"env": args.env}
        elif args.stdin:
            value = stdin.readline().rstrip("\n")
        elif args.value_json is not None:
            value = _json_value(args.value_json, stdin=stdin)
        elif args.value is not None:
            value = args.value
        else:
            raise ValueError("config set requires a value, --value-json, --env, or --stdin")
        return client.request("POST", "/v1/config", {"key": args.key, "value": value})
    if args.group == "config" and args.action in {"get", "ls"}:
        suffix = f"?{urlencode({'key': args.key})}" if getattr(args, "key", None) else ""
        return client.request("GET", f"/v1/config{suffix}")
    if args.group == "config" and args.action == "unset":
        return client.request("DELETE", f"/v1/config/{args.key}")
    if args.group == "provider" and args.action == "check":
        return client.request("GET", f"/v1/providers/{args.name}/check")
    if args.group == "provider" and args.action == "ls":
        return client.request("GET", "/v1/providers")
    if args.group == "provider" and args.action == "status":
        return client.request("GET", f"/v1/providers/{args.name}")
    if args.group == "dataset" and args.action == "ls":
        return client.request("GET", "/v1/datasets")
    if args.group == "dataset" and args.action in {"describe", "status"}:
        if args.action == "status" and args.all:
            return client.request("GET", "/v1/datasets")
        suffix = "status" if args.action == "status" else ""
        return client.request(
            "GET", f"/v1/datasets/{args.dataset}{('/' + suffix) if suffix else ''}"
        )
    if args.group == "dataset" and args.action == "operations":
        return client.request("GET", f"/v1/datasets/{args.dataset}/operations")
    if args.group == "dataset" and args.action == "operation":
        return client.request("GET", f"/v1/datasets/{args.dataset}/operations/{args.operation}")
    if args.group == "dataset" and args.action == "reset":
        if not args.yes:
            if output.output_format != "human" or not getattr(stdin, "isatty", lambda: False)():
                raise CLIUsageError("dataset reset requires --yes in non-interactive use")
            output.stderr.write(
                f"Reset dataset {args.dataset!r} and delete its committed data? [y/N] "
            )
            output.stderr.flush()
            if stdin.readline().strip().lower() not in {"y", "yes"}:
                raise CLIUsageError("dataset reset canceled")
        return client.request("POST", f"/v1/datasets/{args.dataset}/reset", {"confirm": True})
    if args.group == "dataset" and args.action in {"update", "complete", "refresh"}:
        operands = _dataset_operands(args)
        plan_path = f"/v1/datasets/{args.dataset}/operations/{args.action}/plan"
        if args.dry_run:
            return client.request("POST", plan_path, {"operands": operands})
        if output.verbose and output.output_format == "human":
            _render_plan_preview(output, client.request("POST", plan_path, {"operands": operands}))
        submitted = client.request(
            "POST",
            "/v1/tasks",
            {"dataset": args.dataset, "operation": args.action, "operands": operands},
        )
        if not (args.wait or args.follow):
            return submitted
        output.accepted(submitted)
        return _wait_for_task(
            client, str(submitted["handle_id"]), output=output, follow=args.follow
        )
    if args.group == "task" and args.action == "run":
        operands = _params(args.param, args.params, stdin=stdin)
        plan_path = f"/v1/datasets/{args.dataset}/operations/{args.operation}/plan"
        if args.dry_run:
            return client.request("POST", plan_path, {"operands": operands})
        if output.verbose and output.output_format == "human":
            _render_plan_preview(output, client.request("POST", plan_path, {"operands": operands}))
        submitted = client.request(
            "POST",
            "/v1/tasks",
            {"dataset": args.dataset, "operation": args.operation, "operands": operands},
        )
        if not (args.wait or args.follow):
            return submitted
        handle = str(submitted["handle_id"])
        output.accepted(submitted)
        emitted_logs = 0
        try:
            while True:
                if args.follow:
                    logs = client.request("GET", f"/v1/tasks/{handle}/logs")["items"]
                    for message in logs[emitted_logs:]:
                        _render_task_log(output, message, handle_id=handle)
                    emitted_logs = len(logs)
                status = client.request("GET", f"/v1/tasks/{handle}")
                if status["status"] in {"succeeded", "failed", "canceled"}:
                    return status
                output.state(status)
                time.sleep(0.05)
        except KeyboardInterrupt as exc:
            raise TaskDetached(handle) from exc
    if args.group == "task" and args.action == "retry":
        submitted = client.request("POST", f"/v1/tasks/{args.handle}/retry", {})
        if not (args.wait or args.follow):
            return submitted
        output.accepted(submitted)
        return _wait_for_task(
            client, str(submitted["handle_id"]), output=output, follow=args.follow
        )
    if args.group == "task" and args.action == "explain":
        return client.request("GET", f"/v1/tasks/{args.handle}/explain")
    if args.group == "task" and args.action == "watch":
        return _wait_for_task(client, str(args.handle), output=output, follow=True)
    if args.group == "task" and args.action == "ls":
        query = {
            key: value
            for key, value in {"dataset": args.dataset, "status": args.status}.items()
            if value
        }
        if args.all:
            query["all"] = "true"
        suffix = f"?{urlencode(query)}" if query else ""
        return client.request("GET", f"/v1/tasks{suffix}")
    if args.group == "task" and args.action == "status":
        return client.request("GET", f"/v1/tasks/{args.handle}")
    if args.group == "task" and args.action == "logs":
        if not args.follow:
            return client.request("GET", f"/v1/tasks/{args.handle}/logs")
        emitted = 0
        try:
            while True:
                logs = client.request("GET", f"/v1/tasks/{args.handle}/logs")["items"]
                for message in logs[emitted:]:
                    _render_task_log(output, message, handle_id=str(args.handle))
                emitted = len(logs)
                status = client.request("GET", f"/v1/tasks/{args.handle}")
                if status["status"] in {"succeeded", "failed", "canceled"}:
                    return status
                output.state(status)
                time.sleep(0.05)
        except KeyboardInterrupt as exc:
            raise TaskDetached(str(args.handle)) from exc
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

    def optional_config(self, key: str) -> object | None:
        try:
            return self.request("GET", f"/v1/config?{urlencode({'key': key})}").get("value")
        except RuntimeError as exc:
            if "server returned 404:" in str(exc):
                return None
            raise


def _render_task_log(output: CLIOutput, item: object, *, handle_id: str) -> None:
    if isinstance(item, Mapping) and item.get("type") == "task.diagnostic":
        diagnostic = dict(item)
        diagnostic.setdefault("handle_id", handle_id)
        output.diagnostic(diagnostic)
    else:
        output.log(str(item))


def _render_nonfollowing_task_logs(
    args: argparse.Namespace,
    result: object,
    *,
    output: CLIOutput,
) -> bool:
    if not (
        args.group == "task"
        and args.action == "logs"
        and not args.follow
        and output.output_format in {"human", "jsonl"}
        and isinstance(result, Mapping)
        and isinstance(result.get("items"), list)
    ):
        return False
    handle_id = str(result.get("handle_id") or args.handle)
    for item in result["items"]:
        _render_task_log(output, item, handle_id=handle_id)
    output.finish_diagnostics(handle_id)
    return True


def _wait_for_task(
    client: _Client,
    handle: str,
    *,
    output: CLIOutput,
    follow: bool,
) -> dict[str, object]:
    emitted_logs = 0
    try:
        while True:
            if follow:
                logs = client.request("GET", f"/v1/tasks/{handle}/logs")["items"]
                for message in logs[emitted_logs:]:
                    _render_task_log(output, message, handle_id=handle)
                emitted_logs = len(logs)
            status = client.request("GET", f"/v1/tasks/{handle}")
            if status["status"] in {"succeeded", "failed", "canceled"}:
                return status
            output.state(status)
            time.sleep(0.05)
    except KeyboardInterrupt as exc:
        raise TaskDetached(handle) from exc


def _dataset_operands(args: argparse.Namespace) -> dict[str, object]:
    result: dict[str, object] = {}
    for name in ("symbols", "indexes", "exchanges"):
        values = getattr(args, name, None)
        if values:
            result[name] = values
    if args.timerange and (args.range_start or args.range_end):
        raise CLIUsageError("--timerange cannot be combined with --from or --to")
    if bool(args.range_start) != bool(args.range_end):
        raise CLIUsageError("--from and --to must be supplied together")
    if args.timerange:
        result["timerange"] = args.timerange
    elif args.range_start and args.range_end:
        result["timerange"] = f"{args.range_start}:{args.range_end}"
    return result


def _render_plan_preview(output: CLIOutput, plan: Mapping[str, object]) -> None:
    requests = plan.get("estimated_provider_requests")
    request_text = "unknown" if requests is None else str(requests)
    output.log(
        f"Plan: {plan.get('strategy', 'plugin operation')}; "
        f"estimated provider requests: {request_text}"
    )
    for dependency in plan.get("dependencies", []):
        if isinstance(dependency, Mapping):
            output.log(
                f"Dependency: {dependency.get('dataset')} ({dependency.get('state', 'unknown')})"
            )


def _dynamic_completion(client: _Client, words: list[str]) -> list[str]:
    candidates: list[str]
    if not words:
        candidates = "task dataset provider cron events config system completion".split()
    elif (
        words[0] == "dataset"
        and len(words) >= 2
        and len(words) <= 3
        and words[1]
        in {
            "update",
            "complete",
            "refresh",
            "describe",
            "operations",
            "status",
        }
    ):
        candidates = [str(item["name"]) for item in client.request("GET", "/v1/datasets")["items"]]
    elif words[0] == "provider" and len(words) >= 2:
        candidates = [str(item["name"]) for item in client.request("GET", "/v1/providers")["items"]]
    elif (
        words[0] == "task"
        and len(words) >= 2
        and words[1]
        in {
            "status",
            "logs",
            "cancel",
            "watch",
            "retry",
            "explain",
        }
    ):
        candidates = [
            str(item["handle_id"]) for item in client.request("GET", "/v1/tasks?all=true")["items"]
        ]
    elif words[0] == "config" and len(words) >= 2:
        values = client.request("GET", "/v1/config").get("values", {})
        candidates = list(values) if isinstance(values, Mapping) else []
    elif (
        words[0] == "dataset" and len(words) >= 4 and words[1] in {"update", "complete", "refresh"}
    ):
        description = client.request("GET", f"/v1/datasets/{words[2]}/operations/{words[1]}")
        properties = description.get("properties", {})
        candidates = [f"--{name}" for name in properties] if isinstance(properties, Mapping) else []
        candidates.extend(["--from", "--to", "--wait", "--follow", "--dry-run"])
    else:
        return _static_completion(words)
    prefix = words[-1] if words else ""
    return [item for item in candidates if item.startswith(prefix)]


def _static_completion(words: list[str]) -> list[str]:
    groups = "task dataset provider cron events config system completion".split()
    actions = {
        "task": "run ls status logs cancel watch retry explain".split(),
        "dataset": "ls describe operations operation status reset update complete refresh".split(),
        "provider": "ls status check".split(),
        "cron": "ls enable disable set reset".split(),
        "events": "ls ack".split(),
        "config": "ls get set unset".split(),
        "system": ["status"],
    }
    candidates = groups if len(words) <= 1 else actions.get(words[0], []) if len(words) == 2 else []
    prefix = words[-1] if words else ""
    return [item for item in candidates if item.startswith(prefix)]


def _params(
    values: list[str], source: str | None = None, *, stdin: TextIO = sys.stdin
) -> dict[str, object]:
    if source is not None:
        if values:
            raise ValueError("--param and --params are mutually exclusive")
        if source == "-":
            text = stdin.read()
        elif source.startswith("@"):
            text = Path(source[1:]).read_text(encoding="utf-8")
        else:
            text = source
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid operands JSON: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("--params JSON must be an object")
        return parsed
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
            raise CLIUsageError("--format requires human, json, or jsonl") from exc
        del arguments[index : index + 2]
        if value not in {"human", "json", "jsonl"}:
            raise CLIUsageError(f"unsupported format {value!r}")
        return value
    return "human"


def _extract_color(arguments: list[str]) -> str:
    if "--color" not in arguments:
        return "auto"
    index = arguments.index("--color")
    try:
        value = arguments[index + 1]
    except IndexError as exc:
        raise CLIUsageError("--color requires auto, always, or never") from exc
    del arguments[index : index + 2]
    if value not in {"auto", "always", "never"}:
        raise CLIUsageError("--color requires auto, always, or never")
    return value


def _extract_presentation(arguments: list[str]) -> tuple[bool, bool, bool]:
    quiet = "--quiet" in arguments
    verbose = "--verbose" in arguments
    progress_enabled = "--no-progress" not in arguments
    for flag in ("--quiet", "--verbose", "--no-progress"):
        while flag in arguments:
            arguments.remove(flag)
    if quiet and verbose:
        raise CLIUsageError("--quiet and --verbose are mutually exclusive")
    return quiet, verbose, progress_enabled


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="findata")
    parser.add_argument("--version", action="version", version=f"findata {__version__}")
    parser.add_argument("--workspace", type=Path)
    groups = parser.add_subparsers(dest="group", required=True)

    config = groups.add_parser("config").add_subparsers(dest="action", required=True)
    config_set = config.add_parser("set")
    config_set.add_argument("key")
    config_set.add_argument("value", nargs="?")
    config_set.add_argument("--value-json")
    config_set.add_argument("--env")
    config_set.add_argument("--stdin", action="store_true")
    config_get = config.add_parser("get")
    config_get.add_argument("key", nargs="?")
    config.add_parser("ls")
    config_unset = config.add_parser("unset")
    config_unset.add_argument("key")

    provider = groups.add_parser("provider").add_subparsers(dest="action", required=True)
    provider.add_parser("ls")
    provider_status = provider.add_parser("status")
    provider_status.add_argument("name")
    provider_check = provider.add_parser("check")
    provider_check.add_argument("name")

    dataset = groups.add_parser("dataset").add_subparsers(dest="action", required=True)
    dataset.add_parser("ls")
    for action in ("describe", "operations"):
        command = dataset.add_parser(action)
        command.add_argument("dataset")
    dataset_status = dataset.add_parser("status")
    dataset_status.add_argument("dataset", nargs="?")
    dataset_status.add_argument("--all", action="store_true")
    dataset_operation = dataset.add_parser("operation")
    dataset_operation.add_argument("dataset")
    dataset_operation.add_argument("operation")
    dataset_reset = dataset.add_parser("reset")
    dataset_reset.add_argument("dataset")
    dataset_reset.add_argument("--yes", action="store_true")
    for action in ("update", "complete", "refresh"):
        command = dataset.add_parser(action)
        command.add_argument("dataset")
        for operand in ("symbols", "indexes", "exchanges"):
            command.add_argument(f"--{operand}", action="append")
        command.add_argument("--timerange")
        command.add_argument("--from", dest="range_start")
        command.add_argument("--to", dest="range_end")
        command.add_argument("--wait", action="store_true")
        command.add_argument("--follow", action="store_true")
        command.add_argument("--dry-run", action="store_true")

    task = groups.add_parser("task").add_subparsers(dest="action", required=True)
    run = task.add_parser("run")
    run.add_argument("dataset")
    run.add_argument("operation", nargs="?", default="update")
    run.add_argument("--param", action="append", default=[])
    run.add_argument("--params")
    run.add_argument("--wait", action="store_true")
    run.add_argument("--follow", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    listing = task.add_parser("ls")
    listing.add_argument("--dataset")
    listing.add_argument("--status")
    listing.add_argument("--all", action="store_true")
    for action in ("status", "cancel"):
        command = task.add_parser(action)
        command.add_argument("handle")
    logs = task.add_parser("logs")
    logs.add_argument("handle")
    logs.add_argument("--follow", "-f", action="store_true")
    for action in ("watch", "explain"):
        command = task.add_parser(action)
        command.add_argument("handle")
    retry = task.add_parser("retry")
    retry.add_argument("handle")
    retry.add_argument("--wait", action="store_true")
    retry.add_argument("--follow", action="store_true")

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

    completion = groups.add_parser("completion")
    completion.add_argument("shell", choices=("bash", "zsh", "fish"))
    dynamic = groups.add_parser("_complete", help=argparse.SUPPRESS)
    dynamic.add_argument("words", nargs="*")
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


def resolve_workspace(
    explicit: Path | None,
    *,
    environ: dict[str, str] | os._Environ[str] | None = None,
    cwd: Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    if explicit is not None:
        candidates = [Path(explicit)]
    elif environment.get("FINDATA_WORKSPACE"):
        candidates = [Path(environment["FINDATA_WORKSPACE"])]
    else:
        start = (cwd or Path.cwd()).resolve()
        candidates = [start, *start.parents]
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if (resolved / "workspace.json").is_file():
            return resolved
    raise RuntimeError("no findata workspace found; run findata-server init <path>")


def _completion_script(shell: str) -> str:
    if shell == "bash":
        return (
            "_findata_complete() { COMPREPLY=( $(findata _complete "
            '"${COMP_WORDS[@]:1:$COMP_CWORD}" 2>/dev/null) ); }\n'
            "complete -F _findata_complete findata\n"
        )
    if shell == "zsh":
        return (
            "#compdef findata\n_findata_complete() { compadd -- "
            '${(f)"$(findata _complete ${words[2,-1]} 2>/dev/null)"}; }\n'
            "compdef _findata_complete findata\n"
        )
    return "complete -c findata -f -a '(findata _complete (commandline -opc)[2..-1] 2>/dev/null)'\n"


def _normalize_aliases(arguments: list[str]) -> None:
    return


def _validate_cli_args(args: argparse.Namespace, *, output_format: str = "human") -> None:
    if args.group == "events" and args.action == "ack":
        if bool(args.event_id) == bool(args.all):
            raise ValueError("events ack requires an event ID or --all")
    if args.group == "dataset" and args.action == "status":
        if bool(args.dataset) == bool(args.all):
            raise ValueError("dataset status requires a dataset or --all")
    if args.group == "task" and args.action == "run" and args.param and args.params:
        raise ValueError("--param and --params are mutually exclusive")
    if args.group == "task" and args.action in {"run", "logs", "retry", "watch"}:
        if (getattr(args, "follow", False) or args.action == "watch") and output_format == "json":
            raise CLIUsageError("--follow is a stream; use --format JSONL instead of JSON")
    if (
        args.group == "dataset"
        and args.action in {"update", "complete", "refresh"}
        and args.follow
        and output_format == "json"
    ):
        raise CLIUsageError("--follow is a stream; use --format JSONL instead of JSON")
    if args.group == "config" and args.action == "set":
        sources = sum(
            (
                args.value is not None,
                args.value_json is not None,
                bool(args.env),
                bool(args.stdin),
            )
        )
        if sources != 1:
            raise ValueError("config set requires exactly one value source")
        lowered = args.key.lower()
        secret = any(word in lowered for word in ("token", "secret", "password", "credential"))
        if secret and (args.value is not None or args.value_json is not None):
            raise ValueError("secret configuration must use --stdin or --env")


def _json_value(source: str, *, stdin: TextIO) -> object:
    if source == "-":
        text = stdin.read()
    elif source.startswith("@"):
        text = Path(source[1:]).read_text(encoding="utf-8")
    else:
        text = source
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid configuration JSON: {exc.msg}") from exc


def _result_record_type(args: argparse.Namespace) -> str:
    if args.group == "task" and args.action in {"run", "logs"}:
        return "task.result"
    return f"{args.group}.{getattr(args, 'action', 'result')}.result"


if __name__ == "__main__":
    raise SystemExit(main())
