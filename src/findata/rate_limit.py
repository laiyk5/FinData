from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path


class FileRateLimiter:
    """A fixed-window permit ledger shared safely by local task processes."""

    def __init__(self, path: Path, *, limit: int, period: float) -> None:
        if limit <= 0 or period <= 0:
            raise ValueError("rate limit and period must be positive")
        self.path = Path(path)
        self.limit = int(limit)
        self.period = float(period)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.lock_path.touch(mode=0o600, exist_ok=True)

    def try_acquire(self, *, now: float | None = None) -> bool:
        current = time.time() if now is None else float(now)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            timestamps = self._read()
            cutoff = current - self.period
            timestamps = [value for value in timestamps if value > cutoff]
            granted = len(timestamps) < self.limit
            if granted:
                timestamps.append(current)
            self._write(timestamps)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return granted

    def acquire(
        self,
        *,
        checkpoint: Callable[[], None] | None = None,
        waiting: Callable[[str], None] | None = None,
    ) -> None:
        announced = False
        while not self.try_acquire():
            if waiting is not None and not announced:
                waiting("provider_rate_limit")
                announced = True
            if checkpoint is not None:
                checkpoint()
            time.sleep(min(0.1, self.period / 10))

    def _read(self) -> list[float]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [float(item) for item in value.get("timestamps", [])] if isinstance(value, dict) else []

    def _write(self, timestamps: list[float]) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".rate-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"timestamps": timestamps}, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
