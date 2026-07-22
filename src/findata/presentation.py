from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
import shutil
import time
from typing import TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn


ANSI_RESET = "\x1b[0m"
ANSI_BOLD = "\x1b[1m"
ANSI_GREEN = "\x1b[32m"
ANSI_YELLOW = "\x1b[33m"
ANSI_RED = "\x1b[31m"

TIMESTAMP_FIELDS = {
    "created_at",
    "updated_at",
    "timestamp",
    "last_run",
    "next_run",
    "last_evaluation_time",
}
DURATION_FIELDS = {"duration_seconds", "elapsed_seconds"}
PERCENTAGE_FIELDS = {"coverage_percent": 2, "progress_percent": 1}
COUNT_FIELDS = {
    "acknowledged",
    "current",
    "error_count",
    "fetched_requests",
    "running_tasks",
    "subscriber_count",
    "tasks",
    "total",
    "warning_count",
}


@dataclass(frozen=True, slots=True)
class TerminalCapabilities:
    interactive: bool
    color: bool
    unicode: bool
    width: int

    @classmethod
    def detect(
        cls,
        stream: TextIO,
        *,
        color_mode: str,
        environ: Mapping[str, str] | None = None,
    ) -> TerminalCapabilities:
        environment = os.environ if environ is None else environ
        dumb = environment.get("TERM", "") == "dumb"
        interactive = bool(getattr(stream, "isatty", lambda: False)()) and not dumb
        if color_mode == "always":
            color = True
        elif color_mode == "never":
            color = False
        else:
            color = interactive and not dumb and "NO_COLOR" not in environment
        encoding = getattr(stream, "encoding", None) or "utf-8"
        try:
            "✓✗⠋".encode(encoding)
        except (LookupError, UnicodeEncodeError):
            unicode = False
        else:
            unicode = not dumb
        width = shutil.get_terminal_size(fallback=(100, 24)).columns if interactive else 100
        return cls(interactive=interactive, color=color, unicode=unicode, width=max(width, 40))


class CLIOutput:
    def __init__(
        self,
        *,
        output_format: str,
        color_mode: str,
        stdout: TextIO,
        stderr: TextIO,
        environ: Mapping[str, str] | None = None,
        display_timezone: str = "UTC",
        quiet: bool = False,
        verbose: bool = False,
        progress_enabled: bool = True,
    ) -> None:
        self.output_format = output_format
        self.stdout = stdout
        self.stderr = stderr
        self.quiet = quiet
        self.verbose = verbose
        self.progress_enabled = progress_enabled
        try:
            self.display_timezone = ZoneInfo(display_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown display timezone {display_timezone!r}") from exc
        self.out_terminal = TerminalCapabilities.detect(
            stdout, color_mode=color_mode, environ=environ
        )
        self.err_terminal = TerminalCapabilities.detect(
            stderr, color_mode=color_mode, environ=environ
        )
        if output_format != "human":
            self.out_terminal = _without_decoration(self.out_terminal)
            self.err_terminal = _without_decoration(self.err_terminal)
        self._last_state: tuple[object, ...] | None = None
        self._accepted_at: float | None = None
        self._progress: Progress | None = None
        self._progress_task_id: int | None = None
        self._progress_failed = False
        self._diagnostic_status_active = False
        self._diagnostic_visible: set[str] = set()
        self._diagnostic_visible_totals = {"warning": 0, "error": 0}
        self._diagnostic_totals = {"warning": 0, "error": 0}
        self._suppression_noted = False

    def result(self, value: object, *, record_type: str = "result") -> None:
        if self.output_format == "json":
            self.stdout.write(json.dumps(value, separators=(",", ":"), default=str) + "\n")
        elif self.output_format == "jsonl":
            self._jsonl(value, record_type)
        else:
            self.stdout.write(self._human(value))
        self.stdout.flush()

    def error(self, message: str) -> None:
        if self.output_format in {"json", "jsonl"}:
            self.stderr.write(
                json.dumps({"type": "error", "error": message}, separators=(",", ":")) + "\n"
            )
        else:
            marker = "✗" if self.err_terminal.unicode else "ERROR"
            heading = self._style(f"{marker} Error", ANSI_RED, terminal=self.err_terminal)
            self.stderr.write(f"{heading}: {message}\n")
            suggestion = _error_suggestion(message)
            if suggestion:
                self.stderr.write(f"  {suggestion}\n")
        self.stderr.flush()

    def accepted(self, task: Mapping[str, object]) -> None:
        self._accepted_at = time.monotonic()
        handle = str(task.get("handle_id", ""))
        if self.output_format == "jsonl":
            self._jsonl(dict(task), "task.accepted")
        elif self.output_format == "human" and not self.quiet:
            marker = "✓" if self.err_terminal.unicode else "OK"
            label = self._style(f"{marker} Accepted", ANSI_GREEN, terminal=self.err_terminal)
            self.stderr.write(f"{label} task {handle}\n")
            self.stderr.flush()

    def state(self, task: Mapping[str, object]) -> None:
        status = task.get("status")
        reason = task.get("reason")
        stage = task.get("stage")
        progress = task.get("progress")
        key = (status, reason, stage, json.dumps(progress, sort_keys=True, default=str))
        if key == self._last_state and not (
            self.output_format == "human"
            and self.err_terminal.interactive
            and self.progress_enabled
        ):
            return
        self._last_state = key
        if self.output_format == "jsonl":
            event = {
                "handle_id": task.get("handle_id"),
                "status": status,
                "reason": reason,
                "stage": stage,
                "progress": progress,
            }
            self._jsonl(event, "task.progress")
        elif self.output_format == "human":
            if self.quiet:
                return
            now = time.monotonic()
            if self._accepted_at is None:
                self._accepted_at = now
            detail = str(stage or status or "working").replace(":", " ")
            if reason:
                detail += f" — {reason}"
            if isinstance(progress, Mapping) and progress.get("total") is not None:
                detail += f" [{progress.get('current', 0)}/{progress['total']}]"
                for key, label in (
                    ("provider_requests", "requests"),
                    ("rows_fetched", "rows"),
                    ("checkpoints", "checkpoints"),
                ):
                    value = progress.get(key)
                    if isinstance(value, (int, float)):
                        unit = label[:-1] if value == 1 else label
                        detail += f" · {_format_count(value)} {unit}"
                elapsed = max(0.0, now - self._accepted_at)
                detail += f" · {_format_duration(elapsed)} elapsed"
                current = progress.get("current")
                total = progress.get("total")
                if (
                    isinstance(current, (int, float))
                    and isinstance(total, (int, float))
                    and 0 < current < total
                ):
                    eta = elapsed * (total - current) / current
                    detail += f" · ETA {_format_duration(eta)}"
            if self.err_terminal.interactive and self.progress_enabled:
                if now - self._accepted_at < 0.25:
                    return
                current = progress.get("current", 0) if isinstance(progress, Mapping) else 0
                total = progress.get("total") if isinstance(progress, Mapping) else None
                self._update_progress(detail, current=current, total=total)
            else:
                marker = "..."
                self.stderr.write(f"{marker} {detail}\n")
            self.stderr.flush()

    def finish_progress(self) -> None:
        if self._progress is not None:
            progress = self._progress
            self._progress = None
            self._progress_task_id = None
            try:
                progress.stop()
            except Exception:
                self._progress_failed = True

    def _update_progress(self, detail: str, *, current: object, total: object) -> None:
        completed = _progress_number(current, fallback=0)
        maximum = _progress_number(total, fallback=None)
        if self._progress_failed:
            self.stderr.write(f"... {detail}\n")
            self.stderr.flush()
            return
        try:
            if self._progress is None:
                console = Console(
                    file=self.stderr,
                    force_terminal=True,
                    force_interactive=True,
                    color_system="auto" if self.err_terminal.color else None,
                    no_color=not self.err_terminal.color,
                    width=self.err_terminal.width,
                )
                leading = SpinnerColumn() if self.err_terminal.unicode else TextColumn("...")
                columns = [leading, TextColumn("{task.description}", markup=False)]
                if self.err_terminal.unicode:
                    columns.append(BarColumn(bar_width=None))
                columns.append(MofNCompleteColumn())
                self._progress = Progress(
                    *columns,
                    console=console,
                    transient=True,
                    auto_refresh=False,
                    redirect_stdout=False,
                    redirect_stderr=False,
                )
                self._progress.start()
                self._progress_task_id = self._progress.add_task(
                    detail,
                    total=maximum,
                    completed=completed,
                )
            else:
                assert self._progress_task_id is not None
                self._progress.update(
                    self._progress_task_id,
                    description=detail,
                    total=maximum,
                    completed=completed,
                )
            self._progress.refresh()
        except Exception:
            self.finish_progress()
            self._progress_failed = True
            self.stderr.write(f"... {detail}\n")
            self.stderr.flush()

    def diagnostic(self, diagnostic: Mapping[str, object]) -> None:
        severity = str(diagnostic.get("severity") or "warning")
        if severity not in self._diagnostic_totals:
            raise ValueError(f"invalid task diagnostic severity {severity!r}")
        try:
            count = int(diagnostic.get("count", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("task diagnostic count must be a positive integer") from exc
        if count < 1:
            raise ValueError("task diagnostic count must be a positive integer")

        record = dict(diagnostic)
        record["severity"] = severity
        record["count"] = count
        self._diagnostic_totals[severity] += count
        if self.output_format == "jsonl":
            self._jsonl(record, "task.diagnostic")
            return
        if self.output_format != "human":
            return

        identity = json.dumps(
            {
                "severity": severity,
                "code": record.get("code"),
                "message": record.get("message"),
                "context": record.get("context"),
            },
            sort_keys=True,
            default=str,
        )
        if identity in self._diagnostic_visible:
            return
        if len(self._diagnostic_visible) < 10:
            self.finish_progress()
            self._clear_diagnostic_status()
            self._diagnostic_visible.add(identity)
            self._diagnostic_visible_totals[severity] += count
            marker = "Warning" if severity == "warning" else "Error"
            code = f" [{record['code']}]" if record.get("code") else ""
            suffix = f" (x{count})" if count > 1 else ""
            self.stderr.write(f"{marker}{code}: {record.get('message', '')}{suffix}\n")
            self.stderr.flush()
            return

        if self.err_terminal.interactive:
            warnings = self._suppressed_occurrences("warning")
            errors = self._suppressed_occurrences("error")
            self.stderr.write(
                f"\r\x1b[2K{warnings} additional warnings, {errors} additional errors"
            )
            self.stderr.flush()
            self._diagnostic_status_active = True
        elif not self._suppression_noted:
            self.stderr.write("additional diagnostics suppressed from the live view.\n")
            self.stderr.flush()
            self._suppression_noted = True

    def finish_diagnostics(self, handle_id: str | None = None) -> None:
        if not any(self._diagnostic_totals.values()) or self.output_format != "human":
            return
        self._clear_diagnostic_status()
        warning_count = self._diagnostic_totals["warning"]
        error_count = self._diagnostic_totals["error"]
        self.stderr.write(f"Diagnostics: {warning_count} warnings, {error_count} errors.\n")
        if handle_id:
            self.stderr.write(f"  Inspect: findata task logs {handle_id}\n")
        self.stderr.flush()

    def _suppressed_occurrences(self, severity: str) -> int:
        return max(
            0,
            self._diagnostic_totals[severity] - self._diagnostic_visible_totals[severity],
        )

    def _clear_diagnostic_status(self) -> None:
        if self._diagnostic_status_active:
            self.stderr.write("\r\x1b[2K")
            self._diagnostic_status_active = False

    def set_display_timezone(self, name: str) -> None:
        try:
            self.display_timezone = ZoneInfo(name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown display timezone {name!r}") from exc

    def log(self, message: str) -> None:
        if self.output_format == "jsonl":
            self._jsonl({"message": message}, "task.log")
        elif self.output_format == "human":
            if self.quiet:
                return
            # Follow logs and Rich progress use separate Python streams but share
            # one physical terminal. Close the live region before writing a
            # persistent line so Rich can erase it from the correct cursor row.
            self.finish_progress()
            self.stdout.write(f"{message}\n")
            self.stdout.flush()

    def detached(self, handle: str) -> None:
        self.finish_progress()
        if self.output_format in {"json", "jsonl"}:
            self.stderr.write(
                json.dumps(
                    {"type": "task.detached", "handle_id": handle, "running": True},
                    separators=(",", ":"),
                )
                + "\n"
            )
        else:
            marker = "!" if not self.err_terminal.unicode else "⚠"
            label = self._style(f"{marker} Detached", ANSI_YELLOW, terminal=self.err_terminal)
            self.stderr.write(
                f"{label}; task {handle} is still running.\n"
                f"  Inspect: findata task status {handle}\n"
                f"  Cancel:  findata task cancel {handle}\n"
            )
        self.stderr.flush()

    def _human(self, value: object) -> str:
        if isinstance(value, Mapping) and isinstance(value.get("items"), list):
            return self._table(value["items"])
        if isinstance(value, Mapping) and "status" in value and "handle_id" in value:
            return self._task_summary(value)
        if isinstance(value, Mapping):
            return self._details(value)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return self._table(value)
        return f"{_display(value, timezone=self.display_timezone)}\n"

    def _details(self, value: Mapping[object, object]) -> str:
        lines: list[str] = []
        for key, item in value.items():
            label = str(key).replace("_", " ").title()
            padded = label.ljust(16)
            lines.append(
                f"{self._style(padded, ANSI_BOLD, terminal=self.out_terminal)}  "
                f"{_display(item, field=str(key), timezone=self.display_timezone)}"
            )
        return "\n".join(lines) + "\n"

    def _table(self, items: Sequence[object]) -> str:
        rows = [item for item in items if isinstance(item, Mapping)]
        if not rows:
            return "No results found.\n"
        preferred = [
            "name",
            "dataset",
            "operation",
            "status",
            "ready",
            "enabled",
            "provider",
            "handle_id",
            "next_run",
            "severity",
            "kind",
            "message",
        ]
        scalar_keys = {
            str(key)
            for row in rows
            for key, item in row.items()
            if item is None or isinstance(item, (str, int, float, bool))
        }
        columns = [key for key in preferred if key in scalar_keys]
        columns.extend(sorted(scalar_keys - set(columns)))
        columns = columns[:7]
        while (
            len(columns) > 1 and 8 * len(columns) + 2 * (len(columns) - 1) > self.out_terminal.width
        ):
            columns.pop()
        if not columns:
            return "\n".join(_display(row, timezone=self.display_timezone) for row in rows) + "\n"
        rendered = [
            [
                _display(row.get(column), field=column, timezone=self.display_timezone)
                for column in columns
            ]
            for row in rows
        ]
        widths = [
            min(max(len(column.upper()), *(len(row[index]) for row in rendered)), 36)
            for index, column in enumerate(columns)
        ]
        while sum(widths) + 2 * (len(widths) - 1) > self.out_terminal.width:
            widest = max(range(len(widths)), key=widths.__getitem__)
            if widths[widest] <= 8:
                break
            widths[widest] -= 1
        header = "  ".join(
            self._style(
                _truncate(column.upper(), widths[index]).ljust(widths[index]),
                ANSI_BOLD,
                terminal=self.out_terminal,
            )
            for index, column in enumerate(columns)
        )
        body = [
            "  ".join(
                _truncate(cell, widths[index]).ljust(widths[index])
                for index, cell in enumerate(row)
            )
            for row in rendered
        ]
        return "\n".join([header, *body]) + "\n"

    def _task_summary(self, value: Mapping[object, object]) -> str:
        status = str(value.get("status", "unknown"))
        succeeded = status == "succeeded"
        marker = (
            ("✓" if succeeded else "✗")
            if self.out_terminal.unicode
            else ("OK" if succeeded else "ERROR")
        )
        color = ANSI_GREEN if succeeded else ANSI_RED
        lines = [self._style(f"{marker} Task {status}", color, terminal=self.out_terminal)]
        fields: list[tuple[str, object]] = [
            ("Dataset", value.get("dataset")),
            ("Operation", value.get("operation")),
            ("Task", value.get("handle_id")),
        ]
        result = value.get("result")
        if isinstance(result, Mapping):
            fields.extend(
                [
                    ("Publication", result.get("publication_id")),
                    ("Requests", result.get("fetched_requests")),
                ]
            )
        diagnostic_counts = value.get("diagnostic_counts")
        if isinstance(diagnostic_counts, Mapping):
            warning_count = diagnostic_counts.get("warning", 0)
            error_count = diagnostic_counts.get("error", 0)
            if warning_count or error_count:
                fields.append(("Diagnostics", f"{warning_count} warnings, {error_count} errors"))
        if value.get("error"):
            fields.append(("Reason", value["error"]))
        created = value.get("created_at")
        updated = value.get("updated_at")
        if isinstance(created, (int, float)) and isinstance(updated, (int, float)):
            fields.append(("Elapsed", _format_duration(max(0.0, updated - created))))
        lines.extend(
            f"  {label:<12} {_display(item, timezone=self.display_timezone)}"
            for label, item in fields
            if item is not None
        )
        return "\n".join(lines) + "\n"

    def _jsonl(self, value: object, record_type: str) -> None:
        if isinstance(value, Mapping):
            record = dict(value)
            record.setdefault("type", record_type)
        else:
            record = {"type": record_type, "value": value}
        self.stdout.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
        self.stdout.flush()

    @staticmethod
    def _style(value: str, code: str, *, terminal: TerminalCapabilities) -> str:
        return f"{code}{value}{ANSI_RESET}" if terminal.color else value


def _without_decoration(value: TerminalCapabilities) -> TerminalCapabilities:
    return TerminalCapabilities(
        interactive=value.interactive,
        color=False,
        unicode=value.unicode,
        width=value.width,
    )


def _progress_number(value: object, *, fallback: int | None) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return value
    return fallback


def _format_count(value: int | float) -> str:
    return f"{value:,.0f}"


def _display(
    value: object,
    *,
    field: str | None = None,
    timezone: ZoneInfo | None = None,
) -> str:
    if value is None:
        return "—"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if field in TIMESTAMP_FIELDS:
        return _format_timestamp(value, timezone or ZoneInfo("UTC"))
    if field in DURATION_FIELDS and isinstance(value, (int, float)):
        return _format_duration(float(value))
    if field in PERCENTAGE_FIELDS and isinstance(value, (int, float)):
        precision = PERCENTAGE_FIELDS[field]
        return f"{float(value):.{precision}f}%"
    if field in COUNT_FIELDS and isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return _format_measurement(value)
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return str(value)


def _format_timestamp(value: object, timezone: ZoneInfo) -> str:
    if isinstance(value, (int, float)):
        instant = datetime.fromtimestamp(float(value), UTC)
    elif isinstance(value, datetime):
        instant = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    elif isinstance(value, str):
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
    else:
        return str(value)
    return instant.astimezone(timezone).isoformat(timespec="seconds")


def _format_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 1:
        return f"{round(seconds * 1000):,} ms"
    if seconds < 60:
        return f"{seconds:.1f}".rstrip("0").rstrip(".") + " s"
    minutes, remainder = divmod(round(seconds), 60)
    if remainder:
        return f"{minutes:,} min {remainder} s"
    return f"{minutes:,} min"


def _format_measurement(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 1e9 or (0 < magnitude < 1e-4):
        coefficient, exponent = f"{value:.6e}".split("e")
        coefficient = coefficient.rstrip("0").rstrip(".")
        return f"{coefficient}e{int(exponent):+03d}"
    return f"{value:.12f}".rstrip("0").rstrip(".") or "0"


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: max(1, width - 1)] + "…"


def _error_suggestion(message: str) -> str | None:
    lowered = message.lower()
    if "no running server" in lowered or "connection" in lowered:
        return "Check the server with: findata system status"
    if "task" in lowered:
        return "Inspect recent work with: findata task ls"
    if "unresolved coverage" in lowered:
        dataset = message.split(" has unresolved coverage", 1)[0]
        return (
            f"Inspect with: findata data coverage {dataset}; "
            f"fetch missing ranges with: findata dataset complete {dataset}"
        )
    return None
