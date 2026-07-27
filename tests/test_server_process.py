from __future__ import annotations

import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

from findata.server import FindataServer, initialize_workspace
from findata.server_cli import main as server_cli_main


class ServerProcessTests(unittest.TestCase):
    def test_second_start_reports_a_clean_error_without_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            initialize_workspace(workspace)
            server = FindataServer(workspace, port=0, provider_mode="mock", today=date(2026, 7, 20))
            server.start_background()
            try:
                stdout, stderr = io.StringIO(), io.StringIO()
                code = server_cli_main(["start", str(workspace)], stdout=stdout, stderr=stderr)
                self.assertEqual(code, 1)
                self.assertIn("already has a running server", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())
            finally:
                server.shutdown()

    def test_sigterm_performs_clean_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            initialize_workspace(workspace)
            environment = dict(os.environ)
            source = str(Path(__file__).parents[1] / "src")
            environment["PYTHONPATH"] = source
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "findata.server_cli",
                    "start",
                    str(workspace),
                    "--port",
                    "0",
                    "--provider-mode",
                    "mock",
                ],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not (workspace / "server.json").exists():
                    if process.poll() is not None:
                        self.fail(process.stderr.read())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.02)
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertFalse((workspace / "server.json").exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
                if process.stderr is not None:
                    process.stderr.close()

    def test_foreground_server_reports_readiness_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            initialize_workspace(workspace)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "findata.server_cli",
                    "start",
                    str(workspace),
                    "--port",
                    "0",
                    "--provider-mode",
                    "mock",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not (workspace / "server.json").exists():
                    if process.poll() is not None:
                        assert process.stderr is not None
                        self.fail(process.stderr.read())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.02)
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=5), 0)
                assert process.stdout is not None
                output = process.stdout.read()
                self.assertIn("FinData server ready", output)
                self.assertIn(str(workspace), output)
                self.assertIn("providers=findata-plugins/tushare:mock", output)
                self.assertNotIn((workspace / "token").read_text().strip(), output)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()


if __name__ == "__main__":
    unittest.main()
