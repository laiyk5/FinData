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
from findata.server import FindataServer, ServerAlreadyRunningError, initialize_workspace


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

        self.assertEqual(self.run_cli("config", "set", "provider.tushare.token", "--env", "TUSHARE_API_TOKEN")[0], 0)
        code, provider = self.run_cli("provider", "check", "tushare", "--format", "json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(provider)["ready"])
        self.assertEqual(
            self.run_cli(
                "dataset",
                "universe",
                "set",
                "tushare_daily_basic",
                "CSI300@latest",
            )[0],
            0,
        )

        code, output = self.run_cli(
            "task",
            "run",
            "tushare_daily_basic",
            "complete",
            "--param",
            "symbols=CSI300",
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
        data = DataLoader(self.root).dataset("tushare_daily_basic").query(
            keys=["000001.SZ", "600000.SH", "600519.SH"],
            time_range=("2026-06-29", "2026-07-04"),
            require_coverage=True,
        )
        self.assertGreater(data.num_rows, 0)

    def test_task_list_status_logs_and_cancel_endpoints(self) -> None:
        submitted = self.request(
            "POST",
            "/v1/tasks",
            {
                "dataset": "tushare_daily_basic",
                "operation": "complete",
                "operands": {"symbols": ["CSI300"], "timerange": "2026-06-29:2026-07-04"},
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
            self.run_cli("--json", "config", "get", "provider.tushare.token")[1]
        )
        self.assertEqual(config["value"], "<redacted>")

        self.run_cli(
            "dataset", "universe", "set", "tushare_daily_basic", "CSI300@latest"
        )
        enabled = json.loads(
            self.run_cli("--json", "cron", "enable", "tushare_daily_basic")[1]
        )
        self.assertTrue(enabled["enabled"])
        self.server.cron.tick(datetime.fromisoformat(enabled["next_run"]))
        tasks = self.request("GET", "/v1/tasks")["items"]
        self.assertTrue(any(item["owner"] == "cron" for item in tasks))

        self.server.events.record("task_failed", "error", "injected task failure")
        events = json.loads(self.run_cli("--json", "events", "ls", "--unread")[1])
        failure = next(item for item in events["items"] if item["kind"] == "task_failed")
        self.run_cli("events", "ack", failure["event_id"])
        unread = json.loads(self.run_cli("--json", "events", "ls", "--unread")[1])
        self.assertNotIn(failure["event_id"], {item["event_id"] for item in unread["items"]})

    def test_dataset_and_provider_discovery_commands_report_registered_contracts(self) -> None:
        datasets = json.loads(self.run_cli("--json", "dataset", "ls")[1])
        self.assertEqual(len(datasets["items"]), 4)
        described = json.loads(
            self.run_cli("--json", "dataset", "describe", "tushare_daily_basic")[1]
        )
        self.assertEqual(described["provider"], "tushare")
        self.assertEqual(
            {item["name"] for item in described["operations"]},
            {"update", "complete", "refresh"},
        )
        operation = json.loads(
            self.run_cli(
                "--json", "dataset", "operation", "tushare_daily_basic", "complete"
            )[1]
        )
        self.assertEqual(operation["required"], ["symbols", "timerange"])
        providers = json.loads(self.run_cli("--json", "provider", "ls")[1])
        self.assertEqual(providers["items"][0]["name"], "tushare")
        statuses = json.loads(self.run_cli("--json", "dataset", "status", "--all")[1])
        self.assertEqual(len(statuses["items"]), 4)

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

    def test_structured_params_and_universe_clear_cli_forms(self) -> None:
        self.run_cli("dataset", "universe", "set", "tushare_daily_basic", "CSI300@latest")
        direct = json.loads(
            self.run_cli("--json", "dataset", "universe", "tushare_daily_basic")[1]
        )
        self.assertEqual(direct["selectors"], ["CSI300@latest"])
        cleared = json.loads(
            self.run_cli("--json", "dataset", "universe", "clear", "tushare_daily_basic")[1]
        )
        self.assertEqual(cleared["selectors"], [])
        code, output = self.run_cli(
            "--json",
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
                "config", "set", "provider.tushare.token", "--stdin",
                stdin_text="findata-mock:fail=daily_basic@2\n",
            )[0],
            0,
        )

        provider = json.loads(self.run_cli("--json", "provider", "check", "tushare")[1])
        self.assertTrue(provider["ready"])
        self.assertEqual(provider["mode"], "mock")
        self.run_cli(
            "dataset", "universe", "set", "tushare_daily_basic", "CSI300@latest"
        )
        code, failed = self.run_cli(
            "--json",
            "task", "run", "tushare_daily_basic", "complete",
            "--param", "symbols=CSI300",
            "--param", "timerange=2026-06-29:2026-07-04",
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
                "config", "set", "provider.tushare.token", "--stdin",
                stdin_text="findata-mock\n",
            )[0],
            0,
        )
        code, resumed = self.run_cli(
            "--json",
            "task", "run", "tushare_daily_basic", "complete",
            "--param", "symbols=CSI300",
            "--param", "timerange=2026-06-29:2026-07-04",
            "--wait",
        )
        self.assertEqual(code, 0, resumed)
        self.assertEqual(json.loads(resumed)["result"]["fetched_requests"], 2)

    def test_universe_validation_and_system_queue_status(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "PUT",
                "/v1/datasets/tushare_stock_basic/universe",
                {"selectors": ["600000.SH"]},
            )
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "PUT",
                "/v1/datasets/tushare_daily_basic/universe",
                {"selectors": ["NOT-A-SYMBOL"]},
            )
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()
        status = self.request("GET", "/v1/system/status")
        self.assertEqual(status["running_tasks"], 0)
        self.assertEqual(status["queue_lengths"], {})

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
