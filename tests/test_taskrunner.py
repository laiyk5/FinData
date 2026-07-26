from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from findata import DataLoader
from findata_tushare.datasets.operations import (
    OperationWorker,
    register_v1_datasets,
    resolve_v1_dependency,
)
from findata.toolkit.rate_limit import FileRateLimiter
from findata.storage import Workspace
from findata.taskrunner import (
    DatasetBusyError,
    QueueFullError,
    TaskContext,
    TaskNotFoundError,
    TaskRunner,
)

TASK_TIMEOUT = 30.0


def successful_worker(request: dict[str, object], context: TaskContext) -> dict[str, object]:
    context.log("worker started")
    context.progress(1, 2)
    context.checkpoint()
    context.progress(2, 2)
    return {"pid": os.getpid(), "value": request["operands"]}


def context_worker(request: dict[str, object], context: TaskContext) -> dict[str, object]:
    return {
        "revision": request["configuration_revision"],
        "settings": request["settings"],
    }


def slow_worker(request: dict[str, object], context: TaskContext) -> dict[str, object]:
    duration = float(dict(request["operands"])["duration"])
    started = time.time()
    while time.time() - started < duration:
        context.checkpoint()
        time.sleep(0.01)
    return {"pid": os.getpid(), "started": started, "ended": time.time()}


def dependency_worker(request: dict[str, object], context: TaskContext) -> dict[str, object]:
    if request["dataset"] == "parent":
        context.fulfill("child", {"timerange": "2026-07-01:2026-07-02"})
        return {"dependency": "complete"}
    return slow_worker({"operands": {"duration": 0.3}}, context)


def dependency_resolver(
    parent: str, target: str, requirement: dict[str, object]
) -> tuple[str, dict[str, object]]:
    if (parent, target) != ("parent", "child"):
        raise ValueError("undeclared dependency")
    return "complete", requirement


def liveness_worker(request: dict[str, object], context: TaskContext) -> dict[str, object]:
    context.begin_subtask(timeout=0.05)
    time.sleep(0.15)
    context.end_subtask()
    return {"completed": True}


def rate_wait_worker(request: dict[str, object], context: TaskContext) -> dict[str, object]:
    path = Path(str(dict(request["operands"])["path"]))
    limiter = FileRateLimiter(path, limit=1, period=100)
    limiter.acquire(checkpoint=context.checkpoint, waiting=context.waiting)
    return {"permit": True}


def waiting_worker(request: dict[str, object], context: TaskContext) -> dict[str, object]:
    context.waiting("blocked_on_test")
    context.waiting("blocked_on_test")
    context.running()
    return {"ok": True}


def failing_worker(request: dict[str, object], context: TaskContext) -> dict[str, object]:
    raise RuntimeError("boom")


class TaskRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_default_launch_budget_accommodates_a_cold_installed_worker(self) -> None:
        runner = TaskRunner(self.root, successful_worker)

        self.assertGreaterEqual(runner.launch_timeout, 30)

    def test_execution_runs_in_child_process_and_persists_progress_and_logs(self) -> None:
        with TaskRunner(self.root, successful_worker, global_concurrency=2) as runner:
            handle_id = runner.submit(
                "findata/tushare/trade_cal",
                "complete",
                {"exchanges": ["SSE"], "timerange": "2026-07-01:2026-07-02"},
            )
            result = runner.wait(handle_id, timeout=TASK_TIMEOUT)

            self.assertEqual(result.status, "succeeded")
            self.assertNotEqual(result.result["pid"], os.getpid())
            self.assertEqual(result.progress, {"current": 2, "total": 2})
            logs = runner.logs(handle_id)
            self.assertEqual(
                [item["message"] for item in logs],
                ["worker started", "succeeded"],
            )
            self.assertTrue(
                all(item["type"] == "log" and isinstance(item["time"], float) for item in logs)
            )
            self.assertTrue((self.root / "tasks" / "handles" / f"{handle_id}.json").is_file())

    def test_state_transitions_are_logged_once_per_change(self) -> None:
        with TaskRunner(self.root, waiting_worker) as runner:
            handle_id = runner.submit("example", "update", {})
            result = runner.wait(handle_id, timeout=TASK_TIMEOUT)

            self.assertEqual(result.status, "succeeded")
            self.assertEqual(
                [item["message"] for item in runner.logs(handle_id)],
                ["waiting: blocked_on_test", "running", "succeeded"],
            )

    def test_dependency_lifecycle_is_logged_on_the_parent(self) -> None:
        with TaskRunner(
            self.root,
            dependency_worker,
            global_concurrency=1,
            dependency_resolver=dependency_resolver,
        ) as runner:
            parent = runner.submit("parent", "complete", {})
            result = runner.wait(parent, timeout=TASK_TIMEOUT)

            self.assertEqual(result.status, "succeeded", result.error)
            self.assertEqual(
                [item["message"] for item in runner.logs(parent)],
                [
                    'dependency requested: child {"timerange":"2026-07-01:2026-07-02"}',
                    "dependency fulfilled: child",
                    "succeeded",
                ],
            )

    def test_failed_execution_logs_the_terminal_error(self) -> None:
        with TaskRunner(self.root, failing_worker) as runner:
            handle_id = runner.submit("example", "update", {})
            result = runner.wait(handle_id, timeout=TASK_TIMEOUT)

            self.assertEqual(result.status, "failed")
            self.assertEqual(
                [item["message"] for item in runner.logs(handle_id)],
                ["failed: RuntimeError: boom"],
            )

    def test_canceled_execution_logs_the_terminal_state(self) -> None:
        with TaskRunner(self.root, slow_worker, cancel_grace=0.2) as runner:
            handle_id = runner.submit("findata/tushare/daily_basic", "complete", {"duration": 10.0})
            runner.wait_for_status(handle_id, {"running"}, timeout=TASK_TIMEOUT)
            runner.cancel(handle_id)

            self.assertEqual(runner.wait(handle_id, timeout=TASK_TIMEOUT).status, "canceled")
            self.assertEqual(
                [item["message"] for item in runner.logs(handle_id)],
                ["canceled"],
            )

    def test_execution_receives_submission_time_settings_snapshot(self) -> None:
        current = {
            "configuration_revision": 4,
            "settings": {"dataset.example.symbols": ["first"]},
        }
        runner = TaskRunner(
            self.root,
            context_worker,
            execution_context=lambda _dataset: current,
        )
        with runner:
            handle = runner.submit("example", "update", {})
            current["configuration_revision"] = 5
            current["settings"] = {"dataset.example.symbols": ["second"]}
            result = runner.wait(handle, timeout=TASK_TIMEOUT)
        self.assertEqual(result.result["revision"], 4)
        self.assertEqual(result.result["settings"], {"dataset.example.symbols": ["first"]})

    def test_identical_complete_submissions_coalesce_but_handles_cancel_independently(self) -> None:
        operands = {
            "duration": 0.15,
            "symbols": ["000001.SZ"],
            "timerange": "2026-07-01:2026-07-02",
        }
        with TaskRunner(self.root, slow_worker, global_concurrency=2) as runner:
            first = runner.submit(
                "findata/tushare/daily_basic", "complete", operands, owner="alice"
            )
            second = runner.submit("findata/tushare/daily_basic", "complete", operands, owner="bob")

            self.assertNotEqual(first, second)
            self.assertEqual(runner.status(first).execution_id, runner.status(second).execution_id)
            cancellation = runner.cancel(first)
            self.assertTrue(cancellation.shared_execution_continues)
            self.assertEqual(runner.wait(first, timeout=TASK_TIMEOUT).status, "canceled")
            completed = runner.wait(second, timeout=TASK_TIMEOUT)
            self.assertEqual(completed.status, "succeeded")

    def test_parameterless_updates_never_coalesce_and_serialize_per_dataset(self) -> None:
        with TaskRunner(self.root, slow_worker, global_concurrency=2) as runner:
            first = runner.submit("findata/tushare/stock_basic", "update", {"duration": 0.08})
            second = runner.submit("findata/tushare/stock_basic", "update", {"duration": 0.08})

            self.assertNotEqual(
                runner.status(first).execution_id, runner.status(second).execution_id
            )
            first_result = runner.wait(first, timeout=TASK_TIMEOUT).result
            second_result = runner.wait(second, timeout=TASK_TIMEOUT).result
            self.assertGreaterEqual(second_result["started"], first_result["ended"])

    def test_dataset_reset_reservation_rejects_submissions_and_active_work(self) -> None:
        with TaskRunner(self.root, slow_worker, global_concurrency=1) as runner:
            with runner.reserve_dataset_reset("example"):
                with self.assertRaises(DatasetBusyError):
                    runner.submit("example", "update", {})
            handle = runner.submit("example", "update", {"duration": 0.1})
            with self.assertRaises(DatasetBusyError):
                with runner.reserve_dataset_reset("example"):
                    pass
            runner.wait(handle, timeout=TASK_TIMEOUT)

    def test_queue_capacity_counts_executions_not_coalesced_handles(self) -> None:
        with TaskRunner(
            self.root,
            slow_worker,
            global_concurrency=1,
            per_dataset_queue_limit=2,
        ) as runner:
            running = runner.submit("findata/tushare/daily_basic", "update", {"duration": 0.3})
            queued_one = runner.submit("findata/tushare/daily_basic", "update", {"duration": 0.2})
            queued_two = runner.submit("findata/tushare/daily_basic", "complete", {"duration": 0.2})
            shared = runner.submit("findata/tushare/daily_basic", "complete", {"duration": 0.2})

            self.assertEqual(
                runner.status(queued_two).execution_id, runner.status(shared).execution_id
            )
            with self.assertRaises(QueueFullError):
                runner.submit("findata/tushare/daily_basic", "refresh", {"duration": 0.2})

            for handle in (running, queued_one, queued_two, shared):
                runner.cancel(handle)

    def test_canceling_last_subscriber_stops_execution_after_cooperative_checkpoint(self) -> None:
        with TaskRunner(self.root, slow_worker, cancel_grace=0.2) as runner:
            handle = runner.submit("findata/tushare/daily_basic", "complete", {"duration": 10.0})
            runner.wait_for_status(handle, {"running"}, timeout=TASK_TIMEOUT)

            cancellation = runner.cancel(handle)
            terminal = runner.wait(handle, timeout=TASK_TIMEOUT)

            self.assertFalse(cancellation.shared_execution_continues)
            self.assertEqual(terminal.status, "canceled")

    def test_recovery_marks_active_records_failed_with_server_interrupted(self) -> None:
        runner = TaskRunner(self.root, slow_worker, cancel_grace=0.1)
        runner.start()
        handle = runner.submit("findata/tushare/daily_basic", "complete", {"duration": 10.0})
        runner.wait_for_status(handle, {"running"}, timeout=TASK_TIMEOUT)
        runner.crash_for_test()

        with TaskRunner(self.root, successful_worker) as recovered:
            status = recovered.status(handle)

            self.assertEqual(status.status, "failed")
            self.assertEqual(status.error, "server_interrupted")

    def test_operation_worker_executes_mocked_primary_data_path_in_child(self) -> None:
        workspace = Workspace.init(self.root)
        register_v1_datasets(workspace)
        worker = OperationWorker(
            workspace=self.root,
            provider="mock",
            token="test-token",
            today="2026-07-20",
        )
        with TaskRunner(
            self.root,
            worker,
            global_concurrency=1,
            dependency_resolver=resolve_v1_dependency,
        ) as runner:
            handle = runner.submit(
                "findata/tushare/daily_basic",
                "complete",
                {
                    "symbols": ["tushare:000300.SH"],
                    "timerange": "2026-06-29:2026-07-04",
                },
            )
            status = runner.wait(handle, timeout=TASK_TIMEOUT)

            handles = runner.list_handles()

        self.assertEqual(status.status, "succeeded", status.error)
        self.assertEqual(
            status.progress,
            {
                "current": 3,
                "total": 3,
                "provider_requests": 3,
                "rows_fetched": 15,
                "checkpoints": 1,
            },
        )
        self.assertEqual(
            {item.dataset for item in handles},
            {
                "findata/tushare/daily_basic",
                "findata/tushare/trade_cal",
                "findata/tushare/index_basic",
                "findata/tushare/index_weight",
            },
        )
        self.assertTrue(
            all(item.owner.startswith("trigger:") for item in handles if item.handle_id != handle)
        )
        table = (
            DataLoader(self.root)
            .dataset("findata/tushare/daily_basic")
            .query(
                keys=["000001.SZ", "600000.SH", "600519.SH"],
                time_range=("2026-06-29", "2026-07-04"),
                require_coverage=True,
            )
        )
        self.assertGreater(table.num_rows, 0)

    def test_waiting_parent_releases_global_slot_for_owned_triggered_task(self) -> None:
        with TaskRunner(
            self.root,
            dependency_worker,
            global_concurrency=1,
            dependency_resolver=dependency_resolver,
        ) as runner:
            parent = runner.submit("parent", "complete", {})
            result = runner.wait(parent, timeout=TASK_TIMEOUT)
            handles = runner.list_handles()

        self.assertEqual(result.status, "succeeded", result.error)
        child = next(item for item in handles if item.dataset == "child")
        self.assertEqual(child.status, "succeeded")
        self.assertEqual(child.owner, f"trigger:{parent}")

    def test_canceling_parent_recursively_releases_triggered_handle(self) -> None:
        with TaskRunner(
            self.root,
            dependency_worker,
            global_concurrency=1,
            dependency_resolver=dependency_resolver,
            cancel_grace=0.2,
        ) as runner:
            parent = runner.submit("parent", "complete", {})
            runner.wait_for_status(parent, {"waiting"}, timeout=TASK_TIMEOUT)
            child = next(item for item in runner.list_handles() if item.dataset == "child")
            runner.cancel(parent)

            self.assertEqual(runner.wait(parent, timeout=TASK_TIMEOUT).status, "canceled")
            self.assertEqual(runner.wait(child.handle_id, timeout=TASK_TIMEOUT).status, "canceled")

    def test_dependency_depth_limit_rejects_before_child_submission(self) -> None:
        with TaskRunner(
            self.root,
            dependency_worker,
            dependency_resolver=dependency_resolver,
            max_trigger_depth=0,
        ) as runner:
            parent = runner.submit("parent", "complete", {})
            result = runner.wait(parent, timeout=TASK_TIMEOUT)

            self.assertEqual(result.status, "failed")
            self.assertIn("dependency depth exceeds", result.error)
            self.assertEqual(len(runner.list_handles()), 1)

    def test_liveness_timeout_records_event_without_killing_process(self) -> None:
        events: list[tuple[str, str, str, dict[str, object]]] = []

        def sink(kind: str, severity: str, message: str, **context: object) -> None:
            events.append((kind, severity, message, context))

        with TaskRunner(self.root, liveness_worker, event_sink=sink) as runner:
            handle = runner.submit("findata/tushare/stock_basic", "update", {})
            result = runner.wait(handle, timeout=TASK_TIMEOUT)

        self.assertEqual(result.status, "succeeded")
        liveness = next(item for item in events if item[0] == "liveness_timeout")
        self.assertEqual(liveness[1], "warning")

    def test_rate_limit_wait_is_cancelable_and_releases_global_slot(self) -> None:
        with TaskRunner(self.root, rate_wait_worker, global_concurrency=1) as runner:
            waiting = runner.submit(
                "findata/tushare/trade_cal",
                "complete",
                {"path": str(self.root / "provider-rate.json")},
            )
            runner.wait_for_status(waiting, {"waiting"}, timeout=TASK_TIMEOUT)
            self.assertEqual(runner.status(waiting).reason, "provider_rate_limit")
            quick = runner.submit(
                "findata/tushare/stock_basic", "update", {"path": str(self.root / "fast-rate.json")}
            )
            # The second task also waits for its own empty bucket, proving it was dispatched
            # while the first waiting task no longer occupied global capacity.
            runner.wait_for_status(quick, {"waiting"}, timeout=TASK_TIMEOUT)
            runner.cancel(waiting)
            runner.cancel(quick)
            self.assertEqual(runner.wait(waiting, timeout=TASK_TIMEOUT).status, "canceled")
            self.assertEqual(runner.wait(quick, timeout=TASK_TIMEOUT).status, "canceled")

    def test_terminal_history_prunes_old_handles_and_unreferenced_executions(self) -> None:
        with TaskRunner(self.root, successful_worker, terminal_history=2) as runner:
            handles = []
            for sequence in range(3):
                handle = runner.submit(
                    "findata/tushare/stock_basic", "update", {"sequence": sequence}
                )
                runner.wait(handle, timeout=TASK_TIMEOUT)
                handles.append(handle)

            with self.assertRaises(TaskNotFoundError):
                runner.status(handles[0])
            self.assertEqual(len(list((self.root / "tasks" / "handles").glob("*.json"))), 2)
            self.assertEqual(len(list((self.root / "tasks" / "executions").glob("*.json"))), 2)


if __name__ == "__main__":
    unittest.main()
