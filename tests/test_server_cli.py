from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from findata import DataLoader
from findata.cli import main as cli_main
from findata.loader import DatasetNotReadyError
from findata.plugins import ProviderPlugin
from findata.server import (
    FindataServer,
    ServerAlreadyRunningError,
    _redact,
    initialize_workspace,
    secret_config_keys,
)


class SecretConfigKeyTests(unittest.TestCase):
    def test_secret_config_keys_come_from_provider_declarations(self) -> None:
        plugin = ProviderPlugin(
            provider_id="acme",
            configuration_schema={},
            secret_fields=("api_key",),
            rate_limit=1,
            period=1,
        )
        self.assertEqual(secret_config_keys([plugin]), frozenset({"provider.acme.api_key"}))

    def test_redact_prefers_declared_keys_and_keeps_heuristic_fallback(self) -> None:
        declared = frozenset({"provider.acme.api_key"})
        self.assertEqual(_redact("provider.acme.api_key", "value", declared), "<redacted>")
        self.assertEqual(_redact("other.token", "value", declared), "<redacted>")
        self.assertEqual(_redact("display.timezone", "UTC", declared), "UTC")


class ServerCLITests(unittest.TestCase):
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

    def test_init_creates_private_bearer_token_and_single_server_lock(self) -> None:
        token_path = self.root / "token"
        self.assertEqual(stat.S_IMODE(token_path.stat().st_mode), 0o600)
        self.assertGreaterEqual(len(token_path.read_text(encoding="utf-8").strip()), 40)

        second = FindataServer(self.root, port=0, provider_mode="mock", today=date(2026, 7, 20))
        with self.assertRaises(ServerAlreadyRunningError):
            second.start_background()

    def test_http_api_rejects_unauthenticated_requests(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.server.base_url}/v1/system/status", timeout=2)

        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()

    def test_cli_primary_workflow_submits_waits_and_queries_published_data(self) -> None:
        os.environ["TUSHARE_API_TOKEN"] = "not-used-by-mock"
        self.addCleanup(os.environ.pop, "TUSHARE_API_TOKEN", None)

        self.assertEqual(
            self.run_cli("config", "set", "provider.tushare.token", "--env", "TUSHARE_API_TOKEN")[
                0
            ],
            0,
        )
        code, provider = self.run_cli("provider", "check", "tushare", "--format", "json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(provider)["ready"])
        self.assertEqual(
            self.run_cli(
                "--format",
                "json",
                "task",
                "run",
                "tushare_index_basic",
                "complete",
                "--param",
                "indexes=tushare:000300.SH",
                "--wait",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                "config",
                "set",
                "dataset.tushare_daily_basic.update_symbols",
                "--value-json",
                '["tushare:000300.SH@latest"]',
            )[0],
            0,
        )

        code, output = self.run_cli(
            "task",
            "run",
            "tushare_daily_basic",
            "complete",
            "--param",
            "symbols=tushare:000300.SH",
            "--param",
            "timerange=2026-06-29:2026-07-04",
            "--wait",
            "--format",
            "json",
        )

        self.assertEqual(code, 0, output)
        task = json.loads(output)
        self.assertEqual(task["status"], "succeeded")
        self.assertTrue(task["handle_id"])
        data = (
            DataLoader(self.root)
            .dataset("tushare_daily_basic")
            .query(
                keys=["000001.SZ", "600000.SH", "600519.SH"],
                time_range=("2026-06-29", "2026-07-04"),
                require_coverage=True,
            )
        )
        self.assertGreater(data.num_rows, 0)

    def test_task_list_status_logs_and_cancel_endpoints(self) -> None:
        submitted = self.request(
            "POST",
            "/v1/tasks",
            {
                "dataset": "tushare_daily_basic",
                "operation": "complete",
                "operands": {
                    "symbols": ["tushare:000300.SH"],
                    "timerange": "2026-06-29:2026-07-04",
                },
            },
        )
        handle = submitted["handle_id"]
        self.assertTrue(submitted["execution_id"])
        terminal = self.wait_http(handle)

        self.assertEqual(terminal["status"], "succeeded")
        listed = self.request("GET", "/v1/tasks")
        self.assertIn(handle, [item["handle_id"] for item in listed["items"]])
        logs = self.request("GET", f"/v1/tasks/{handle}/logs")
        self.assertTrue(any("starting" in item for item in logs["items"]))
        canceled = self.request("POST", f"/v1/tasks/{handle}/cancel", {})
        self.assertEqual(canceled["status"], "succeeded")

    def test_cron_events_and_redacted_config_are_available_through_cli(self) -> None:
        os.environ["TUSHARE_API_TOKEN"] = "not-used-by-mock"
        self.addCleanup(os.environ.pop, "TUSHARE_API_TOKEN", None)
        self.run_cli("config", "set", "provider.tushare.token", "--env", "TUSHARE_API_TOKEN")
        config = json.loads(
            self.run_cli("--format", "json", "config", "get", "provider.tushare.token")[1]
        )
        # Environment references name a variable, not a secret, and stay visible.
        self.assertEqual(config["value"], {"env": "TUSHARE_API_TOKEN"})

        self.run_cli(
            "config", "set", "provider.tushare.token", "--stdin", stdin_text="literal-secret\n"
        )
        config = json.loads(
            self.run_cli("--format", "json", "config", "get", "provider.tushare.token")[1]
        )
        self.assertEqual(config["value"], "<redacted>")
        self.run_cli("config", "unset", "provider.tushare.token")

        self.run_cli(
            "--format",
            "json",
            "task",
            "run",
            "tushare_index_basic",
            "complete",
            "--param",
            "indexes=tushare:000300.SH",
            "--wait",
        )
        self.run_cli(
            "config",
            "set",
            "dataset.tushare_daily_basic.update_symbols",
            "--value-json",
            '["tushare:000300.SH@latest"]',
        )
        enabled = json.loads(
            self.run_cli("--format", "json", "cron", "enable", "tushare_daily_basic")[1]
        )
        self.assertTrue(enabled["enabled"])
        self.server.cron.tick(datetime.fromisoformat(enabled["next_run"]))
        tasks = self.request("GET", "/v1/tasks")["items"]
        self.assertTrue(any(item["owner"] == "cron" for item in tasks))

        self.server.events.record("task_failed", "error", "injected task failure")
        events = json.loads(self.run_cli("--format", "json", "events", "ls", "--unread")[1])
        failure = next(item for item in events["items"] if item["kind"] == "task_failed")
        self.run_cli("events", "ack", failure["event_id"])
        unread = json.loads(self.run_cli("--format", "json", "events", "ls", "--unread")[1])
        self.assertNotIn(failure["event_id"], {item["event_id"] for item in unread["items"]})

    def test_config_set_rejects_literal_values_for_declared_secret_keys(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli_main(
            ["--workspace", str(self.root), "config", "set", "provider.tushare.token", "plain"],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 2)
        self.assertIn("--stdin", stderr.getvalue())
        self.assertIsNone(self.server.workspace.get_config("provider.tushare.token"))

        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli_main(
            [
                "--workspace",
                str(self.root),
                "config",
                "set",
                "provider.tushare.token",
                "--value-json",
                '"plain"',
            ],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 2)
        self.assertIsNone(self.server.workspace.get_config("provider.tushare.token"))

        code, _ = self.run_cli("config", "set", "display.timezone", "UTC")
        self.assertEqual(code, 0)

    def test_dataset_and_provider_discovery_commands_report_registered_contracts(self) -> None:
        datasets = json.loads(self.run_cli("--format", "json", "dataset", "ls")[1])
        self.assertEqual(len(datasets["items"]), 5)
        described = json.loads(
            self.run_cli("--format", "json", "dataset", "describe", "tushare_daily_basic")[1]
        )
        self.assertEqual(described["provider"], "tushare")
        self.assertEqual(
            {item["name"] for item in described["operations"]},
            {"update", "complete", "refresh"},
        )
        operation = json.loads(
            self.run_cli(
                "--format", "json", "dataset", "operation", "tushare_daily_basic", "complete"
            )[1]
        )
        self.assertEqual(operation["required"], ["symbols", "timerange"])
        code, operations_table = self.run_cli("dataset", "operations", "tushare_daily_basic")
        self.assertEqual(code, 0)
        self.assertIn("REQUIRED", operations_table)
        self.assertIn("symbols, timerange", operations_table)
        providers = json.loads(self.run_cli("--format", "json", "provider", "ls")[1])
        self.assertEqual(providers["items"][0]["name"], "tushare")
        statuses = json.loads(self.run_cli("--format", "json", "dataset", "status", "--all")[1])
        self.assertEqual(len(statuses["items"]), 5)

    def test_config_ls_is_a_table_and_internal_keys_stay_internal(self) -> None:
        self.run_cli("config", "set", "display.timezone", "UTC")
        code, output = self.run_cli("config", "ls")
        self.assertEqual(code, 0)
        self.assertIn("KEY", output)
        self.assertIn("display.timezone", output)
        self.assertNotIn("cron.jobs", output)

        self.run_cli("--format", "json", "cron", "enable", "tushare_trade_cal")
        values = json.loads(self.run_cli("--format", "json", "config", "ls")[1])["values"]
        self.assertNotIn("cron.jobs", values)

        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli_main(
            ["--workspace", str(self.root), "config", "set", "cron.jobs", "{}"],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 1)
        self.assertIn("reserved", stderr.getvalue())

        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli_main(
            ["--workspace", str(self.root), "config", "get", "missing.key"],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 1)
        self.assertIn("configuration key 'missing.key' is not set", stderr.getvalue())

    def test_declining_reset_confirmation_cancels_neutrally(self) -> None:
        class TTYInput(io.StringIO):
            def isatty(self) -> bool:
                return True

        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli_main(
            ["--workspace", str(self.root), "dataset", "reset", "tushare_trade_cal"],
            stdin=TTYInput("n\n"),
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 1)
        self.assertIn("Reset canceled.", stderr.getvalue())
        self.assertNotIn("Error", stderr.getvalue())

    def test_dataset_status_reports_committed_state_not_the_contract(self) -> None:
        status = json.loads(
            self.run_cli("--format", "json", "dataset", "status", "tushare_trade_cal")[1]
        )
        self.assertEqual(status["state"], "uninitialized")
        self.assertIsNone(status["publication_id"])
        self.assertNotIn("operations", status)
        self.assertNotIn("capabilities", status)

        self.run_cli(
            "--format",
            "json",
            "task",
            "run",
            "tushare_trade_cal",
            "complete",
            "--param",
            "exchanges=SSE",
            "--param",
            "timerange=2026-07-01:2026-07-10",
            "--wait",
        )
        status = json.loads(
            self.run_cli("--format", "json", "dataset", "status", "tushare_trade_cal")[1]
        )
        self.assertEqual(status["state"], "ready")
        self.assertTrue(status["update_ready"])
        self.assertEqual(status["covered_keys"], 1)
        self.assertEqual(status["coverage_start"], "2026-07-01")
        self.assertEqual(status["coverage_end"], "2026-07-10")

        described = json.loads(
            self.run_cli("--format", "json", "dataset", "describe", "tushare_trade_cal")[1]
        )
        self.assertIn("operations", described)
        self.assertNotIn("covered_keys", described)

        self.run_cli("--format", "json", "task", "run", "tushare_stock_basic", "update", "--wait")
        status = json.loads(
            self.run_cli("--format", "json", "dataset", "status", "tushare_stock_basic")[1]
        )
        self.assertEqual(status["state"], "ready")
        self.assertIsNone(status["covered_keys"])

    def test_invalid_operation_is_rejected_without_creating_a_handle(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "POST",
                "/v1/tasks",
                {"dataset": "tushare_stock_basic", "operation": "complete", "operands": {}},
            )
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()
        self.assertEqual(self.request("GET", "/v1/tasks")["items"], [])

    def test_structured_params_and_typed_config_cli_forms(self) -> None:
        configured = json.loads(
            self.run_cli(
                "--format",
                "json",
                "config",
                "set",
                "dataset.tushare_daily_basic.update_symbols",
                "--value-json",
                '["000001.SZ"]',
            )[1]
        )
        self.assertEqual(configured["value"], ["000001.SZ"])
        direct = json.loads(
            self.run_cli(
                "--format", "json", "config", "get", "dataset.tushare_daily_basic.update_symbols"
            )[1]
        )
        self.assertEqual(direct["value"], ["000001.SZ"])
        cleared = json.loads(
            self.run_cli(
                "--format", "json", "config", "unset", "dataset.tushare_daily_basic.update_symbols"
            )[1]
        )
        self.assertTrue(cleared["removed"])
        code, output = self.run_cli(
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
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["status"], "succeeded")

    def test_unready_real_provider_is_rejected_before_queueing(self) -> None:
        self.server.shutdown()
        real_server = FindataServer(
            self.root, port=0, provider_mode="real", today=date(2026, 7, 20)
        )
        self.server = real_server
        real_server.start_background()
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "POST",
                "/v1/tasks",
                {
                    "dataset": "tushare_trade_cal",
                    "operation": "complete",
                    "operands": {
                        "exchanges": ["SSE"],
                        "timerange": "2026-07-17:2026-07-20",
                    },
                },
            )
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()
        self.assertEqual(self.request("GET", "/v1/tasks")["items"], [])

    def test_special_mock_token_drives_failure_and_resume_through_cli(self) -> None:
        self.server.shutdown()
        self.server = FindataServer(
            self.root, port=0, provider_mode="real", today=date(2026, 7, 20)
        )
        self.server.start_background()
        self.assertEqual(
            self.run_cli(
                "config",
                "set",
                "provider.tushare.token",
                "--stdin",
                stdin_text="findata-mock:fail=daily_basic@2\n",
            )[0],
            0,
        )

        provider = json.loads(self.run_cli("--format", "json", "provider", "check", "tushare")[1])
        self.assertTrue(provider["ready"])
        self.assertEqual(provider["mode"], "mock")
        self.run_cli(
            "--format",
            "json",
            "task",
            "run",
            "tushare_index_basic",
            "complete",
            "--param",
            "indexes=tushare:000300.SH",
            "--wait",
        )
        self.run_cli(
            "config",
            "set",
            "dataset.tushare_daily_basic.update_symbols",
            "--value-json",
            '["tushare:000300.SH@latest"]',
        )
        code, failed = self.run_cli(
            "--format",
            "json",
            "task",
            "run",
            "tushare_daily_basic",
            "complete",
            "--param",
            "symbols=tushare:000300.SH",
            "--param",
            "timerange=2026-06-29:2026-07-04",
            "--wait",
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(failed)["status"], "failed")
        self.assertEqual(
            DataLoader(self.root)
            .dataset("tushare_daily_basic")
            .coverage()
            .column("key")
            .to_pylist(),
            ["000001.SZ"],
        )

        self.assertEqual(
            self.run_cli(
                "config",
                "set",
                "provider.tushare.token",
                "--stdin",
                stdin_text="findata-mock\n",
            )[0],
            0,
        )
        code, resumed = self.run_cli(
            "--format",
            "json",
            "task",
            "run",
            "tushare_daily_basic",
            "complete",
            "--param",
            "symbols=tushare:000300.SH",
            "--param",
            "timerange=2026-06-29:2026-07-04",
            "--wait",
        )
        self.assertEqual(code, 0, resumed)
        self.assertEqual(json.loads(resumed)["result"]["fetched_requests"], 2)

    def test_dataset_reset_requires_confirmation_and_reinitializes_only_that_dataset(self) -> None:
        code, completed = self.run_cli(
            "--format", "json", "task", "run", "tushare_stock_basic", "update", "--wait"
        )
        self.assertEqual(code, 0, completed)
        self.assertGreater(DataLoader(self.root).dataset("tushare_stock_basic").query().num_rows, 0)

        code, reset = self.run_cli(
            "--format", "json", "dataset", "reset", "tushare_stock_basic", "--yes"
        )

        self.assertEqual(code, 0, reset)
        self.assertEqual(json.loads(reset)["state"], "uninitialized")
        with self.assertRaisesRegex(DatasetNotReadyError, "no committed revision"):
            DataLoader(self.root).dataset("tushare_stock_basic").query()
        self.assertEqual(
            self.request("GET", "/v1/datasets/tushare_index_basic")["state"],
            "uninitialized",
        )

    def test_removed_universe_route_and_setting_validation(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "PUT",
                "/v1/datasets/tushare_stock_basic/universe",
                {"selectors": ["600000.SH"]},
            )
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "POST",
                "/v1/config",
                {
                    "key": "dataset.tushare_daily_basic.update_symbols",
                    "value": ["NOT-A-SYMBOL"],
                },
            )
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()
        status = self.request("GET", "/v1/system/status")
        self.assertEqual(status["running_tasks"], 0)
        self.assertEqual(status["queue_lengths"], {})

    def test_dataset_shortcut_dry_run_is_side_effect_free(self) -> None:
        before = self.request("GET", "/v1/tasks")["items"]

        code, rendered = self.run_cli(
            "--format",
            "json",
            "dataset",
            "complete",
            "tushare_daily_basic",
            "--symbols",
            "000001.SZ",
            "--from",
            "2026-07-17",
            "--to",
            "2026-07-20",
            "--dry-run",
        )

        self.assertEqual(code, 0, rendered)
        plan = json.loads(rendered)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["dataset"], "tushare_daily_basic")
        self.assertEqual(plan["operation"], "complete")
        self.assertEqual(plan["operands"]["symbols"], ["000001.SZ"])
        self.assertEqual(plan["operands"]["timerange"], "2026-07-17:2026-07-20")
        self.assertEqual(self.request("GET", "/v1/tasks")["items"], before)

    def test_retry_and_explain_use_retained_normalized_request(self) -> None:
        submitted = self.request(
            "POST",
            "/v1/tasks",
            {
                "dataset": "tushare_trade_cal",
                "operation": "complete",
                "operands": {
                    "exchanges": ["SSE"],
                    "timerange": "2026-07-17:2026-07-20",
                },
            },
        )
        original = self.wait_http(str(submitted["handle_id"]))

        explained = self.request("GET", f"/v1/tasks/{original['handle_id']}/explain")
        self.assertEqual(explained["handle_id"], original["handle_id"])
        self.assertEqual(explained["status"], "succeeded")
        self.assertIn("inspection", explained)

        code, rendered = self.run_cli(
            "--format", "json", "task", "retry", str(original["handle_id"])[:8], "--wait"
        )
        self.assertEqual(code, 0, rendered)
        terminal = json.loads(rendered)
        self.assertNotEqual(terminal["handle_id"], original["handle_id"])
        self.assertEqual(terminal["status"], "succeeded")

    def test_dynamic_completion_reads_live_server_metadata(self) -> None:
        code, output = self.run_cli("_complete", "dataset", "complete", "")

        self.assertEqual(code, 0)
        self.assertIn("tushare_daily_basic", output.splitlines())

        code, output = self.run_cli("_complete", "data", "coverage")
        self.assertEqual(code, 0)
        self.assertIn("tushare_daily_basic", output.splitlines())

        code, output = self.run_cli("_complete", "task", "run", "")
        self.assertEqual(code, 0)
        self.assertIn("tushare_daily_basic", output.splitlines())

        code, output = self.run_cli("_complete", "task", "run", "tushare_daily_basic", "")
        self.assertEqual(code, 0)
        self.assertEqual(output.splitlines(), ["update", "complete", "refresh"])

        # Fish form (no trailing empty word): an exact dataset name completes
        # the next position rather than re-offering the name.
        code, output = self.run_cli("_complete", "task", "run", "tushare_daily_basic")
        self.assertEqual(code, 0)
        self.assertEqual(output.splitlines(), ["update", "complete", "refresh"])

        code, output = self.run_cli("_complete", "dataset", "complete", "tushare_daily_basic")
        self.assertEqual(code, 0)
        self.assertIn("--symbols", output.splitlines())
        self.assertIn("--from", output.splitlines())

        # update is parameterless: no operand flags, no --from/--to.
        code, output = self.run_cli("_complete", "dataset", "update", "tushare_daily_basic")
        self.assertEqual(code, 0)
        self.assertNotIn("--from", output.splitlines())
        self.assertNotIn("--symbols", output.splitlines())

        code, output = self.run_cli("_complete", "dataset", "operation", "tushare_daily_basic", "")
        self.assertEqual(code, 0)
        self.assertIn("complete", output.splitlines())

        code, output = self.run_cli("_complete", "cron", "enable", "")
        self.assertEqual(code, 0)
        self.assertIn("tushare_daily_basic", output.splitlines())

        self.run_cli("config", "set", "display.timezone", "UTC")
        code, output = self.run_cli("_complete", "config", "set", "")
        self.assertEqual(code, 0)
        self.assertIn("display.timezone", output.splitlines())

        self.server.events.record("task_failed", "error", "injected failure")
        code, output = self.run_cli("_complete", "events", "ack", "")
        self.assertEqual(code, 0)
        self.assertEqual(len(output.splitlines()), 1)

        code, output = self.run_cli(
            "_complete", "data", "export", "tushare_daily_basic", "--output-format", ""
        )
        self.assertEqual(code, 0)
        self.assertEqual(output.splitlines(), ["csv", "parquet", "arrow", "jsonl"])

        code, output = self.run_cli("_complete", "dataset", "status", "--a")
        self.assertEqual(code, 0)
        self.assertEqual(output.splitlines(), ["--all"])

    def test_task_ls_status_is_a_validated_choice(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli_main(
            ["--workspace", str(self.root), "task", "ls", "--status", "bogus"],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 2)
        self.assertIn("bogus", stderr.getvalue())

    def test_events_filters_and_ack_all(self) -> None:
        self.server.events.record(
            "cron_skipped", "warning", "first warning", timestamp=time.time() - 300
        )
        self.server.events.record("task_failed", "error", "second error", timestamp=time.time())

        errors_only = json.loads(
            self.run_cli("--format", "json", "events", "ls", "--severity", "error")[1]
        )
        self.assertEqual({item["severity"] for item in errors_only["items"]}, {"error"})

        recent = json.loads(self.run_cli("--format", "json", "events", "ls", "--since", "150s")[1])
        messages = {item["message"] for item in recent["items"]}
        self.assertIn("second error", messages)
        self.assertNotIn("first warning", messages)

        acknowledged = json.loads(self.run_cli("--format", "json", "events", "ack", "--all")[1])
        self.assertEqual(acknowledged["acknowledged"], 2)
        unread = json.loads(self.run_cli("--format", "json", "events", "ls", "--unread")[1])
        self.assertEqual(unread["items"], [])

    def test_cron_set_reset_disable_round_trip(self) -> None:
        self.run_cli(
            "--format",
            "json",
            "cron",
            "set",
            "tushare_trade_cal",
            "--expression",
            "30 6 * * 1",
            "--timezone",
            "UTC",
        )
        jobs = json.loads(self.run_cli("--format", "json", "cron", "ls")[1])["items"]
        job = next(item for item in jobs if item["dataset"] == "tushare_trade_cal")
        self.assertEqual(job["expression"], "30 6 * * 1")
        self.assertEqual(job["timezone"], "UTC")
        self.assertEqual(job["source"], "override")

        self.run_cli("--format", "json", "cron", "enable", "tushare_trade_cal")
        self.run_cli("--format", "json", "cron", "reset", "tushare_trade_cal")
        jobs = json.loads(self.run_cli("--format", "json", "cron", "ls")[1])["items"]
        job = next(item for item in jobs if item["dataset"] == "tushare_trade_cal")
        self.assertEqual(job["expression"], "0 9 * * 1")
        self.assertEqual(job["source"], "suggested")
        self.assertTrue(job["enabled"])

        self.run_cli("--format", "json", "cron", "disable", "tushare_trade_cal")
        jobs = json.loads(self.run_cli("--format", "json", "cron", "ls")[1])["items"]
        job = next(item for item in jobs if item["dataset"] == "tushare_trade_cal")
        self.assertFalse(job["enabled"])

    def test_execution_identifier_is_not_addressable(self) -> None:
        submitted = json.loads(
            self.run_cli(
                "--format",
                "json",
                "task",
                "run",
                "tushare_trade_cal",
                "complete",
                "--param",
                "exchanges=SSE",
                "--param",
                "timerange=2026-07-01:2026-07-03",
                "--wait",
            )[1]
        )
        execution_id = submitted["execution_id"]
        with self.assertRaises(HTTPError) as caught:
            self.request("GET", f"/v1/tasks/{execution_id}")
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()
        with self.assertRaises(HTTPError) as caught:
            self.request("POST", f"/v1/tasks/{execution_id}/cancel", {})
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    def test_provider_check_reports_not_probed_in_mock_mode(self) -> None:
        check = json.loads(self.run_cli("--format", "json", "provider", "check", "tushare")[1])
        self.assertIsNone(check["authenticated"])

    def test_cancel_of_terminal_task_is_reported_as_a_no_op(self) -> None:
        submitted = json.loads(
            self.run_cli(
                "--format",
                "json",
                "task",
                "run",
                "tushare_trade_cal",
                "complete",
                "--param",
                "exchanges=SSE",
                "--param",
                "timerange=2026-07-01:2026-07-03",
                "--wait",
            )[1]
        )
        handle = str(submitted["handle_id"])
        canceled = json.loads(self.run_cli("--format", "json", "task", "cancel", handle)[1])
        self.assertTrue(canceled["already_terminal"])
        self.assertEqual(canceled["status"], "succeeded")

        code, rendered = self.run_cli("task", "cancel", handle)
        self.assertEqual(code, 0)
        self.assertIn("no-op", rendered)

    def run_cli(self, *arguments: str, stdin_text: str = "") -> tuple[int, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = cli_main(
            ["--workspace", str(self.root), *arguments],
            stdin=io.StringIO(stdin_text),
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(stderr.getvalue(), "")
        return code, stdout.getvalue().strip()

    def request(self, method: str, path: str, body: object | None = None) -> dict[str, object]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            f"{self.server.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.server.token}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def wait_http(self, handle: str) -> dict[str, object]:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = self.request("GET", f"/v1/tasks/{handle}")
            if result["status"] in {"succeeded", "failed", "canceled"}:
                return result
            time.sleep(0.02)
        self.fail("task did not finish")


if __name__ == "__main__":
    unittest.main()
