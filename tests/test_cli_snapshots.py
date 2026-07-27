"""Golden-output tests for human CLI rendering, paired with semantic assertions.

Snapshots live in tests/snapshots/ and store normalized output: identifiers,
timestamps, and durations are replaced with stable placeholders so only the
meaningful layout and labels are compared. Set FINDATA_UPDATE_SNAPSHOTS=1 to
accept a deliberate rendering change.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

from findata.cli.main import main as cli_main
from findata.server.server import FindataServer, initialize_workspace

SNAPSHOTS = Path(__file__).parent / "snapshots"

NORMALIZERS = [
    (re.compile(r"\b[0-9a-f]{32}\b"), "<ID>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}"), "<TIMESTAMP>"),
    (re.compile(r"\d+ min(?: \d+ s)?"), "<DURATION>"),
    (re.compile(r"\d+(?:\.\d+)? (?:ms|s)\b"), "<DURATION>"),
]


def normalize(rendered: str) -> str:
    for pattern, replacement in NORMALIZERS:
        rendered = pattern.sub(replacement, rendered)
    return rendered


class HumanRenderingSnapshotTests(unittest.TestCase):
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

    def run_cli(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli_main(
            ["--workspace", str(self.root), *arguments],
            stdout=stdout,
            stderr=stderr,
            environ={},
        )
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_snapshot(self, name: str, rendered: str) -> None:
        path = SNAPSHOTS / name
        normalized = normalize(rendered)
        SNAPSHOTS.mkdir(exist_ok=True)
        if os.environ.get("FINDATA_UPDATE_SNAPSHOTS") == "1":
            path.write_text(normalized, encoding="utf-8")
        if not path.exists():
            path.write_text(normalized, encoding="utf-8")
            self.fail(f"created snapshot {name}; review it and rerun the test")
        expected = path.read_text(encoding="utf-8")
        self.assertEqual(
            normalized,
            expected,
            f"snapshot {name} changed; set FINDATA_UPDATE_SNAPSHOTS=1 to accept the new "
            "rendering after review",
        )

    def test_dataset_listing_table(self) -> None:
        code, output, errors = self.run_cli("dataset", "ls")
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")

        # Semantic assertions: a cosmetically accepted snapshot must still
        # contain the registered datasets and a table header.
        self.assertIn("NAME", output)
        for dataset in (
            "findata-plugins/tushare_trade_cal",
            "findata-plugins/tushare_stock_basic",
            "findata-plugins/tushare_index_basic",
            "findata-plugins/tushare_index_weight",
            "findata-plugins/tushare_daily_basic",
        ):
            self.assertIn(dataset, output)
        self.assert_snapshot("dataset_ls.txt", output)

    def test_provider_listing_table(self) -> None:
        code, output, errors = self.run_cli("provider", "ls")
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")

        self.assertIn("findata-plugins/tushare", output)
        self.assertIn("mock", output)
        self.assert_snapshot("provider_ls.txt", output)

    def test_task_terminal_summary(self) -> None:
        code, output, errors = self.run_cli(
            "task",
            "run",
            "findata-plugins/tushare_trade_cal",
            "complete",
            "--params",
            '{"exchanges":["SSE"],"timerange":"2026-07-17:2026-07-20"}',
            "--wait",
        )
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", errors)

        self.assertIn("Task succeeded", output)
        self.assertIn("findata-plugins/tushare_trade_cal", output)
        self.assertIn("complete", output)
        self.assert_snapshot("task_terminal_summary.txt", output)


if __name__ == "__main__":
    unittest.main()
