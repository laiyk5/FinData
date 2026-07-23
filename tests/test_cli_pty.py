"""PTY transcript tests: interactive terminal behavior through a real pseudo-terminal."""

from __future__ import annotations

import io
import os
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

from findata.presentation import CLIOutput
from findata.server import FindataServer, initialize_workspace


def read_available(master: int, deadline: float) -> str:
    """Drain a pty master until the deadline or a short quiet period."""
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.05)
        if not ready:
            if chunks:
                break
            continue
        try:
            chunk = os.read(master, 65536)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


class PtyDiagnosticsTests(unittest.TestCase):
    def test_suppression_line_counts_and_cleanup_on_a_real_terminal(self) -> None:
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "w")
        self.addCleanup(stream.close)
        output = CLIOutput(
            output_format="human",
            color_mode="auto",
            stdout=io.StringIO(),
            stderr=stream,
            environ={},
        )

        for index in range(12):
            output.diagnostic(
                {"severity": "warning", "code": f"W{index}", "message": f"warning {index}"}
            )
        transcript = read_available(master, time.monotonic() + 2)
        for index in range(10):
            self.assertIn(f"warning {index}", transcript)
        self.assertNotIn("warning 10", transcript)
        self.assertIn("2 additional warnings, 0 additional errors", transcript)

        output.finish_diagnostics("handle-1")
        transcript = read_available(master, time.monotonic() + 2)
        self.assertIn("Diagnostics: 12 warnings, 0 errors.", transcript)
        self.assertIn("findata task logs handle-1", transcript)


class PtyTaskFollowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        initialize_workspace(self.root)
        self.server = FindataServer(
            self.root,
            port=0,
            provider_mode="mock",
            today=date(2026, 7, 20),
        )
        self.server.start_background()
        self.addCleanup(self.server.shutdown)

    def spawn_on_pty(self, *arguments: str) -> tuple[int, subprocess.Popen[bytes]]:
        master, slave = pty.openpty()
        environment = {key: value for key, value in os.environ.items() if key != "NO_COLOR"}
        environment["TERM"] = "xterm-256color"
        process = subprocess.Popen(
            [sys.executable, "-m", "findata.cli", "--workspace", str(self.root), *arguments],
            stdout=slave,
            stderr=slave,
            stdin=subprocess.DEVNULL,
            env=environment,
            close_fds=True,
        )
        os.close(slave)
        return master, process

    def run_to_exit(self, master: int, process: subprocess.Popen[bytes]) -> str:
        transcript = ""
        deadline = time.monotonic() + 60
        while process.poll() is None and time.monotonic() < deadline:
            transcript += read_available(master, time.monotonic() + 0.5)
        transcript += read_available(master, time.monotonic() + 1)
        return transcript

    def test_interactive_wait_uses_live_progress_region_on_a_real_terminal(self) -> None:
        master, process = self.spawn_on_pty(
            "task",
            "run",
            "tushare_trade_cal",
            "complete",
            "--params",
            '{"exchanges":["SSE","SZSE"],"timerange":"2015-01-01:2026-07-20"}',
            "--wait",
        )
        self.addCleanup(os.close, master)
        try:
            transcript = self.run_to_exit(master, process)
            self.assertEqual(process.wait(timeout=10), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

        self.assertIn("Accepted", transcript)
        # The Rich live region draws with terminal control sequences and is
        # removed before the persistent terminal summary.
        self.assertIn("\x1b[", transcript)
        self.assertIn("Task succeeded", transcript)
        self.assertIn("tushare_trade_cal", transcript)

    def test_sigint_detaches_from_a_real_terminal(self) -> None:
        master, process = self.spawn_on_pty(
            "task",
            "run",
            "tushare_trade_cal",
            "complete",
            "--params",
            '{"exchanges":["SSE","SZSE"],"timerange":"2015-01-01:2026-07-20"}',
            "--wait",
        )
        self.addCleanup(os.close, master)
        transcript = ""
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and "Accepted" not in transcript:
                transcript += read_available(master, time.monotonic() + 0.5)
                if process.poll() is not None:
                    break
            self.assertIn("Accepted", transcript)
            self.assertIsNone(process.poll())

            process.send_signal(signal.SIGINT)
            deadline = time.monotonic() + 10
            while process.poll() is None and time.monotonic() < deadline:
                transcript += read_available(master, time.monotonic() + 0.5)
            transcript += read_available(master, time.monotonic() + 1)
            self.assertEqual(process.wait(timeout=10), 130)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

        self.assertIn("Detached", transcript)
        self.assertIn("still running", transcript)
        self.assertIn("findata task status", transcript)
        handles = self.server.taskrunner.list_handles()
        self.assertEqual(len(handles), 1)
        self.assertNotEqual(handles[0].status, "canceled")


if __name__ == "__main__":
    unittest.main()
