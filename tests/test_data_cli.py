from __future__ import annotations

from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.ipc as ipc
import pyarrow.parquet as pq

from findata.cli import main as cli_main
from findata.datasets.tushare.operations import DatasetService, register_v1_datasets
from findata.providers.tushare import TushareClient
from findata.storage import Workspace
from findata.testing.tushare import MockTushareTransport


class DataCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        workspace = Workspace.init(self.root)
        register_v1_datasets(workspace)
        transport = MockTushareTransport(today=date(2026, 7, 20))
        service = DatasetService(
            workspace,
            TushareClient(token="test-token", transport=transport),
            today=date(2026, 7, 20),
        )
        service.run(
            "tushare_daily_basic",
            "complete",
            {
                "symbols": ["600000.SH"],
                "timerange": "2026-07-13:2026-07-18",
            },
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_json(self, *arguments: str) -> dict[str, object]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = cli_main(
            ["--workspace", str(self.root), "--json", *arguments],
            stdout=stdout,
            stderr=stderr,
            environ={},
        )
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        return json.loads(stdout.getvalue())

    def test_schema_preview_and_coverage_need_no_running_server(self) -> None:
        schema = self.run_json("data", "schema", "tushare_daily_basic")
        self.assertEqual(schema["partition_key"], "ts_code")
        self.assertEqual(schema["time_field"], "trade_date")
        self.assertIn("trade_date", [field["name"] for field in schema["fields"]])

        preview = self.run_json(
            "data",
            "preview",
            "tushare_daily_basic",
            "--keys",
            "600000.SH",
            "--from",
            "2026-07-13",
            "--to",
            "2026-07-18",
            "--columns",
            "ts_code,trade_date,close",
        )
        self.assertEqual(len(preview["items"]), 5)
        self.assertEqual(set(preview["items"][0]), {"ts_code", "trade_date", "close"})

        coverage = self.run_json("data", "coverage", "tushare_daily_basic", "--keys", "600000.SH")
        self.assertEqual(coverage["items"][0]["start"], "2026-07-13")
        self.assertEqual(coverage["items"][0]["end"], "2026-07-18")

        checked = self.run_json(
            "data",
            "coverage",
            "tushare_daily_basic",
            "--keys",
            "600000.SH",
            "--from",
            "2026-07-10",
            "--to",
            "2026-07-18",
        )
        self.assertFalse(checked["items"][0]["complete"])
        self.assertEqual(
            checked["items"][0]["missing"],
            [["2026-07-10", "2026-07-13"]],
        )

        human = io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "data",
                "coverage",
                "tushare_daily_basic",
                "--keys",
                "600000.SH",
                "--from",
                "2026-07-10",
                "--to",
                "2026-07-18",
            ],
            stdout=human,
            stderr=io.StringIO(),
            environ={},
        )
        self.assertEqual(code, 0)
        self.assertIn("REQUESTED_START", human.getvalue())
        self.assertIn("2026-07-13", human.getvalue())
        self.assertIn("MISSING", human.getvalue())

        completion = io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "_complete",
                "data",
                "preview",
                "tushare_daily_",
            ],
            stdout=completion,
            stderr=io.StringIO(),
            environ={},
        )
        self.assertEqual(code, 0)
        self.assertEqual(completion.getvalue(), "tushare_daily_basic\n")

    def test_unknown_dataset_reads_never_create_directories(self) -> None:
        datasets = self.root / "datasets"
        before = {path.name for path in datasets.iterdir()}
        commands = [
            ["data", "schema", "NOT_EXIST_DATASET"],
            ["data", "coverage", "NOT_EXIST_DATASET"],
            ["data", "preview", "NOT_EXIST_DATASET"],
            [
                "data",
                "export",
                "NOT_EXIST_DATASET",
                "--output-format",
                "csv",
                "--output",
                str(self.root / "unexpected.csv"),
            ],
        ]
        for command in commands:
            with self.subTest(command=command[1]):
                stderr = io.StringIO()
                code = cli_main(
                    ["--workspace", str(self.root), *command],
                    stdout=io.StringIO(),
                    stderr=stderr,
                    environ={},
                )
                self.assertEqual(code, 1)
                self.assertIn("unknown dataset 'NOT_EXIST_DATASET'", stderr.getvalue())
                self.assertEqual({path.name for path in datasets.iterdir()}, before)
                self.assertFalse((self.root / "unexpected.csv").exists())

    def test_export_streams_every_supported_file_format(self) -> None:
        readers = {
            "csv": lambda path: pacsv.read_csv(path),
            "parquet": pq.read_table,
            "arrow": lambda path: ipc.open_file(path).read_all(),
            "jsonl": _read_jsonl,
        }
        for output_format, read in readers.items():
            with self.subTest(output_format=output_format):
                target = self.root / f"export.{output_format}"
                stdout = io.StringIO()
                stderr = io.StringIO()
                code = cli_main(
                    [
                        "--workspace",
                        str(self.root),
                        "data",
                        "export",
                        "tushare_daily_basic",
                        "--keys",
                        "600000.SH",
                        "--from",
                        "2026-07-13",
                        "--to",
                        "2026-07-18",
                        "--columns",
                        "ts_code,trade_date,close",
                        "--output-format",
                        output_format,
                        "--output",
                        str(target),
                        "--batch-size",
                        "2",
                    ],
                    stdout=stdout,
                    stderr=stderr,
                    environ={},
                )
                self.assertEqual(code, 0, stderr.getvalue())
                self.assertEqual(stdout.getvalue(), "")
                table = read(target)
                self.assertIsInstance(table, pa.Table)
                self.assertEqual(table.num_rows, 5)
                self.assertIn("Exported 5 rows", stderr.getvalue())

    def test_export_refuses_overwrite_without_force(self) -> None:
        target = self.root / "existing.csv"
        target.write_text("keep me", encoding="utf-8")
        stderr = io.StringIO()

        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "data",
                "export",
                "tushare_daily_basic",
                "--output-format",
                "csv",
                "--output",
                str(target),
            ],
            stdout=io.StringIO(),
            stderr=stderr,
            environ={},
        )

        self.assertEqual(code, 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep me")
        self.assertIn("already exists", stderr.getvalue())

    def test_strict_coverage_error_is_actionable_and_allow_partial_is_explicit(self) -> None:
        stderr = io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "data",
                "preview",
                "tushare_daily_basic",
                "--keys",
                "600000.SH",
                "--from",
                "2026-07-10",
                "--to",
                "2026-07-18",
            ],
            stdout=io.StringIO(),
            stderr=stderr,
            environ={},
        )
        self.assertEqual(code, 1)
        self.assertIn("2026-07-10", stderr.getvalue())
        self.assertIn("findata dataset complete tushare_daily_basic", stderr.getvalue())

        partial = self.run_json(
            "data",
            "preview",
            "tushare_daily_basic",
            "--keys",
            "600000.SH",
            "--from",
            "2026-07-10",
            "--to",
            "2026-07-18",
            "--allow-partial",
        )
        self.assertTrue(partial["partial_allowed"])
        self.assertEqual(len(partial["items"]), 5)

    def test_csv_stdout_is_data_only_and_failed_export_cleans_temporary_file(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "data",
                "export",
                "tushare_daily_basic",
                "--keys",
                "600000.SH",
                "--from",
                "2026-07-13",
                "--to",
                "2026-07-18",
                "--columns",
                "ts_code,trade_date,close",
                "--output-format",
                "csv",
                "--output",
                "-",
                "--batch-size",
                "2",
            ],
            stdout=stdout,
            stderr=stderr,
            environ={},
        )
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertTrue(stdout.getvalue().startswith('"ts_code","trade_date","close"'))
        self.assertNotIn("Exported", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

        target = self.root / "failed.parquet"
        error = io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "data",
                "export",
                "tushare_daily_basic",
                "--columns",
                "unknown",
                "--output-format",
                "parquet",
                "--output",
                str(target),
            ],
            stdout=io.StringIO(),
            stderr=error,
            environ={},
        )
        self.assertEqual(code, 1)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob(".failed.parquet.*.tmp")), [])


def _read_jsonl(path: Path) -> pa.Table:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return pa.Table.from_pylist(rows)


if __name__ == "__main__":
    unittest.main()
