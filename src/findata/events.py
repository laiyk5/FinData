from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SEVERITIES = {"info", "warning", "error"}


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_id: str
    timestamp: float
    kind: str
    severity: str
    message: str
    context: dict[str, Any]
    acknowledged: bool = False


class EventStore:
    """A durable append-only event stream with reference acknowledgements."""

    def __init__(self, workspace: Path) -> None:
        root = Path(workspace)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "events.jsonl"
        self.lock_path = root / "events.lock"
        self.lock_path.touch(mode=0o600, exist_ok=True)

    def record(
        self,
        kind: str,
        severity: str,
        message: str,
        *,
        timestamp: float | None = None,
        **context: Any,
    ) -> EventRecord:
        if severity not in SEVERITIES:
            raise ValueError(f"invalid event severity {severity!r}")
        record = {
            "event_id": uuid.uuid4().hex,
            "timestamp": time.time() if timestamp is None else float(timestamp),
            "kind": str(kind),
            "severity": severity,
            "message": str(message),
            "context": context,
        }
        self._append(record)
        return EventRecord(**record)

    def ack(self, event_id: str, *, timestamp: float | None = None) -> None:
        known = {item.event_id for item in self.list_events()}
        if event_id not in known:
            raise KeyError(event_id)
        self._append(
            {
                "event_id": uuid.uuid4().hex,
                "timestamp": time.time() if timestamp is None else float(timestamp),
                "kind": "acknowledgement",
                "severity": "info",
                "message": "event acknowledged",
                "context": {},
                "reference": event_id,
            }
        )

    def ack_all(self, *, timestamp: float | None = None) -> int:
        unread = self.list_events(unread=True)
        for event in unread:
            self.ack(event.event_id, timestamp=timestamp)
        return len(unread)

    def list_events(
        self,
        *,
        unread: bool = False,
        since: float | None = None,
        severity: str | None = None,
    ) -> list[EventRecord]:
        if severity is not None and severity not in SEVERITIES:
            raise ValueError(f"invalid event severity {severity!r}")
        records = self._read()
        acknowledged = {
            str(item["reference"])
            for item in records
            if item.get("kind") == "acknowledgement" and item.get("reference")
        }
        result: list[EventRecord] = []
        for item in records:
            if item.get("kind") == "acknowledgement":
                continue
            is_acknowledged = str(item.get("event_id")) in acknowledged
            if unread and is_acknowledged:
                continue
            if since is not None and float(item.get("timestamp", 0)) < since:
                continue
            if severity is not None and item.get("severity") != severity:
                continue
            result.append(
                EventRecord(
                    event_id=str(item["event_id"]),
                    timestamp=float(item["timestamp"]),
                    kind=str(item["kind"]),
                    severity=str(item["severity"]),
                    message=str(item["message"]),
                    context=dict(item.get("context") or {}),
                    acknowledged=is_acknowledged,
                )
            )
        return sorted(result, key=lambda item: item.timestamp, reverse=True)

    def _append(self, record: dict[str, Any]) -> None:
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            lines = self.path.read_text(encoding="utf-8").splitlines()
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        result = []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                result.append(value)
        return result
