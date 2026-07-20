from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
import shutil
import time
from typing import TextIO


ANSI_RESET = "\x1b[0m"
ANSI_BOLD = "\x1b[1m"
ANSI_GREEN = "\x1b[32m"
ANSI_YELLOW = "\x1b[33m"
ANSI_RED = "\x1b[31m"


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
    ) -> None:
        self.output_format = output_format
        self.stdout = stdout
        self.stderr = stderr
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
        self._spinner_index = 0
        self._progress_active = False

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
                json.dumps({"type": "error", "error": message}, separators=(",", ":"))
                + "\n"
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
        elif self.output_format == "human":
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
            self.output_format == "human" and self.err_terminal.interactive
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
            detail = str(stage or status or "working").replace(":", " ")
            if reason:
                detail += f" — {reason}"
            if isinstance(progress, Mapping) and progress.get("total") is not None:
                detail += f" [{progress.get('current', 0)}/{progress['total']}]"
            if self.err_terminal.interactive:
                if self._accepted_at is not None and time.monotonic() - self._accepted_at < 0.25:
                    return
                frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if self.err_terminal.unicode else "-|/\\"
                marker = frames[self._spinner_index % len(frames)]
                self._spinner_index += 1
                self.stderr.write(f"\r\x1b[2K{marker} {detail}")
                self._progress_active = True
            else:
                marker = "..."
                self.stderr.write(f"{marker} {detail}\n")
            self.stderr.flush()

    def finish_progress(self) -> None:
        if self._progress_active:
            self.stderr.write("\r\x1b[2K")
            self.stderr.flush()
            self._progress_active = False

    def log(self, message: str) -> None:
        if self.output_format == "jsonl":
            self._jsonl({"message": message}, "task.log")
        elif self.output_format == "human":
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
        return f"{_display(value)}\n"

    def _details(self, value: Mapping[object, object]) -> str:
        lines: list[str] = []
        for key, item in value.items():
            label = str(key).replace("_", " ").title()
            padded = label.ljust(16)
            lines.append(
                f"{self._style(padded, ANSI_BOLD, terminal=self.out_terminal)}  {_display(item)}"
            )
        return "\n".join(lines) + "\n"

    def _table(self, items: Sequence[object]) -> str:
        rows = [item for item in items if isinstance(item, Mapping)]
        if not rows:
            return "No results found.\n"
        preferred = [
            "name", "dataset", "operation", "status", "ready", "enabled", "provider",
            "handle_id", "next_run", "severity", "kind", "message",
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
        while len(columns) > 1 and 8 * len(columns) + 2 * (len(columns) - 1) > self.out_terminal.width:
            columns.pop()
        if not columns:
            return "\n".join(_display(row) for row in rows) + "\n"
        rendered = [[_display(row.get(column)) for column in columns] for row in rows]
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
            "  ".join(_truncate(cell, widths[index]).ljust(widths[index]) for index, cell in enumerate(row))
            for row in rendered
        ]
        return "\n".join([header, *body]) + "\n"

    def _task_summary(self, value: Mapping[object, object]) -> str:
        status = str(value.get("status", "unknown"))
        succeeded = status == "succeeded"
        marker = ("✓" if succeeded else "✗") if self.out_terminal.unicode else ("OK" if succeeded else "ERROR")
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
        if value.get("error"):
            fields.append(("Reason", value["error"]))
        created = value.get("created_at")
        updated = value.get("updated_at")
        if isinstance(created, (int, float)) and isinstance(updated, (int, float)):
            fields.append(("Elapsed", f"{max(0.0, updated - created):.1f}s"))
        lines.extend(f"  {label:<12} {_display(item)}" for label, item in fields if item is not None)
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


def _display(value: object) -> str:
    if value is None:
        return "—"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return str(value)


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
    return None
