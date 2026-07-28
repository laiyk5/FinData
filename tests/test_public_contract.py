from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import click

from findata import __version__
from findata.cli.main import main as cli_main, resolve_workspace
from findata.cli.click_parser import command_tree
from findata.sdk.contracts import OperandError
from findata.server.server_cli import _command_tree as server_command_tree
from findata.server.server_cli import main as server_cli_main
from findata.storage import Workspace
from findata_plugins.tushare.plugins.datasets.stock.daily_basic.operations import DailyBasicDatasetRuntime
from findata_plugins.tushare.plugins.datasets.stock.stock_basic.operations import StockBasicDatasetRuntime
from findata_plugins.tushare.plugins.datasets.stock.trade_cal.operations import TradeCalDatasetRuntime


class ReadProtocolImportTests(unittest.TestCase):
    def test_dataloader_is_the_standalone_read_protocol(self) -> None:
        # External readers install findata only for DataLoader; importing it must
        # not pull in CLI, server, presentation, task, or plugin modules.
        code = (
            "import sys\n"
            "from findata import DataLoader\n"
            "banned = [\n"
            "    module\n"
            "    for module in sys.modules\n"
            "    if module.split('.')[0] in {'click', 'rich'}\n"
            "    or module.startswith((\n"
            "        'findata.cli', 'findata.click_parser', 'findata.server',\n"
            "        'findata.presentation', 'findata.taskrunner', 'findata.cron',\n"
            "        'findata.events', 'findata.plugins', 'findata.data_access',\n"
            "        'findata.datasets', 'findata.providers', 'findata.toolkit',\n"
            "        'findata.testing',\n"
            "    ))\n"
            "]\n"
            "sys.exit(1 if banned else 0)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_package_root_reexports_the_official_reader(self) -> None:
        import findata
        from findata.sdk.loader import DataLoader

        self.assertIs(findata.DataLoader, DataLoader)
        self.assertIn("DataLoader", findata.__all__)


class OperationNormalizationTests(unittest.TestCase):
    def test_resolves_today_once_and_canonicalizes_array_operands(self) -> None:
        values = DailyBasicDatasetRuntime().normalize_operation(
            "complete",
            {
                "symbols": ["600000.SH", "600000.SH", "000001.SZ"],
                "timerange": "2026-07-01:today",
            },
            today=date(2026, 7, 20),
        )
        self.assertEqual(values["symbols"], ["000001.SZ", "600000.SH"])
        self.assertEqual(values["timerange"], "2026-07-01:2026-07-20")

    def test_rejects_unknown_operation_and_fields_before_submission(self) -> None:
        with self.assertRaises(OperandError):
            StockBasicDatasetRuntime().normalize_operation("complete", {}, today=date(2026, 7, 20))
        with self.assertRaises(OperandError):
            TradeCalDatasetRuntime().normalize_operation(
                "complete",
                {"exchanges": ["SSE"], "timerange": "2026-07-01:2026-07-02", "extra": 1},
                today=date(2026, 7, 20),
            )
        with self.assertRaisesRegex(OperandError, "future trade-calendar"):
            TradeCalDatasetRuntime().normalize_operation(
                "complete",
                {"exchanges": ["SSE"], "timerange": "2026-07-20:2026-07-22"},
                today=date(2026, 7, 20),
            )


class WorkspaceResolutionTests(unittest.TestCase):
    def test_every_public_command_and_parameter_is_documented(self) -> None:
        def visit(command: click.Command) -> None:
            if command.hidden:
                return
            self.assertTrue(command.help, command.name)
            for parameter in command.params:
                if isinstance(parameter, click.Option):
                    self.assertTrue(parameter.help, f"{command.name} --{parameter.name}")
                elif isinstance(parameter, click.Argument):
                    self.assertTrue(
                        getattr(parameter, "help", None),
                        f"{command.name} {parameter.name}",
                    )
            if isinstance(command, click.Group):
                for child in command.commands.values():
                    visit(child)

        visit(command_tree(version=__version__))
        visit(server_command_tree())

    def test_nested_help_explains_command_arguments_and_options(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        code = cli_main(
            ["data", "coverage", "--help"],
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        rendered = stdout.getvalue()
        self.assertIn("Inspect committed coverage", rendered)
        self.assertIn("Arguments:", rendered)
        self.assertIn("Registered dataset identifier", rendered)
        self.assertIn("--keys", rendered)
        self.assertIn("partition key", rendered)

        stdout = io.StringIO()
        code = server_cli_main(["start", "--help"], stdout=stdout, stderr=io.StringIO())
        self.assertEqual(code, 0)
        self.assertIn("Run the authenticated local API", stdout.getvalue())
        self.assertIn("Workspace directory", stdout.getvalue())
        self.assertIn("deterministic local", stdout.getvalue())
        self.assertIn("mock responses", stdout.getvalue())

    def test_click_help_is_embeddable_and_lists_global_presentation_options(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        code = cli_main(["--help"], stdout=stdout, stderr=stderr, environ={})

        self.assertEqual(code, 0)
        self.assertIn("--format", stdout.getvalue())
        self.assertIn("--quiet", stdout.getvalue())
        self.assertIn("--no-progress", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_click_nested_help_does_not_require_workspace(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        code = cli_main(
            ["dataset", "complete", "--help"],
            stdout=stdout,
            stderr=stderr,
            environ={},
        )

        self.assertEqual(code, 0)
        self.assertIn("--dry-run", stdout.getvalue())
        self.assertIn("--symbols", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_server_click_help_is_embeddable(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        code = server_cli_main(["start", "--help"], stdout=stdout, stderr=stderr)

        self.assertEqual(code, 0)
        self.assertIn("--provider-mode", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_explicit_then_environment_then_nearest_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            explicit = root / "explicit"
            environment = root / "environment"
            parent = root / "parent"
            for workspace in (explicit, environment, parent):
                Workspace.init(workspace)
            nested = parent / "one" / "two"
            nested.mkdir(parents=True)

            self.assertEqual(
                resolve_workspace(
                    explicit, environ={"FINDATA_WORKSPACE": str(environment)}, cwd=nested
                ),
                explicit.resolve(),
            )
            self.assertEqual(
                resolve_workspace(
                    None, environ={"FINDATA_WORKSPACE": str(environment)}, cwd=nested
                ),
                environment.resolve(),
            )
            self.assertEqual(resolve_workspace(None, environ={}, cwd=nested), parent.resolve())

    def test_missing_workspace_has_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "findata-server init"):
                resolve_workspace(None, environ={}, cwd=Path(directory))

    def test_completion_generation_does_not_require_workspace(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = cli_main(["completion", "bash"], stdout=stdout, stderr=stderr)
        self.assertEqual(code, 0)
        self.assertIn("complete", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

        stdout = io.StringIO()
        code = cli_main(["completion", "zsh"], stdout=stdout, stderr=io.StringIO())
        self.assertEqual(code, 0)
        self.assertIn("compdef", stdout.getvalue())

    def test_dynamic_completion_falls_back_without_workspace_or_server(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        code = cli_main(["_complete", "d"], stdout=stdout, stderr=stderr, environ={})

        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "dataset\ndata\n")
        self.assertEqual(stderr.getvalue(), "")

        stdout = io.StringIO()
        code = cli_main(["_complete"], stdout=stdout, stderr=io.StringIO(), environ={})
        self.assertEqual(code, 0)
        self.assertIn("data", stdout.getvalue().splitlines())

        stdout = io.StringIO()
        code = cli_main(["_complete", "data"], stdout=stdout, stderr=io.StringIO(), environ={})
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "schema\npreview\ncoverage\nexport\nsnapshot\n")

        stdout = io.StringIO()
        code = cli_main(
            ["_complete", "data", "coverage", "dataset", ""],
            stdout=stdout,
            stderr=io.StringIO(),
            environ={},
        )
        self.assertEqual(code, 0)
        self.assertIn("--keys", stdout.getvalue().splitlines())

        stdout = io.StringIO()
        code = cli_main(["_complete", "--w"], stdout=stdout, stderr=io.StringIO(), environ={})
        self.assertEqual(code, 0)
        self.assertIn("--workspace", stdout.getvalue().splitlines())

    def test_event_ack_requires_an_id_or_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace.init(Path(directory))
            # A marker alone is enough to reach client discovery; no server should be contacted
            # because syntax validation happens first.
            stdout = io.StringIO()
            stderr = io.StringIO()
            code = cli_main(
                ["--workspace", str(workspace.root), "events", "ack"],
                stdout=stdout,
                stderr=stderr,
            )
            self.assertEqual(code, 2)
            self.assertIn("requires an event ID or --all", stderr.getvalue())

    def test_dataset_status_requires_a_dataset_or_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace.init(Path(directory))
            stdout = io.StringIO()
            stderr = io.StringIO()
            code = cli_main(
                ["--workspace", str(workspace.root), "dataset", "status"],
                stdout=stdout,
                stderr=stderr,
            )
            self.assertEqual(code, 2)
            self.assertIn("requires a dataset or --all", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
