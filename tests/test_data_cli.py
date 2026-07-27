from __future__ import annotations

from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest

import duckdb
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.ipc as ipc
import pyarrow.parquet as pq

from findata.cli import main as cli_main
from findata.plugins import register_plugins
from findata.storage import Workspace
from findata_plugins.plugins.datasets.tushare_daily_basic import daily_basic_plugin
from findata_plugins.plugins.datasets.tushare_daily_basic.operations import DailyBasicDatasetService
from findata_plugins.plugins.datasets.tushare_index_basic import index_basic_plugin
from findata_plugins.plugins.datasets.tushare_index_weight import index_weight_plugin
from findata_plugins.plugins.providers.tushare.provider import tushare_provider_plugin
from findata_plugins.shared.engine import TushareClient
from findata_plugins.shared.testing import MockTushareTransport
from findata_plugins.plugins.datasets.tushare_stock_basic import stock_basic_plugin
from findata_plugins.plugins.datasets.tushare_trade_cal import trade_cal_plugin


def register_v1_datasets(workspace: Workspace) -> None:
    register_plugins(
        workspace,
        [
            trade_cal_plugin(),
            stock_basic_plugin(),
            index_basic_plugin(),
            index_weight_plugin(),
            daily_basic_plugin(),
        ],
        providers=[tushare_provider_plugin()],
    )


class DataCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        workspace = Workspace.init(self.root)
        register_v1_datasets(workspace)
        transport = MockTushareTransport(today=date(2026, 7, 20))
        service = DailyBasicDatasetService(
            workspace,
            TushareClient(token="test-token", transport=transport),
            today=date(2026, 7, 20),
        )
        service.run(
            "complete",
            {
                "symbols": ["600000.SH"],
                "timerange": "2026-07-13:2026-07-18",
            },
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_preview_leads_with_key_columns_and_honors_column_order(self) -> None:
        stdout = io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "data",
                "preview",
                "findata-plugins/tushare_daily_basic",
                "--keys",
                "600000.SH",
                "--limit",
                "2",
            ],
            stdout=stdout,
            stderr=io.StringIO(),
            environ={},
        )
        self.assertEqual(code, 0)
        header = stdout.getvalue().splitlines()[0].split()
        self.assertEqual(header[:2], ["TS_CODE", "TRADE_DATE"])

        stdout = io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "data",
                "preview",
                "findata-plugins/tushare_daily_basic",
                "--keys",
                "600000.SH",
                "--columns",
                "close,ts_code",
                "--limit",
                "2",
            ],
            stdout=stdout,
            stderr=io.StringIO(),
            environ={},
        )
        self.assertEqual(code, 0)
        header = stdout.getvalue().splitlines()[0].split()
        self.assertEqual(header[:2], ["CLOSE", "TS_CODE"])

    def test_data_usage_errors_exit_2(self) -> None:
        stderr = io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "data",
                "preview",
                "findata-plugins/tushare_daily_basic",
                "--require-coverage",
                "--allow-partial",
            ],
            stdout=io.StringIO(),
            stderr=stderr,
            environ={},
        )
        self.assertEqual(code, 2)
        self.assertIn("mutually exclusive", stderr.getvalue())

        stderr = io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "data",
                "coverage",
                "findata-plugins/tushare_daily_basic",
                "--from",
                "2026-07-01",
            ],
            stdout=io.StringIO(),
            stderr=stderr,
            environ={},
        )
        self.assertEqual(code, 2)
        self.assertIn("supplied together", stderr.getvalue())

    def test_parquet_stdout_export_refuses_an_interactive_terminal(self) -> None:
        class TTYStdout(io.StringIO):
            def isatty(self) -> bool:
                return True

        stderr = io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "data",
                "export",
                "findata-plugins/tushare_daily_basic",
                "--output-format",
                "parquet",
                "--output",
                "-",
            ],
            stdout=TTYStdout(),
            stderr=stderr,
            environ={},
        )
        self.assertEqual(code, 2)
        self.assertIn("binary data", stderr.getvalue())

    def test_partial_export_summary_reports_the_policy(self) -> None:
        target = self.root / "partial.csv"
        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "data",
                "export",
                "findata-plugins/tushare_daily_basic",
                "--keys",
                "600000.SH",
                "--from",
                "2026-07-10",
                "--to",
                "2026-07-18",
                "--allow-partial",
                "--output-format",
                "csv",
                "--output",
                str(target),
            ],
            stdout=stdout,
            stderr=stderr,
            environ={},
        )
        self.assertEqual(code, 0)
        self.assertIn("partial coverage allowed", stderr.getvalue())

    def test_snapshot_writes_default_and_explicit_destinations(self) -> None:
        default = self.run_json("data", "snapshot", "findata-plugins/tushare_daily_basic")
        default_path = self.root / "snapshots" / "findata-plugins/tushare_daily_basic.duckdb"
        # The CLI resolves the workspace, so compare resolved paths.
        self.assertEqual(Path(default["path"]), default_path.resolve())
        self.assertTrue(default_path.is_file())
        with duckdb.connect(str(default_path), read_only=True) as connection:
            rows = connection.execute("select count(*) from data").fetchone()
        self.assertGreater(rows[0], 0)

        explicit = self.root / "copies" / "daily.duckdb"
        result = self.run_json(
            "data", "snapshot", "findata-plugins/tushare_daily_basic", "--output", str(explicit)
        )
        self.assertEqual(result["path"], str(explicit))
        self.assertTrue(explicit.is_file())
        # A repeated snapshot replaces the previous copy atomically.
        second = self.run_json("data", "snapshot", "findata-plugins/tushare_daily_basic")
        self.assertEqual(Path(second["path"]), default_path.resolve())

    def run_json(self, *arguments: str) -> dict[str, object]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = cli_main(
            ["--workspace", str(self.root), "--format", "json", *arguments],
            stdout=stdout,
            stderr=stderr,
            environ={},
        )
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        return json.loads(stdout.getvalue())

    def test_schema_preview_and_coverage_need_no_running_server(self) -> None:
        schema = self.run_json("data", "schema", "findata-plugins/tushare_daily_basic")
        self.assertEqual(schema["partition_key"], "ts_code")
        self.assertEqual(schema["time_field"], "trade_date")
        self.assertIn("trade_date", [field["name"] for field in schema["fields"]])

        preview = self.run_json(
            "data",
            "preview",
            "findata-plugins/tushare_daily_basic",
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

        coverage = self.run_json(
            "data", "coverage", "findata-plugins/tushare_daily_basic", "--keys", "600000.SH"
        )
        self.assertEqual(coverage["items"][0]["start"], "2026-07-13")
        self.assertEqual(coverage["items"][0]["end"], "2026-07-18")

        checked = self.run_json(
            "data",
            "coverage",
            "findata-plugins/tushare_daily_basic",
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
                "findata-plugins/tushare_daily_basic",
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
        self.assertIn("2026-07-10:2026-07-13", human.getvalue())
        self.assertIn("MISSING", human.getvalue())

        (self.root / "datasets" / "STALE_DIRECTORY").mkdir()
        completion = io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "_complete",
                "data",
                "preview",
                "findata-plugins/tushare_daily_",
            ],
            stdout=completion,
            stderr=io.StringIO(),
            environ={},
        )
        self.assertEqual(code, 0)
        self.assertEqual(completion.getvalue(), "findata-plugins/tushare_daily_basic\n")

        completion = io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "_complete",
                "data",
                "coverage",
            ],
            stdout=completion,
            stderr=io.StringIO(),
            environ={},
        )
        self.assertEqual(code, 0)
        self.assertIn("findata-plugins/tushare_daily_basic", completion.getvalue().splitlines())
        self.assertNotIn("STALE_DIRECTORY", completion.getvalue().splitlines())

        completion = io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "_complete",
                "task",
                "run",
                "findata-plugins/tushare_daily_",
            ],
            stdout=completion,
            stderr=io.StringIO(),
            environ={},
        )
        self.assertEqual(code, 0)
        self.assertEqual(completion.getvalue(), "findata-plugins/tushare_daily_basic\n")

    def test_unknown_dataset_reads_never_create_directories(self) -> None:
        datasets = self.root / "datasets"
        before = {path.name for path in datasets.iterdir()}
        commands = [
            ["data", "schema", "nobody/not_exist"],
            ["data", "coverage", "nobody/not_exist"],
            ["data", "preview", "nobody/not_exist"],
            [
                "data",
                "export",
                "nobody/not_exist",
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
                self.assertIn("unknown dataset 'nobody/not_exist'", stderr.getvalue())
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
                        "findata-plugins/tushare_daily_basic",
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
                "findata-plugins/tushare_daily_basic",
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
                "findata-plugins/tushare_daily_basic",
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
        self.assertIn(
            "findata dataset complete findata-plugins/tushare_daily_basic", stderr.getvalue()
        )

        partial = self.run_json(
            "data",
            "preview",
            "findata-plugins/tushare_daily_basic",
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
                "findata-plugins/tushare_daily_basic",
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
                "findata-plugins/tushare_daily_basic",
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
