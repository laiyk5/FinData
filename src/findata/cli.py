from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import json
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

import click

from findata import __version__
from findata.click_parser import command_tree
from findata.data_access import DataCommandUsageError, ExportOutcome, execute_data_command
from findata.presentation import CLIOutput
from findata.storage import DATABASE_NAME


class CLIUsageError(ValueError):
    pass


class UserCancelled(Exception):
    """The user declined a destructive-operation confirmation."""


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

    def fallback_output() -> CLIOutput:
        return CLIOutput(
            output_format=output_format,
            color_mode=color_mode,
            stdout=stdout,
            stderr=stderr,
            environ=environment,
            quiet=locals().get("quiet", False),
            verbose=locals().get("verbose", False),
            progress_enabled=locals().get("progress_enabled", True),
            pager=lambda text, color: _page_output(text, color=color, stdout=stdout),
        )

    output: CLIOutput | None = None
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
            pager=lambda text, color: _page_output(text, color=color, stdout=stdout),
        )
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                args = command_tree(version=__version__).main(
                    args=arguments,
                    prog_name="findata",
                    standalone_mode=False,
                )
        except click.exceptions.Exit as exc:
            return exc.exit_code
        except click.exceptions.NoArgsIsHelpError as exc:
            stdout.write(exc.format_message())
            stdout.flush()
            return 0
        except click.UsageError as exc:
            raise CLIUsageError(getattr(exc, "message", None) or exc.format_message()) from exc
        if isinstance(args, int):
            return args
        if args is None:
            raise CLIUsageError("a command is required")
        if args.group == "completion":
            stdout.write(_completion_script(args.shell))
            return 0
        if args.group == "_complete":
            try:
                completion_client = _Client(
                    resolve_workspace(args.workspace, environ=environment), timeout=1
                )
                items = _dynamic_completion(completion_client, list(args.words))
            except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                items = _local_dataset_completion(
                    args.workspace,
                    list(args.words),
                    environ=environment,
                ) or _static_completion(list(args.words))
            stdout.write("".join(f"{item}\n" for item in items))
            return 0
        if args.group == "plugin":
            result = execute_plugin_command(args)
            output.result(result, record_type=f"plugin.{args.action}.result")
            return 0
        _validate_cli_args(args, output_format=output_format)
        workspace = resolve_workspace(args.workspace, environ=environment)
        if args.group == "data":
            result = execute_data_command(workspace, args, stdout=stdout)
            if isinstance(result, ExportOutcome):
                if result.path != "-":
                    partial = " (partial coverage allowed)" if result.partial_allowed else ""
                    stderr.write(
                        f"Exported {result.rows:,} rows to {result.path} "
                        f"from publication {result.publication_id}{partial}\n"
                    )
                    stderr.flush()
                return 0
            output.result(result, record_type=f"data.{args.action}.result")
            return 0
        client = _Client(workspace)
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
        failed = isinstance(result, dict) and result.get("status") in {"failed", "canceled"}
        return 1 if failed and _waited_for_task(args) else 0
    except TaskDetached as exc:
        assert output is not None
        output.detached(exc.handle)
        return 130
    except UserCancelled:
        renderer = output or fallback_output()
        renderer.finish_progress()
        renderer.stderr.write("Reset canceled.\n")
        renderer.stderr.flush()
        return 1
    except KeyboardInterrupt:
        renderer = output or fallback_output()
        renderer.finish_progress()
        renderer.stderr.write("Interrupted.\n")
        renderer.stderr.flush()
        return 130
    except (CLIUsageError, DataCommandUsageError) as exc:
        renderer = output or fallback_output()
        renderer.finish_progress()
        renderer.error(str(exc))
        return 2
    except (OSError, ValueError, RuntimeError) as exc:
        renderer = output or fallback_output()
        renderer.finish_progress()
        renderer.error(_error_message(exc))
        return 1


def _error_message(exc: BaseException) -> str:
    """Render an operational failure without transport noise for human readers."""
    if isinstance(exc, ServerError):
        detail = exc.detail
        try:
            parsed = json.loads(detail)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
            detail = parsed["error"]
        if exc.status == 401:
            return (
                "authentication failed; the workspace token does not match the "
                "running server — restart findata-server for this workspace"
            )
        if 400 <= exc.status < 500:
            return detail
        suffix = f": {detail}" if detail.strip() else ""
        return f"server returned {exc.status}{suffix}"
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", exc)
        return f"cannot reach the server ({reason})"
    if isinstance(exc, TimeoutError):
        return "the server did not respond in time"
    return str(exc)


def _waited_for_task(args: Any) -> bool:
    """Whether the command blocked on the task; only waits map a failed task to exit 1."""
    if args.group == "dataset" and args.action in {"update", "complete", "refresh"}:
        return bool(args.wait or args.follow)
    if args.group != "task":
        return False
    if args.action == "watch":
        return True
    if args.action in {"run", "retry"}:
        return bool(args.wait or args.follow)
    if args.action == "logs":
        return bool(args.follow)
    return False


def _page_output(text: str, *, color: bool, stdout: TextIO) -> None:
    with redirect_stdout(stdout):
        click.echo_via_pager(text, color=color)


def _execute(
    client: _Client,
    args: Any,
    *,
    output: CLIOutput,
    stdin: TextIO = sys.stdin,
) -> object:
    if args.group == "config" and args.action == "set":
        literal = args.value is not None or args.value_json is not None
        if literal and args.key in _declared_secret_keys(client):
            raise CLIUsageError("secret configuration must use --stdin or --env")
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
            return client.request("GET", "/v1/datasets/status")
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
                f"Reset dataset {args.dataset!r}? Committed data will be deleted; "
                "settings and task history are preserved. [y/N] "
            )
            output.stderr.flush()
            if stdin.readline().strip().lower() not in {"y", "yes"}:
                raise UserCancelled
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
        output.accepted(submitted)
        return _wait_for_task(
            client, str(submitted["handle_id"]), output=output, follow=args.follow
        )
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
        return _wait_for_task(client, str(args.handle), output=output, follow=True)
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
    if args.group == "system" and args.action == "health":
        return client.request("GET", "/v1/system/health")
    raise ValueError("unsupported command")


def execute_plugin_command(args: Any) -> dict[str, object]:
    """Handle ``plugin`` subcommands that work without a running server."""
    if args.action == "ls":
        return _plugin_ls()
    if args.action == "check":
        return _plugin_check(str(args.name))
    if args.action == "blocked":
        return _plugin_blocked(args)
    if args.action == "scaffold":
        return _plugin_scaffold(args)
    if args.action == "block":
        return _plugin_block(args)
    if args.action == "unblock":
        return _plugin_unblock(args)
    raise ValueError(f"unsupported plugin action: {args.action}")


def _plugin_ls() -> dict[str, object]:
    """List installed plugin distributions that expose findata entry points."""
    from importlib.metadata import distributions

    groups = ("findata.providers", "findata.datasets")
    items: list[dict[str, object]] = []
    for dist in distributions():
        eps = dist.entry_points
        found = {group for group in groups if any(ep.group == group for ep in eps)}
        if not found:
            continue
        ep_names = {
            ep.name for group in found for ep in eps if ep.group == group
        }
        items.append(
            {
                "name": dist.metadata["Name"],
                "version": dist.version,
                "entry_points": sorted(ep_names),
                "groups": sorted(found),
            }
        )
    items.sort(key=lambda item: str(item["name"]))
    return {"items": items, "total": len(items)}


def _plugin_check(name: str) -> dict[str, object]:
    """Try to load an entry point and report success or failure.

    Accepts both short entry-point names (``demo``) and full plugin names
    (``findata-test/demo``).
    """
    from importlib.metadata import entry_points

    groups = {"findata.providers": "findata.providers", "findata.datasets": "findata.datasets"}

    # Phase 1 — match by entry-point name or value suffix (fast path).
    for group_key, group_name in groups.items():
        for ep in entry_points(group=group_key):
            if ep.name == name or ep.value.endswith(f":{name}"):
                return _try_load(ep, group_name)
            # Also match the part after the last dot in the module path,
            # so "demo" matches "...providers.demo:provider_plugin"
            module_part = ep.module.rpartition(".")[-1]
            if module_part == name:
                return _try_load(ep, group_name)

    # Phase 2 — match by full plugin name (<namespace>/<name>).
    # Load each entry point and check the returned plugin's provider_id or name.
    for group_key, group_name in groups.items():
        for ep in entry_points(group=group_key):
            result = _try_load(ep, group_name)
            if result.get("loaded"):
                full_name = result.get("full_name", "")
                if full_name == name:
                    return result

    return {
        "name": name,
        "loaded": False,
        "error_type": "NotFound",
        "error_message": f"No entry point or plugin named {name!r} found in installed packages",
    }


def _try_load(ep: Any, group_name: str) -> dict[str, object]:
    """Load one entry point and return a check result."""
    try:
        loaded = ep.load()
        value = loaded() if callable(loaded) else loaded
        full_name = ""
        type_name = type(value).__name__
        if hasattr(value, "provider_id"):  # ProviderPlugin
            full_name = value.provider_id
        elif hasattr(value, "name"):  # DatasetPlugin
            full_name = value.name
        return {
            "name": ep.name,
            "group": group_name,
            "module": ep.module,
            "full_name": full_name,
            "loaded": True,
            "type": type_name,
        }
    except Exception as exc:
        return {
            "name": ep.name,
            "group": group_name,
            "module": ep.module,
            "loaded": False,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }


def _plugin_blocked(args: Any) -> dict[str, object]:
    """Show the workspace plugin blocklist."""
    from findata.plugins import plugin_blocklist as read_blocklist
    from findata.storage import Workspace

    try:
        environ = os.environ if not hasattr(args, "_environ") else args._environ
        ws_path = resolve_workspace(getattr(args, "workspace", None), environ=environ)
        blocked = read_blocklist(Workspace(ws_path))
        return {"blocked": blocked, "workspace": str(ws_path)}
    except RuntimeError as exc:
        return {"blocked": [], "error": str(exc), "workspace": None}


def _plugin_scaffold(args: Any) -> dict[str, object]:
    """Generate a plugin family directory tree."""
    from findata.scaffold import ScaffoldError, scaffold_plugin

    namespace = str(args.namespace)
    name = str(args.name)
    try:
        root = scaffold_plugin(namespace, name)
        ns_pkg = namespace.replace("-", "_")
        local_pkg = name.replace("-", "_")
        return {
            "namespace": namespace,
            "name": name,
            "path": str(root),
            "succeeded": True,
            "next_steps": (
                f"cd {namespace}/\n"
                f"  pip install -e ./provider -e ./datasets/{name}\n"
                f"  findata-server init ~/my-workspace\n"
                f"  findata-server start ~/my-workspace\n"
                f"  findata plugin check {name}\n"
                f"\nEdit {namespace}/datasets/{name}/src/{ns_pkg}/plugins/datasets/{local_pkg}/operations.py"
                f" to add your data logic."
            ),
        }
    except ScaffoldError as exc:
        return {
            "namespace": namespace,
            "name": name,
            "path": None,
            "succeeded": False,
            "error": str(exc),
        }


def _plugin_block(args: Any) -> dict[str, object]:
    """Add a plugin to the workspace blocklist."""
    return _modify_blocklist(args, add=True)


def _plugin_unblock(args: Any) -> dict[str, object]:
    """Remove a plugin from the workspace blocklist."""
    return _modify_blocklist(args, add=False)


def _modify_blocklist(args: Any, *, add: bool) -> dict[str, object]:
    from findata.plugins import plugin_blocklist as read_blocklist
    from findata.storage import Workspace

    try:
        environ = os.environ if not hasattr(args, "_environ") else args._environ
        ws_path = resolve_workspace(getattr(args, "workspace", None), environ=environ)
        ws = Workspace(ws_path)
        current = list(read_blocklist(ws))
        name = str(args.name)
        if add:
            if name not in current:
                current.append(name)
                ws.set_config("plugins.blocked", current)
            action = "blocked"
        else:
            if name in current:
                current = [item for item in current if item != name]
                ws.set_config("plugins.blocked", current)
            action = "unblocked"
        return {"name": name, "action": action, "blocked": current, "workspace": str(ws_path)}
    except RuntimeError as exc:
        return {"name": str(getattr(args, "name", "")), "error": str(exc), "workspace": None}


def _declared_secret_keys(client: _Client) -> set[str]:
    """Secret configuration keys declared by registered providers; empty when unavailable."""
    try:
        items = client.request("GET", "/v1/providers").get("items", [])
    except (OSError, RuntimeError, ValueError):
        return set()
    keys: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        fields = item.get("secret_fields")
        for field in fields if isinstance(fields, list) else []:
            keys.add(f"provider.{item.get('name')}.{field}")
    return keys


class ServerError(RuntimeError):
    """An HTTP error response from the findata server."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"server returned {status}: {detail}")


# The API is always localhost; environment proxies must never see the bearer token.
_LOCAL_OPENER = build_opener(ProxyHandler({}))


class _Client:
    def __init__(self, workspace: Path, *, timeout: float = 30) -> None:
        try:
            server = json.loads((workspace / "server.json").read_text(encoding="utf-8"))
            self.token = (workspace / "token").read_text(encoding="utf-8").strip()
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"no running server for workspace {workspace}") from exc
        self.base_url = f"http://{server['host']}:{server['port']}"
        self.timeout = timeout

    def request(self, method: str, path: str, body: object | None = None) -> dict[str, object]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with _LOCAL_OPENER.open(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ServerError(exc.code, detail) from exc
        if not isinstance(result, dict):
            raise RuntimeError("server returned a non-object response")
        return result

    def optional_config(self, key: str) -> object | None:
        try:
            return self.request("GET", f"/v1/config?{urlencode({'key': key})}").get("value")
        except ServerError as exc:
            if exc.status == 404:
                return None
            raise


def _render_task_log(output: CLIOutput, item: object, *, handle_id: str) -> None:
    if isinstance(item, Mapping) and item.get("type") == "task.diagnostic":
        diagnostic = dict(item)
        diagnostic.setdefault("handle_id", handle_id)
        output.diagnostic(diagnostic)
    elif isinstance(item, Mapping) and item.get("type") == "log":
        output.log_record(item)
    else:
        output.log(str(item))


def _render_nonfollowing_task_logs(
    args: Any,
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
    except OSError as exc:
        raise RuntimeError(
            f"lost contact with the server while waiting for task {handle}; "
            f"it may still be running — inspect with: findata task status {handle}"
        ) from exc


def _dataset_operands(args: Any) -> dict[str, object]:
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


def _option_value_candidates(words: list[str]) -> list[str] | None:
    """Complete an option's value; None when the previous word is not a value-taking option."""
    if len(words) < 2:
        return None
    tree = command_tree(version=__version__)
    command: click.Command | None = tree.commands.get(words[0])
    if isinstance(command, click.Group) and len(words) >= 3:
        command = command.commands.get(words[1])
    if command is None or isinstance(command, click.Group):
        return None
    previous = words[-2]
    for parameter in command.params:
        if not isinstance(parameter, click.Option):
            continue
        if previous not in [*parameter.opts, *parameter.secondary_opts]:
            continue
        if parameter.is_flag:
            return None
        prefix = words[-1]
        if isinstance(parameter.type, click.Choice):
            return [choice for choice in parameter.type.choices if choice.startswith(prefix)]
        return []
    return None


def _completion_items(response: Mapping[str, object], key: str) -> list[str]:
    items = response.get("items", [])
    if not isinstance(items, list):
        return []
    return [str(item[key]) for item in items if isinstance(item, Mapping) and key in item]


def _dynamic_completion(client: _Client, words: list[str]) -> list[str]:
    value_candidates = _option_value_candidates(words)
    if value_candidates is not None:
        return value_candidates
    if words and words[-1].startswith("-"):
        return _static_completion(words)

    def dataset_names() -> list[str]:
        return _completion_items(client.request("GET", "/v1/datasets"), "name")

    def operation_names(dataset: str) -> list[str]:
        return _completion_items(
            client.request("GET", f"/v1/datasets/{dataset}/operations"), "name"
        )

    if not words:
        tree = command_tree(version=__version__)
        return [name for name, command in tree.commands.items() if not command.hidden]

    first, rest = words[0], words[1:]

    if first == "dataset" and rest:
        name_actions = {
            "update",
            "complete",
            "refresh",
            "describe",
            "operations",
            "operation",
            "status",
            "reset",
        }
        action = rest[0]
        tail = rest[1:]
        if action in name_actions:
            if not tail:
                return dataset_names()
            names = dataset_names()
            if tail[0] not in names:
                return (
                    [name for name in names if name.startswith(tail[0])] if len(tail) == 1 else []
                )
            prefix = tail[1] if len(tail) == 2 else ""
            if action in {"update", "complete", "refresh"} and len(tail) in {1, 2}:
                description = client.request("GET", f"/v1/datasets/{tail[0]}/operations/{action}")
                properties = description.get("properties", {})
                candidates = (
                    [f"--{name}" for name in properties] if isinstance(properties, Mapping) else []
                )
                if isinstance(properties, Mapping) and "timerange" in properties:
                    candidates.extend(["--from", "--to"])
                candidates.extend(["--wait", "--follow", "--dry-run"])
                return [item for item in candidates if item.startswith(prefix)]
            if action == "operation" and len(tail) in {1, 2}:
                return [name for name in operation_names(tail[0]) if name.startswith(prefix)]
            return []
        return []

    if first == "task" and rest:
        if rest[0] == "run":
            tail = rest[1:]
            if not tail:
                return dataset_names()
            names = dataset_names()
            if tail[0] not in names:
                return (
                    [name for name in names if name.startswith(tail[0])] if len(tail) == 1 else []
                )
            prefix = tail[1] if len(tail) == 2 else ""
            return [name for name in operation_names(tail[0]) if name.startswith(prefix)]
        if rest[0] in {"status", "logs", "cancel", "watch", "retry", "explain"} and len(rest) in {
            1,
            2,
        }:
            prefix = rest[1] if len(rest) == 2 else ""
            handles = _completion_items(client.request("GET", "/v1/tasks?all=true"), "handle_id")
            return [handle for handle in handles if handle.startswith(prefix)]

    if first == "provider" and len(rest) in {1, 2} and rest[0] in {"status", "check"}:
        prefix = rest[1] if len(rest) == 2 else ""
        names = _completion_items(client.request("GET", "/v1/providers"), "name")
        return [name for name in names if name.startswith(prefix)]

    if (
        first == "data"
        and len(rest) in {1, 2}
        and rest[0]
        in {
            "schema",
            "preview",
            "coverage",
            "export",
            "snapshot",
        }
    ):
        prefix = rest[1] if len(rest) == 2 else ""
        return [name for name in dataset_names() if name.startswith(prefix)]

    if first == "config" and len(rest) in {1, 2} and rest[0] in {"set", "get", "unset"}:
        prefix = rest[1] if len(rest) == 2 else ""
        keys = _config_key_candidates(client)
        return [key for key in keys if key.startswith(prefix)]

    if (
        first == "cron"
        and len(rest) in {1, 2}
        and rest[0]
        in {
            "enable",
            "disable",
            "reset",
            "set",
        }
    ):
        prefix = rest[1] if len(rest) == 2 else ""
        return [name for name in dataset_names() if name.startswith(prefix)]

    if first == "events" and len(rest) in {1, 2} and rest[0] == "ack":
        prefix = rest[1] if len(rest) == 2 else ""
        events = _completion_items(client.request("GET", "/v1/events?unread=true"), "event_id")
        return [event_id for event_id in events if event_id.startswith(prefix)]

    return _static_completion(words)


def _config_key_candidates(client: _Client) -> list[str]:
    """Merge declared configuration keys with currently-set keys for completion."""
    keys: set[str] = set()
    try:
        declared = client.request("GET", "/v1/config/keys").get("items", [])
        if isinstance(declared, list):
            for entry in declared:
                if isinstance(entry, Mapping) and entry.get("key"):
                    keys.add(str(entry["key"]))
    except ServerError:
        # Older servers lack /v1/config/keys; fall back to set keys only.
        pass
    values = client.request("GET", "/v1/config").get("values", {})
    if isinstance(values, Mapping):
        keys.update(str(key) for key in values)
    return sorted(keys)


def _static_completion(words: list[str]) -> list[str]:
    value_candidates = _option_value_candidates(words)
    if value_candidates is not None:
        return value_candidates
    tree = command_tree(version=__version__)
    groups = [name for name, command in tree.commands.items() if not command.hidden]
    actions = {
        name: list(command.commands)
        for name, command in tree.commands.items()
        if isinstance(command, click.Group)
    }
    if len(words) == 1 and words[0].startswith("-"):
        return _option_candidates(tree, words[0])
    if len(words) >= 3:
        family = tree.commands.get(words[0])
        command = family.commands.get(words[1]) if isinstance(family, click.Group) else None
        prefix = words[-1]
        if command is not None and (not prefix or prefix.startswith("-")):
            return _option_candidates(command, prefix)
    if not words:
        candidates, prefix = groups, ""
    elif len(words) == 1 and words[0] in actions:
        candidates, prefix = actions[words[0]], ""
    elif len(words) == 1:
        candidates, prefix = groups, words[0]
    elif len(words) == 2:
        family_actions = actions.get(words[0], [])
        if words[1] in family_actions:
            candidates, prefix = [], ""
        else:
            candidates, prefix = family_actions, words[1]
    else:
        candidates, prefix = [], words[-1]
    return [item for item in candidates if item.startswith(prefix)]


def _option_candidates(command: click.Command, prefix: str) -> list[str]:
    candidates = [
        option
        for parameter in command.params
        if isinstance(parameter, click.Option)
        for option in [*parameter.opts, *parameter.secondary_opts]
    ]
    return sorted(option for option in candidates if option.startswith(prefix))


def _local_dataset_completion(
    explicit_workspace: Path | None,
    words: list[str],
    *,
    environ: Mapping[str, str],
) -> list[str]:
    """Complete dataset names from workspace storage when no server is available."""
    if not (words and len(words) in {2, 3}):
        return []
    dataset_positions = (
        words[0] == "data" and words[1] in {"schema", "preview", "coverage", "export", "snapshot"}
    ) or (words[0] == "task" and words[1] == "run")
    if not dataset_positions:
        return []
    try:
        workspace = resolve_workspace(explicit_workspace, environ=dict(environ))
    except RuntimeError:
        return []
    datasets = workspace / "datasets"
    candidates = sorted(
        str(item.parent.relative_to(datasets))
        for item in datasets.rglob(DATABASE_NAME)
        if item.is_file()
    )
    prefix = words[2] if len(words) == 3 else ""
    return [item for item in candidates if item.startswith(prefix)]


def _params(
    values: list[str], source: str | None = None, *, stdin: TextIO = sys.stdin
) -> dict[str, object]:
    if source is not None:
        if values:
            raise CLIUsageError("--param and --params are mutually exclusive")
        if source == "-":
            text = stdin.read()
        elif source.startswith("@"):
            text = Path(source[1:]).read_text(encoding="utf-8")
        else:
            text = source
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CLIUsageError(f"invalid operands JSON: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise CLIUsageError("--params JSON must be an object")
        return parsed
    result: dict[str, object] = {}
    for value in values:
        if "=" not in value:
            raise CLIUsageError(f"invalid --param {value!r}")
        key, item = value.split("=", 1)
        if not key:
            raise CLIUsageError("parameter name cannot be empty")
        if key in result:
            existing = result[key]
            result[key] = [*existing, item] if isinstance(existing, list) else [existing, item]
        else:
            result[key] = item
    return result


def _extract_option(arguments: list[str], name: str) -> str | None:
    """Remove every occurrence of a --name value / --name=value option; last one wins."""
    value: str | None = None
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == name:
            if index + 1 >= len(arguments):
                raise CLIUsageError(f"{name} requires a value")
            value = arguments[index + 1]
            del arguments[index : index + 2]
        elif token.startswith(f"{name}="):
            value = token.split("=", 1)[1]
            del arguments[index]
        else:
            index += 1
    return value


def _extract_format(arguments: list[str]) -> str:
    value = _extract_option(arguments, "--format")
    if value is None:
        return "human"
    if value not in {"human", "json", "jsonl"}:
        raise CLIUsageError(f"unsupported format {value!r}")
    return value


def _extract_color(arguments: list[str]) -> str:
    value = _extract_option(arguments, "--color")
    if value is None:
        return "auto"
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


def _duration_seconds(value: str) -> float:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if len(value) < 2 or value[-1] not in units:
        raise CLIUsageError("duration must end in s, m, h, or d")
    try:
        amount = float(value[:-1])
    except ValueError as exc:
        raise CLIUsageError(f"invalid duration {value!r}") from exc
    if amount < 0:
        raise CLIUsageError("duration cannot be negative")
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
            '${(f)"$(findata _complete "${(@)words[2,-1]}" 2>/dev/null)"}; }\n'
            "compdef _findata_complete findata\n"
        )
    return "complete -c findata -f -a '(findata _complete (commandline -opc)[2..-1] 2>/dev/null)'\n"


def _validate_cli_args(args: Any, *, output_format: str = "human") -> None:
    if args.group == "events" and args.action == "ack":
        if bool(args.event_id) == bool(args.all):
            raise CLIUsageError("events ack requires an event ID or --all")
    if args.group == "dataset" and args.action == "status":
        if bool(args.dataset) == bool(args.all):
            raise CLIUsageError("dataset status requires a dataset or --all")
    if args.group == "task" and args.action == "run" and args.param and args.params:
        raise CLIUsageError("--param and --params are mutually exclusive")
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
            raise CLIUsageError("config set requires exactly one value source")


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
        raise CLIUsageError(f"invalid configuration JSON: {exc.msg}") from exc


def _result_record_type(args: Any) -> str:
    if args.group == "task" and args.action in {"run", "logs"}:
        return "task.result"
    return f"{args.group}.{getattr(args, 'action', 'result')}.result"


if __name__ == "__main__":
    raise SystemExit(main())
