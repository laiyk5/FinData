from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from findata.cli import main as cli_main
from findata.presentation import CLIOutput
from findata.server import FindataServer, initialize_workspace


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class CLIPresentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        initialize_workspace(self.root)
        self.server = FindataServer(
            self.root,
            port=0,
            provider_mode="mock",
            today=date(2026, 7, 20),
        )
        self.server.start_background()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.tempdir.cleanup()

    def run_cli(
        self,
        *arguments: str,
        tty: bool = False,
        environ: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        stream = TTYBuffer if tty else io.StringIO
        stdout = stream()
        stderr = stream()
        code = cli_main(
            ["--workspace", str(self.root), *arguments],
            stdout=stdout,
            stderr=stderr,
            environ={} if environ is None else environ,
        )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_human_collections_and_details_are_not_raw_json(self) -> None:
        code, output, errors = self.run_cli("dataset", "ls")

        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("NAME", output)
        self.assertIn("tushare_daily_basic", output)
        self.assertNotIn("{", output)

        code, output, _ = self.run_cli("provider", "check", "tushare")
        self.assertEqual(code, 0)
        self.assertIn("Provider", output)
        self.assertIn("Ready", output)
        self.assertNotIn("{", output)

    def test_color_is_capability_aware_and_never_leaks_into_json(self) -> None:
        code, output, _ = self.run_cli(
            "--color", "always", "provider", "check", "tushare", tty=True
        )
        self.assertEqual(code, 0)
        self.assertIn("\x1b[", output)

        code, output, _ = self.run_cli(
            "--color", "always", "--format", "json", "provider", "check", "tushare", tty=True
        )
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b[", output)
        self.assertTrue(json.loads(output)["ready"])

        code, output, _ = self.run_cli(
            "--color",
            "auto",
            "provider",
            "check",
            "tushare",
            tty=True,
            environ={"TERM": "dumb"},
        )
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b[", output)

        code, output, _ = self.run_cli(
            "--color",
            "auto",
            "provider",
            "check",
            "tushare",
            tty=True,
            environ={"NO_COLOR": "1"},
        )
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b[", output)

    def test_empty_and_narrow_human_views_remain_readable(self) -> None:
        code, output, _ = self.run_cli("events", "ls", "--unread")
        self.assertEqual(code, 0)
        self.assertEqual(output, "No results found.\n")

        with patch(
            "findata.presentation.shutil.get_terminal_size",
            return_value=os.terminal_size((40, 24)),
        ):
            code, output, _ = self.run_cli("dataset", "ls", tty=True)
        self.assertEqual(code, 0)
        plain = output.replace("\x1b[1m", "").replace("\x1b[0m", "")
        self.assertTrue(all(len(line) <= 40 for line in plain.splitlines()))

    def test_json_error_is_structured_and_json_follow_is_rejected(self) -> None:
        code, output, errors = self.run_cli(
            "--format", "json", "dataset", "describe", "does_not_exist"
        )
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        error = json.loads(errors)
        self.assertEqual(error["type"], "error")
        self.assertIn("unknown dataset", error["error"])

        code, output, errors = self.run_cli(
            "--format",
            "json",
            "task",
            "run",
            "tushare_trade_cal",
            "complete",
            "--params",
            '{"exchanges":["SSE"],"timerange":"2026-07-17:2026-07-20"}',
            "--follow",
        )
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("JSONL", json.loads(errors)["error"])

        code, output, errors = self.run_cli("--format", "json", "provider", "check")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertEqual(json.loads(errors)["type"], "error")

    def test_jsonl_follow_emits_only_typed_records(self) -> None:
        code, output, errors = self.run_cli(
            "--format",
            "jsonl",
            "task",
            "run",
            "tushare_trade_cal",
            "complete",
            "--params",
            '{"exchanges":["SSE"],"timerange":"2026-07-17:2026-07-20"}',
            "--follow",
        )

        self.assertEqual(code, 0, errors)
        records = [json.loads(line) for line in output.splitlines()]
        self.assertGreaterEqual(len(records), 3)
        self.assertTrue(all(isinstance(item.get("type"), str) for item in records))
        self.assertEqual(records[0]["type"], "task.accepted")
        self.assertEqual(records[-1]["type"], "task.result")
        self.assertNotIn("\x1b[", output)

    def test_json_wait_is_exactly_one_document(self) -> None:
        code, output, errors = self.run_cli(
            "--format",
            "json",
            "task",
            "run",
            "tushare_trade_cal",
            "complete",
            "--params",
            '{"exchanges":["SSE"],"timerange":"2026-07-17:2026-07-20"}',
            "--wait",
        )
        self.assertEqual(code, 0, errors)
        self.assertEqual(len(output.splitlines()), 1)
        self.assertEqual(json.loads(output)["status"], "succeeded")

    def test_wait_reports_acceptance_and_ctrl_c_detaches(self) -> None:
        with patch("findata.cli.time.sleep", side_effect=KeyboardInterrupt):
            code, output, errors = self.run_cli(
                "task",
                "run",
                "tushare_trade_cal",
                "complete",
                "--params",
                '{"exchanges":["SSE"],"timerange":"2020-01-01:2026-07-20"}',
                "--wait",
            )

        self.assertEqual(code, 130)
        self.assertEqual(output, "")
        self.assertIn("Accepted", errors)
        self.assertIn("detached", errors.lower())
        tasks = self.server.taskrunner.list_handles()
        self.assertEqual(len(tasks), 1)
        self.assertNotEqual(tasks[0].status, "canceled")

    def test_logs_follow_ctrl_c_detaches(self) -> None:
        submitted = json.loads(
            self.run_cli(
                "--json",
                "task",
                "run",
                "tushare_trade_cal",
                "complete",
                "--params",
                '{"exchanges":["SSE"],"timerange":"2020-01-01:2026-07-20"}',
            )[1]
        )
        with patch("findata.cli.time.sleep", side_effect=KeyboardInterrupt):
            code, _, errors = self.run_cli("task", "logs", str(submitted["handle_id"]), "--follow")

        self.assertEqual(code, 130)
        self.assertIn("detached", errors.lower())

    def test_equals_form_global_options_match_spaced_forms(self) -> None:
        code, output, errors = self.run_cli("--format=json", "provider", "ls")
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        spaced = json.loads(self.run_cli("--format", "json", "provider", "ls")[1])
        self.assertEqual(json.loads(output), spaced)

        code, output, _ = self.run_cli("--color=never", "provider", "check", "tushare", tty=True)
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b[", output)

        code, output, _ = self.run_cli("provider", "ls", "--format=json")
        self.assertEqual(code, 0)
        self.assertIn("items", json.loads(output))

    def test_invalid_global_option_values_are_usage_errors(self) -> None:
        code, output, errors = self.run_cli("--format=bogus", "provider", "ls")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("bogus", errors)

        code, _, errors = self.run_cli("provider", "ls", "--format")
        self.assertEqual(code, 2)
        self.assertIn("--format", errors)

        code, _, errors = self.run_cli("--color=sometimes", "provider", "ls")
        self.assertEqual(code, 2)
        self.assertIn("--color", errors)


class ProgressPresentationTests(unittest.TestCase):
    @patch("findata.presentation.Progress")
    def test_interactive_progress_uses_one_transient_rich_live_task(self, progress_type) -> None:
        stderr = TTYBuffer()
        output = CLIOutput(
            output_format="human",
            color_mode="auto",
            stdout=TTYBuffer(),
            stderr=stderr,
            environ={},
        )
        progress = progress_type.return_value
        progress.add_task.return_value = 7
        output._accepted_at = 0

        with patch("findata.presentation.time.monotonic", return_value=1):
            output.state(
                {
                    "status": "running",
                    "stage": "fetching:tushare_daily_basic",
                    "progress": {"current": 1, "total": 3},
                }
            )
            output.state(
                {
                    "status": "running",
                    "stage": "committing:tushare_daily_basic",
                    "progress": {"current": 2, "total": 3},
                }
            )

        self.assertTrue(progress_type.call_args.kwargs["transient"])
        progress.start.assert_called_once_with()
        progress.add_task.assert_called_once()
        progress.update.assert_called_once()
        output.finish_progress()
        progress.stop.assert_called_once_with()

    @patch("findata.presentation.Progress")
    def test_redirected_progress_remains_plain_newline_text(self, progress_type) -> None:
        stderr = io.StringIO()
        output = CLIOutput(
            output_format="human",
            color_mode="auto",
            stdout=io.StringIO(),
            stderr=stderr,
            environ={},
        )

        output.state({"status": "running", "stage": "fetching:data"})

        progress_type.assert_not_called()
        self.assertEqual(stderr.getvalue(), "... fetching data\n")

    @patch("findata.presentation.Progress")
    def test_rich_rendering_failure_falls_back_to_plain_text(self, progress_type) -> None:
        stderr = TTYBuffer()
        output = CLIOutput(
            output_format="human",
            color_mode="auto",
            stdout=TTYBuffer(),
            stderr=stderr,
            environ={},
        )
        progress_type.return_value.start.side_effect = RuntimeError("render failed")
        output._accepted_at = 0

        with patch("findata.presentation.time.monotonic", return_value=1):
            output.state({"status": "running", "stage": "fetching:data"})

        self.assertEqual(stderr.getvalue(), "... fetching data\n")
        self.assertIsNone(output._progress)

    @patch("findata.presentation.Progress")
    def test_persistent_follow_log_stops_live_progress_first(self, progress_type) -> None:
        output = CLIOutput(
            output_format="human",
            color_mode="auto",
            stdout=TTYBuffer(),
            stderr=TTYBuffer(),
            environ={},
        )
        progress = progress_type.return_value
        progress.add_task.return_value = 7
        output._accepted_at = 0

        with patch("findata.presentation.time.monotonic", return_value=1):
            output.state({"status": "running", "stage": "fetching:data"})
        output.log("provider request completed")

        progress.stop.assert_called_once_with()
        self.assertIsNone(output._progress)
        self.assertEqual(output.stdout.getvalue(), "provider request completed\n")

    @patch("findata.presentation.Progress")
    def test_progress_includes_server_metrics_elapsed_and_eta(self, progress_type) -> None:
        output = CLIOutput(
            output_format="human",
            color_mode="auto",
            stdout=TTYBuffer(),
            stderr=TTYBuffer(),
            environ={},
        )
        progress_type.return_value.add_task.return_value = 1
        output._accepted_at = 1

        with patch("findata.presentation.time.monotonic", return_value=3):
            output.state(
                {
                    "status": "running",
                    "stage": "fetching:data",
                    "progress": {
                        "current": 2,
                        "total": 4,
                        "provider_requests": 2,
                        "rows_fetched": 12000,
                        "checkpoints": 1,
                    },
                }
            )

        description = progress_type.return_value.add_task.call_args.args[0]
        self.assertIn("2 requests", description)
        self.assertIn("12,000 rows", description)
        self.assertIn("1 checkpoint", description)
        self.assertIn("2 s elapsed", description)
        self.assertIn("ETA 2 s", description)

    @patch("findata.presentation.Progress")
    def test_no_progress_uses_plain_status_without_rich(self, progress_type) -> None:
        stderr = TTYBuffer()
        output = CLIOutput(
            output_format="human",
            color_mode="auto",
            stdout=TTYBuffer(),
            stderr=stderr,
            environ={},
            progress_enabled=False,
        )

        output.state({"status": "running", "stage": "fetching:data"})

        progress_type.assert_not_called()
        self.assertIn("fetching data", stderr.getvalue())

    def test_long_interactive_human_result_uses_pager(self) -> None:
        rendered: list[str] = []
        stdout = TTYBuffer()
        output = CLIOutput(
            output_format="human",
            color_mode="auto",
            stdout=stdout,
            stderr=TTYBuffer(),
            environ={},
            pager=lambda text, _color: rendered.append(text),
        )

        output.result({"items": [{"key": f"item-{index}"} for index in range(100)]})

        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(len(rendered), 1)
        self.assertIn("item-99", rendered[0])

    def test_redirected_human_result_does_not_use_pager(self) -> None:
        rendered: list[str] = []
        stdout = io.StringIO()
        output = CLIOutput(
            output_format="human",
            color_mode="auto",
            stdout=stdout,
            stderr=io.StringIO(),
            environ={},
            pager=lambda text, _color: rendered.append(text),
        )

        output.result({"items": [{"key": f"item-{index}"} for index in range(100)]})

        self.assertEqual(rendered, [])
        self.assertIn("item-99", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
