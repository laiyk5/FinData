"""Pure-function and transport-error tests for the CLI; no server required."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError
from zoneinfo import ZoneInfo

from findata.cli import (
    CLIUsageError,
    ServerError,
    _dataset_operands,
    _declared_secret_keys,
    _duration_seconds,
    _error_message,
    _extract_color,
    _extract_format,
    _extract_option,
    _extract_presentation,
    _params,
    _validate_cli_args,
    main as cli_main,
)
from findata.presentation import (
    _display,
    _error_suggestion,
    _format_count,
    _format_duration,
    _format_measurement,
    _format_timestamp,
    _truncate,
)


class GlobalOptionExtractionTests(unittest.TestCase):
    def test_extract_option_accepts_spaced_and_equals_forms(self) -> None:
        arguments = ["--format", "json"]
        self.assertEqual(_extract_option(arguments, "--format"), "json")
        self.assertEqual(arguments, [])

        arguments = ["task", "ls", "--format=jsonl"]
        self.assertEqual(_extract_option(arguments, "--format"), "jsonl")
        self.assertEqual(arguments, ["task", "ls"])

    def test_extract_option_last_occurrence_wins(self) -> None:
        arguments = ["--format", "json", "--format=jsonl"]
        self.assertEqual(_extract_option(arguments, "--format"), "jsonl")
        self.assertEqual(arguments, [])

    def test_extract_option_missing_value_is_a_usage_error(self) -> None:
        with self.assertRaises(CLIUsageError):
            _extract_option(["--format"], "--format")

    def test_extract_option_absent_returns_none_and_keeps_arguments(self) -> None:
        arguments = ["task", "run", "--param", "timerange=2026-01-01:2026-02-01"]
        self.assertIsNone(_extract_option(arguments, "--format"))
        self.assertEqual(len(arguments), 4)

    def test_extract_format_defaults_and_validation(self) -> None:
        self.assertEqual(_extract_format([]), "human")

        # --json was removed; the token is left for Click to reject.
        arguments = ["--json"]
        self.assertEqual(_extract_format(arguments), "human")
        self.assertEqual(arguments, ["--json"])

        self.assertEqual(_extract_format(["--format=jsonl"]), "jsonl")
        with self.assertRaises(CLIUsageError):
            _extract_format(["--format=bogus"])

    def test_extract_color_defaults_and_validation(self) -> None:
        self.assertEqual(_extract_color([]), "auto")
        self.assertEqual(_extract_color(["--color=never"]), "never")
        with self.assertRaises(CLIUsageError):
            _extract_color(["--color", "sometimes"])

    def test_extract_presentation_strips_flags_and_rejects_conflicts(self) -> None:
        arguments = ["--quiet", "--no-progress", "task", "ls"]
        quiet, verbose, progress = _extract_presentation(arguments)
        self.assertEqual((quiet, verbose, progress), (True, False, False))
        self.assertEqual(arguments, ["task", "ls"])

        with self.assertRaises(CLIUsageError):
            _extract_presentation(["--quiet", "--verbose"])


class OperandParsingTests(unittest.TestCase):
    def test_param_pairs_and_repeated_keys(self) -> None:
        operands = _params(["symbols=a", "symbols=b", "timerange=2026-01-01:2026-02-01"])
        self.assertEqual(operands["symbols"], ["a", "b"])
        self.assertEqual(operands["timerange"], "2026-01-01:2026-02-01")

    def test_param_rejects_malformed_input(self) -> None:
        with self.assertRaises(ValueError):
            _params(["no-equals-sign"])
        with self.assertRaises(ValueError):
            _params(["=value"])
        with self.assertRaises(ValueError):
            _params(["a=1"], '{"symbols":["a"]}')

    def test_params_json_inline_file_and_stdin(self) -> None:
        self.assertEqual(_params([], '{"a": 1}'), {"a": 1})

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operands.json"
            path.write_text('{"b": 2}', encoding="utf-8")
            self.assertEqual(_params([], f"@{path}"), {"b": 2})

        self.assertEqual(_params([], "-", stdin=io.StringIO('{"c": 3}')), {"c": 3})

    def test_params_rejects_invalid_and_non_object_json(self) -> None:
        with self.assertRaises(ValueError):
            _params([], "{not json")
        with self.assertRaises(ValueError):
            _params([], "[1, 2]")


class DurationAndDatasetOperandTests(unittest.TestCase):
    def test_duration_units_and_rejections(self) -> None:
        self.assertEqual(_duration_seconds("30s"), 30)
        self.assertEqual(_duration_seconds("2m"), 120)
        self.assertEqual(_duration_seconds("1.5h"), 5400)
        self.assertEqual(_duration_seconds("1d"), 86400)
        for invalid in ("10", "-5m", "xm", "1w"):
            with self.assertRaises(ValueError, msg=invalid):
                _duration_seconds(invalid)

    def namespace(self, **overrides: object) -> SimpleNamespace:
        values: dict[str, object] = {
            "symbols": (),
            "indexes": (),
            "exchanges": (),
            "timerange": None,
            "range_start": None,
            "range_end": None,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_dataset_operands_passthrough_and_range_composition(self) -> None:
        operands = _dataset_operands(self.namespace(symbols=("a", "b"), exchanges=("SSE",)))
        self.assertEqual(operands, {"symbols": ("a", "b"), "exchanges": ("SSE",)})

        operands = _dataset_operands(self.namespace(timerange="2026-01-01:2026-02-01"))
        self.assertEqual(operands, {"timerange": "2026-01-01:2026-02-01"})

        operands = _dataset_operands(
            self.namespace(range_start="2026-01-01", range_end="2026-02-01")
        )
        self.assertEqual(operands, {"timerange": "2026-01-01:2026-02-01"})

    def test_dataset_operands_rejects_conflicting_and_partial_ranges(self) -> None:
        with self.assertRaises(CLIUsageError):
            _dataset_operands(
                self.namespace(timerange="2026-01-01:2026-02-01", range_start="2026-01-01")
            )
        with self.assertRaises(CLIUsageError):
            _dataset_operands(self.namespace(range_start="2026-01-01"))


class ValidateCliArgsTests(unittest.TestCase):
    def test_events_ack_requires_exactly_one_target(self) -> None:
        args = SimpleNamespace(group="events", action="ack", event_id=None, all=False)
        with self.assertRaises(ValueError):
            _validate_cli_args(args)
        args = SimpleNamespace(group="events", action="ack", event_id="abc12345", all=True)
        with self.assertRaises(ValueError):
            _validate_cli_args(args)
        args = SimpleNamespace(group="events", action="ack", event_id="abc12345", all=False)
        _validate_cli_args(args)

    def test_dataset_status_requires_a_dataset_or_all(self) -> None:
        args = SimpleNamespace(group="dataset", action="status", dataset=None, all=False)
        with self.assertRaises(ValueError):
            _validate_cli_args(args)

    def test_follow_with_json_is_rejected_for_streaming_commands(self) -> None:
        args = SimpleNamespace(group="task", action="run", param=[], params=None, follow=True)
        with self.assertRaises(CLIUsageError):
            _validate_cli_args(args, output_format="json")
        _validate_cli_args(args, output_format="jsonl")

        args = SimpleNamespace(group="dataset", action="complete", follow=True)
        with self.assertRaises(CLIUsageError):
            _validate_cli_args(args, output_format="json")

    def test_config_set_requires_exactly_one_value_source(self) -> None:
        base = {
            "group": "config",
            "action": "set",
            "key": "display.timezone",
            "value": None,
            "value_json": None,
            "env": None,
            "stdin": False,
        }
        with self.assertRaises(ValueError):
            _validate_cli_args(SimpleNamespace(**base))
        with self.assertRaises(ValueError):
            _validate_cli_args(SimpleNamespace(**{**base, "value": "UTC", "env": "TZ"}))
        _validate_cli_args(SimpleNamespace(**{**base, "value": "UTC"}))


class HumanFormattingTests(unittest.TestCase):
    def test_duration_adaptive_units(self) -> None:
        self.assertEqual(_format_duration(0.24), "240 ms")
        self.assertEqual(_format_duration(3.2), "3.2 s")
        self.assertEqual(_format_duration(125), "2 min 5 s")
        self.assertEqual(_format_duration(120), "2 min")

    def test_measurement_scientific_notation_thresholds(self) -> None:
        self.assertEqual(_format_measurement(3.14), "3.14")
        self.assertEqual(_format_measurement(1234567.0), "1234567")
        self.assertEqual(_format_measurement(1e9), "1e+09")
        self.assertEqual(_format_measurement(0.00001), "1e-05")

    def test_count_grouping_and_truncation(self) -> None:
        self.assertEqual(_format_count(12500), "12,500")
        self.assertEqual(_truncate("short", 10), "short")
        self.assertEqual(_truncate("a much longer value", 10), "a much lo…")

    def test_timestamp_and_semantic_display(self) -> None:
        self.assertEqual(_format_timestamp(0, ZoneInfo("UTC")), "1970-01-01T00:00:00+00:00")
        self.assertEqual(_display(None), "—")
        self.assertEqual(_display(True), "yes")
        self.assertEqual(_display(12.3, field="progress_percent"), "12.3%")
        self.assertEqual(_display(12500, field="total"), "12,500")
        self.assertEqual(_display(["a", 1]), "a, 1")
        self.assertEqual(_display([]), "none")
        self.assertEqual(_display({"row_limit": 6000}), "row_limit=6000")
        self.assertEqual(_display({}), "none")


class DeclaredSecretKeyTests(unittest.TestCase):
    class StubClient:
        def __init__(self, response: object = None, error: Exception | None = None) -> None:
            self.response = response
            self.error = error

        def request(self, method: str, path: str, body: object = None) -> object:
            if self.error is not None:
                raise self.error
            return self.response

    def test_declared_keys_are_built_from_provider_metadata(self) -> None:
        client = self.StubClient(
            {"items": [{"name": "findata-plugins/tushare", "secret_fields": ["token"]}]}
        )
        self.assertEqual(_declared_secret_keys(client), {"provider.findata-plugins/tushare.token"})

    def test_declared_keys_degrade_to_empty_on_failure(self) -> None:
        self.assertEqual(_declared_secret_keys(self.StubClient(error=RuntimeError("down"))), set())
        self.assertEqual(_declared_secret_keys(self.StubClient({"items": "broken"})), set())


class ErrorMessageRenderingTests(unittest.TestCase):
    def test_server_error_5xx_without_detail_has_no_trailing_colon(self) -> None:
        self.assertEqual(_error_message(ServerError(502, "")), "server returned 502")
        self.assertEqual(_error_message(ServerError(502, "broken")), "server returned 502: broken")

    def test_urlerror_is_unwrapped_and_401_explains_token_mismatch(self) -> None:
        self.assertEqual(
            _error_message(URLError("connection refused")),
            "cannot reach the server (connection refused)",
        )
        self.assertIn("token does not match", _error_message(ServerError(401, "")))

    def test_error_suggestions_point_at_recovery_commands(self) -> None:
        self.assertEqual(
            _error_suggestion("no running server for workspace /tmp/ws"),
            "Start the server with: findata-server start /tmp/ws",
        )
        self.assertEqual(
            _error_suggestion("unknown dataset 'x'"), "List datasets with: findata dataset ls"
        )
        self.assertEqual(
            _error_suggestion("unknown provider 'y'"), "List providers with: findata provider ls"
        )
        self.assertEqual(
            _error_suggestion("configuration key 'k' is not set"),
            "List configuration with: findata config ls",
        )
        # Messages that already name a command get no second suggestion.
        self.assertIsNone(
            _error_suggestion(
                "lost contact with the server while waiting for task abc; "
                "it may still be running — inspect with: findata task status abc"
            )
        )


class EntryPointTests(unittest.TestCase):
    def test_bare_invocation_prints_help_to_stdout_and_exits_zero(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli_main([], stdout=stdout, stderr=stderr, environ={})
        self.assertEqual(code, 0)
        self.assertTrue(stdout.getvalue().startswith("Usage: findata"))
        self.assertEqual(stderr.getvalue(), "")

    def test_unknown_option_is_a_clean_usage_error(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli_main(["--bogus"], stdout=stdout, stderr=stderr, environ={})
        self.assertEqual(code, 2)
        self.assertIn("No such option", stderr.getvalue())
        self.assertNotIn("Error: Error", stderr.getvalue())


class ErrorMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.workspace = Path(self.tempdir.name)
        (self.workspace / "workspace.json").write_text('{"workspace_version": 1}', encoding="utf-8")

    def run_cli(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli_main(
            ["--workspace", str(self.workspace), *arguments],
            stdout=stdout,
            stderr=stderr,
            environ={},
        )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_server_error_statuses_map_to_exit_1_with_sanitized_message(self) -> None:
        expectations = {
            400: "detail",
            401: "authentication failed",
            404: "detail",
            409: "detail",
            500: "server returned 500: detail",
        }
        for status, expected in expectations.items():
            with patch("findata.cli._Client") as client_type:
                client_type.return_value.request.side_effect = ServerError(status, "detail")
                code, output, errors = self.run_cli("task", "ls")
            self.assertEqual(code, 1, msg=f"status {status}")
            self.assertEqual(output, "")
            self.assertIn(expected, errors, msg=f"status {status}")
            self.assertNotIn("Traceback", errors)

    def test_server_error_json_detail_is_unwrapped_for_humans(self) -> None:
        detail = '{"error":"unknown dataset \'tushare_daily\'"}'
        with patch("findata.cli._Client") as client_type:
            client_type.return_value.request.side_effect = ServerError(400, detail)
            code, _, errors = self.run_cli("dataset", "describe", "tushare_daily")
        self.assertEqual(code, 1)
        self.assertIn("unknown dataset 'tushare_daily'", errors)
        self.assertNotIn("server returned", errors)
        self.assertNotIn("{", errors)

    def test_structured_format_keeps_error_object_on_stderr(self) -> None:
        with patch("findata.cli._Client") as client_type:
            client_type.return_value.request.side_effect = ServerError(409, "queue is full")
            code, output, errors = self.run_cli("--format", "json", "task", "ls")
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        record = json.loads(errors)
        self.assertEqual(record["type"], "error")
        self.assertIn("queue is full", record["error"])

    def test_network_failures_render_an_error_without_a_traceback(self) -> None:
        with patch("findata.cli._Client") as client_type:
            client_type.return_value.request.side_effect = URLError("connection refused")
            code, _, errors = self.run_cli("task", "ls")
        self.assertEqual(code, 1)
        self.assertIn("cannot reach the server", errors)
        self.assertIn("connection refused", errors)
        self.assertNotIn("Traceback", errors)

        with patch("findata.cli._Client") as client_type:
            client_type.return_value.request.side_effect = TimeoutError("timed out")
            code, _, errors = self.run_cli("task", "ls")
        self.assertEqual(code, 1)
        self.assertIn("did not respond", errors)
        self.assertNotIn("Traceback", errors)

    def test_dynamic_completion_falls_back_to_static_candidates_on_timeout(self) -> None:
        with patch("findata.cli._Client") as client_type:
            client_type.return_value.request.side_effect = TimeoutError("timed out")
            code, output, errors = self.run_cli("_complete", "d")
        self.assertEqual(code, 0)
        self.assertEqual(output, "dataset\ndata\n")
        self.assertEqual(errors, "")

    def test_connection_loss_while_waiting_names_the_inspection_command(self) -> None:
        handle = "abcdef0123456789"
        status_polls: list[str] = []

        def route(method: str, path: str, body: object = None) -> dict[str, object]:
            if path.startswith("/v1/config"):
                return {}
            if path.endswith("/logs"):
                return {"items": []}
            status_polls.append(path)
            if len(status_polls) >= 2:
                raise URLError("connection reset")
            return {"status": "running", "handle_id": handle}

        with patch("findata.cli._Client") as client_type:
            client_type.return_value.request.side_effect = route
            code, output, errors = self.run_cli("task", "watch", handle)
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("lost contact", errors)
        self.assertIn(f"findata task status {handle}", errors)
        self.assertNotIn("Traceback", errors)


if __name__ == "__main__":
    unittest.main()
