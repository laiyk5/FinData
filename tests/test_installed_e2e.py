from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from findata import DataLoader

CLI_TIMEOUT = 120


class InstalledQuickStartTests(unittest.TestCase):
    def test_console_scripts_run_mocked_failure_and_resume_story(self) -> None:
        scripts = Path(sys.executable).parent
        findata = scripts / "findata"
        server_command = scripts / "findata-server"
        self.assertTrue(findata.is_file(), f"installed script missing: {findata}")
        self.assertTrue(server_command.is_file(), f"installed script missing: {server_command}")

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            initialized = subprocess.run(
                [server_command, "init", workspace],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            process = subprocess.Popen(
                [server_command, "start", workspace, "--port", "0"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 10
                while not (workspace / "server.json").exists():
                    if process.poll() is not None:
                        assert process.stderr is not None
                        self.fail(process.stderr.read())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.02)

                def cli(
                    *arguments: str, input_text: str | None = None
                ) -> subprocess.CompletedProcess[str]:
                    return subprocess.run(
                        [findata, "--workspace", workspace, *arguments],
                        input=input_text,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=CLI_TIMEOUT,
                    )

                configured = cli(
                    "config",
                    "set",
                    "provider.tushare.token",
                    "--stdin",
                    input_text="findata-mock:fail=daily_basic@2\n",
                )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                provider = cli("--format", "json", "provider", "check", "tushare")
                self.assertEqual(provider.returncode, 0, provider.stderr)
                self.assertEqual(json.loads(provider.stdout)["mode"], "mock")
                metadata = cli(
                    "task",
                    "run",
                    "findata/tushare/index_basic",
                    "complete",
                    "--param",
                    "indexes=tushare:000300.SH",
                    "--wait",
                )
                self.assertEqual(metadata.returncode, 0, metadata.stderr)
                setting = cli(
                    "config",
                    "set",
                    "dataset.findata/tushare/daily_basic.update_symbols",
                    "--value-json",
                    '["tushare:000300.SH@latest"]',
                )
                self.assertEqual(setting.returncode, 0, setting.stderr)

                task_arguments = (
                    "--format",
                    "json",
                    "task",
                    "run",
                    "findata/tushare/daily_basic",
                    "complete",
                    "--param",
                    "symbols=tushare:000300.SH",
                    "--param",
                    "timerange=2026-06-29:2026-07-04",
                    "--wait",
                )
                failed = cli(*task_arguments)
                self.assertEqual(failed.returncode, 1, failed.stderr)
                self.assertEqual(json.loads(failed.stdout)["status"], "failed")
                self.assertEqual(
                    DataLoader(workspace)
                    .dataset("findata/tushare/daily_basic")
                    .coverage()
                    .column("key")
                    .to_pylist(),
                    ["000001.SZ"],
                )

                configured = cli(
                    "config",
                    "set",
                    "provider.tushare.token",
                    "--stdin",
                    input_text="findata-mock\n",
                )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                resumed = cli(*task_arguments)
                self.assertEqual(resumed.returncode, 0, resumed.stderr)
                self.assertEqual(json.loads(resumed.stdout)["result"]["fetched_requests"], 2)
                table = (
                    DataLoader(workspace)
                    .dataset("findata/tushare/daily_basic")
                    .query(
                        keys=["000001.SZ", "600000.SH", "600519.SH"],
                        time_range=("2026-06-29", "2026-07-04"),
                        require_coverage=True,
                    )
                )
                self.assertEqual(table.num_rows, 15)
                cron = cli("cron", "enable", "findata/tushare/daily_basic")
                self.assertEqual(cron.returncode, 0, cron.stderr)
                self.assertIn("Enabled", cron.stdout)
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    process.wait(timeout=10)
                if process.stdout is not None:
                    readiness = process.stdout.read()
                    self.assertIn("FinData server ready", readiness)
                    process.stdout.close()
                if process.stderr is not None:
                    errors = process.stderr.read()
                    self.assertEqual(errors, "")
                    process.stderr.close()


if __name__ == "__main__":
    unittest.main()
