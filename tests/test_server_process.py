from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from findata.server import initialize_workspace


class ServerProcessTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
