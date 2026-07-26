from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from findata.cli import main as cli_main
from findata.events import EventStore
from findata.identifiers import (
    AmbiguousIdentifierError,
    InvalidIdentifierError,
    resolve_identifier,
)
from findata.presentation import CLIOutput
from findata.server import FindataServer, initialize_workspace
from findata.taskrunner import TaskContext, TaskRunner


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def diagnostic_worker(request: dict[str, object], context: TaskContext) -> dict[str, object]:
    context.log("started")
    context.diagnostic(
        "warning",
        "PARTIAL_DATA",
        "some rows were unavailable",
        context={"dataset": request["dataset"]},
        count=3,
    )
    return {"ok": True}


class IdentifierResolutionTests(unittest.TestCase):
    def test_exact_unique_invalid_missing_and_ambiguous_resolution(self) -> None:
        first = "12345678" + "0" * 24
        second = "12345678" + "f" * 24

        self.assertEqual(resolve_identifier(first, [first, second]), first)
        self.assertEqual(resolve_identifier("123456780", [first, second]), first)
        with self.assertRaises(InvalidIdentifierError):
            resolve_identifier("1234567", [first])
        with self.assertRaises(InvalidIdentifierError):
            resolve_identifier("1234567G", [first])
        with self.assertRaises(LookupError):
            resolve_identifier("abcdef12", [first])
        with self.assertRaises(AmbiguousIdentifierError):
            resolve_identifier("12345678", [first, second])

    def test_event_ack_accepts_a_unique_prefix_and_returns_the_full_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            event = store.record("example", "warning", "example warning")

            resolved, already = store.ack(event.event_id[:8])

            self.assertEqual(resolved, event.event_id)
            self.assertFalse(already)
            self.assertTrue(store.list_events()[0].acknowledged)

            _, already = store.ack(event.event_id)
            self.assertTrue(already)


class PrefixHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        initialize_workspace(self.root)
        self.server = FindataServer(
            self.root, port=0, provider_mode="mock", today=date(2026, 7, 20)
        )
        self.server.start_background()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.tempdir.cleanup()

    def test_task_routes_resolve_prefix_and_ambiguity_has_no_side_effect(self) -> None:
        handle = self.server.taskrunner.submit("one", "update", {})
        full = self.request("GET", f"/v1/tasks/{handle[:8]}")
        self.assertEqual(full["handle_id"], handle)

        self.server.taskrunner._append_log(full["execution_id"], "copyable log line")
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = cli_main(
            ["--workspace", str(self.root), "task", "logs", handle[:8]],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("copyable log line\n", stdout.getvalue())
        self.assertNotIn("No results found", stdout.getvalue())

        original = self.server.taskrunner.status(handle)
        collision_id = handle[:8] + ("f" if handle[8] != "f" else "e") + handle[9:]
        collision = replace(original, handle_id=collision_id)
        with self.server.taskrunner._condition:  # Contract fixture: force a UUID-prefix collision.
            self.server.taskrunner._handles[collision_id] = collision
        self.addCleanup(self.server.taskrunner._handles.pop, collision_id, None)

        with self.assertRaises(HTTPError) as caught:
            self.request("POST", f"/v1/tasks/{handle[:8]}/cancel", {})
        self.assertEqual(caught.exception.code, 409)
        caught.exception.close()
        self.assertNotEqual(self.server.taskrunner.status(handle).status, "canceled")
        self.assertNotEqual(self.server.taskrunner.status(collision_id).status, "canceled")

    def test_prefix_status_codes_and_event_ack_full_id(self) -> None:
        for operand, expected in (("abc", 400), ("abcdef12", 404)):
            with self.assertRaises(HTTPError) as caught:
                self.request("GET", f"/v1/tasks/{operand}")
            self.assertEqual(caught.exception.code, expected)
            caught.exception.close()

        event = self.server.events.record("example", "error", "example failure")
        result = self.request("POST", "/v1/events/ack", {"event_id": event.event_id[:8]})
        self.assertEqual(result["event_id"], event.event_id)

    def test_cli_uses_the_workspace_display_timezone(self) -> None:
        self.request(
            "POST",
            "/v1/config",
            {"key": "display.timezone", "value": "Asia/Shanghai"},
        )
        self.server.events.record("epoch", "warning", "epoch warning", timestamp=0)
        stdout = io.StringIO()
        stderr = io.StringIO()

        code = cli_main(
            ["--workspace", str(self.root), "events", "ls"],
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("1970-01-01T08:00:00+08:00", stdout.getvalue())

        with self.assertRaises(HTTPError) as caught:
            self.request(
                "POST",
                "/v1/config",
                {"key": "display.timezone", "value": "Not/A-Timezone"},
            )
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()

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


class HumanFormattingTests(unittest.TestCase):
    def test_semantic_values_are_humanized_without_changing_json(self) -> None:
        value = {
            "created_at": 0.0,
            "elapsed_seconds": 125.0,
            "fetched_requests": 12_500,
            "coverage_percent": 98.765,
            "measurement": 0.00001,
            "price": Decimal("1234.5000"),
            "handle_id": "1234567890abcdef",
        }
        human = io.StringIO()
        CLIOutput(
            output_format="human",
            color_mode="never",
            stdout=human,
            stderr=io.StringIO(),
            display_timezone="Asia/Shanghai",
        ).result(value)
        rendered = human.getvalue()
        self.assertIn("1970-01-01T08:00:00+08:00", rendered)
        self.assertIn("2 min 5 s", rendered)
        self.assertIn("12,500", rendered)
        self.assertIn("98.77%", rendered)
        self.assertIn("1e-05", rendered)
        self.assertIn("1234.5000", rendered)
        self.assertIn("1234567890abcdef", rendered)

        structured = io.StringIO()
        CLIOutput(
            output_format="json",
            color_mode="never",
            stdout=structured,
            stderr=io.StringIO(),
            display_timezone="Asia/Shanghai",
        ).result({key: item for key, item in value.items() if key != "price"})
        self.assertEqual(json.loads(structured.getvalue())["created_at"], 0.0)
        self.assertEqual(json.loads(structured.getvalue())["measurement"], 0.00001)


class DiagnosticPresentationTests(unittest.TestCase):
    def test_task_diagnostics_are_persisted_and_counted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskRunner(Path(directory), diagnostic_worker) as runner:
                handle = runner.submit("example", "update", {})
                result = runner.wait(handle, timeout=15)
                logs = runner.logs(handle)

        self.assertEqual(result.diagnostic_counts, {"warning": 3, "error": 0})
        self.assertEqual(logs[0]["type"], "log")
        self.assertEqual(logs[0]["message"], "started")
        self.assertIsInstance(logs[0]["time"], float)
        self.assertEqual(logs[1]["type"], "task.diagnostic")
        self.assertEqual(logs[1]["code"], "PARTIAL_DATA")
        self.assertEqual(logs[1]["count"], 3)
        self.assertIsInstance(logs[1]["time"], float)
        self.assertEqual(logs[-1]["message"], "succeeded")

    def test_redirected_human_diagnostics_are_bounded_with_exact_totals(self) -> None:
        stderr = io.StringIO()
        output = CLIOutput(
            output_format="human",
            color_mode="never",
            stdout=io.StringIO(),
            stderr=stderr,
        )
        for index in range(12):
            output.diagnostic(
                {
                    "severity": "warning" if index % 2 == 0 else "error",
                    "code": f"D{index}",
                    "message": f"diagnostic {index}",
                    "count": 1,
                }
            )
        output.finish_diagnostics("12345678")

        rendered = stderr.getvalue()
        self.assertIn("diagnostic 9", rendered)
        self.assertNotIn("diagnostic 10", rendered)
        self.assertEqual(rendered.count("additional diagnostics suppressed"), 1)
        self.assertIn("6 warnings, 6 errors", rendered)
        self.assertIn("findata task logs 12345678", rendered)

    def test_jsonl_diagnostics_keep_every_logical_occurrence(self) -> None:
        stdout = io.StringIO()
        output = CLIOutput(
            output_format="jsonl",
            color_mode="never",
            stdout=stdout,
            stderr=io.StringIO(),
        )
        output.diagnostic(
            {"severity": "warning", "code": "REPEAT", "message": "warning", "count": 3}
        )

        record = json.loads(stdout.getvalue())
        self.assertEqual(record["type"], "task.diagnostic")
        self.assertEqual(record["count"], 3)


class TaskLogPresentationTests(unittest.TestCase):
    def test_human_log_records_render_clock_in_display_timezone(self) -> None:
        stdout = io.StringIO()
        output = CLIOutput(
            output_format="human",
            color_mode="never",
            stdout=stdout,
            stderr=io.StringIO(),
            display_timezone="Asia/Shanghai",
        )
        output.log_record({"type": "log", "time": 0.0, "message": "worker started"})
        output.log_record({"type": "log", "message": "legacy without timestamp"})

        self.assertEqual(
            stdout.getvalue(),
            "08:00:00 worker started\nlegacy without timestamp\n",
        )

    def test_jsonl_log_records_pass_through_unchanged(self) -> None:
        stdout = io.StringIO()
        output = CLIOutput(
            output_format="jsonl",
            color_mode="never",
            stdout=stdout,
            stderr=io.StringIO(),
        )
        record = {"type": "log", "time": 1784883136.5, "message": "succeeded"}
        output.log_record(record)

        self.assertEqual(json.loads(stdout.getvalue()), record)


if __name__ == "__main__":
    unittest.main()
