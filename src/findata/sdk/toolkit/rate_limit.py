from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path


class FileRateLimiter:
    """A continuously refilling token bucket shared by local task processes."""

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
            tokens, updated = self._read(current)
            elapsed = max(0.0, current - updated)
            tokens = min(float(self.limit), tokens + elapsed * self.limit / self.period)
            granted = tokens >= 1.0
            if granted:
                tokens -= 1.0
            self._write(tokens, current)
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

    def _read(self, now: float) -> tuple[float, float]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0.0, now
        if not isinstance(value, dict) or "tokens" not in value or "updated" not in value:
            return 0.0, now
        return float(value["tokens"]), float(value["updated"])

    def _write(self, tokens: float, updated: float) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".rate-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"tokens": tokens, "updated": updated}, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
