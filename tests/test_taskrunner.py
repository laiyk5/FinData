from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from findata import DataLoader
from findata.operations import OperationWorker, register_v1_datasets
from findata.storage import Workspace
from findata.taskrunner import QueueFullError, TaskContext, TaskNotFoundError, TaskRunner


def successful_worker(request: dict[str, object], context: TaskContext) -> dict[str, object]:
    context.log("worker started")
    context.progress(1, 2)
    context.checkpoint()
    context.progress(2, 2)
    return {"pid": os.getpid(), "value": request["operands"]}


def slow_worker(request: dict[str, object], context: TaskContext) -> dict[str, object]:
    duration = float(dict(request["operands"])["duration"])
    started = time.time()
    while time.time() - started < duration:
        context.checkpoint()
        time.sleep(0.01)
    return {"pid": os.getpid(), "started": started, "ended": time.time()}


class TaskRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_execution_runs_in_child_process_and_persists_progress_and_logs(self) -> None:
        with TaskRunner(self.root, successful_worker, global_concurrency=2) as runner:
            handle_id = runner.submit(
                "tushare_trade_cal",
                "complete",
                {"exchanges": ["SSE"], "timerange": "2026-07-01:2026-07-02"},
            )
            result = runner.wait(handle_id, timeout=5)

            self.assertEqual(result.status, "succeeded")
            self.assertNotEqual(result.result["pid"], os.getpid())
            self.assertEqual(result.progress, {"current": 2, "total": 2})
            self.assertEqual(runner.logs(handle_id), ["worker started"])
            self.assertTrue((self.root / "tasks" / "handles" / f"{handle_id}.json").is_file())

    def test_identical_complete_submissions_coalesce_but_handles_cancel_independently(self) -> None:
        operands = {"duration": 0.15, "symbols": ["000001.SZ"], "timerange": "2026-07-01:2026-07-02"}
        with TaskRunner(self.root, slow_worker, global_concurrency=2) as runner:
            first = runner.submit("tushare_daily_basic", "complete", operands, owner="alice")
            second = runner.submit("tushare_daily_basic", "complete", operands, owner="bob")

            self.assertNotEqual(first, second)
            self.assertEqual(runner.status(first).execution_id, runner.status(second).execution_id)
            cancellation = runner.cancel(first)
            self.assertTrue(cancellation.shared_execution_continues)
            self.assertEqual(runner.wait(first, timeout=2).status, "canceled")
            completed = runner.wait(second, timeout=5)
            self.assertEqual(completed.status, "succeeded")

    def test_parameterless_updates_never_coalesce_and_serialize_per_dataset(self) -> None:
        with TaskRunner(self.root, slow_worker, global_concurrency=2) as runner:
            first = runner.submit("tushare_stock_basic", "update", {"duration": 0.08})
            second = runner.submit("tushare_stock_basic", "update", {"duration": 0.08})

            self.assertNotEqual(runner.status(first).execution_id, runner.status(second).execution_id)
            first_result = runner.wait(first, timeout=5).result
            second_result = runner.wait(second, timeout=5).result
            self.assertGreaterEqual(second_result["started"], first_result["ended"])

    def test_queue_capacity_counts_executions_not_coalesced_handles(self) -> None:
        with TaskRunner(
            self.root,
            slow_worker,
            global_concurrency=1,
            per_dataset_queue_limit=2,
        ) as runner:
            running = runner.submit("tushare_daily_basic", "update", {"duration": 0.3})
            queued_one = runner.submit("tushare_daily_basic", "update", {"duration": 0.2})
            queued_two = runner.submit("tushare_daily_basic", "complete", {"duration": 0.2})
            shared = runner.submit("tushare_daily_basic", "complete", {"duration": 0.2})

            self.assertEqual(runner.status(queued_two).execution_id, runner.status(shared).execution_id)
            with self.assertRaises(QueueFullError):
                runner.submit("tushare_daily_basic", "refresh", {"duration": 0.2})

            for handle in (running, queued_one, queued_two, shared):
                runner.cancel(handle)

    def test_canceling_last_subscriber_stops_execution_after_cooperative_checkpoint(self) -> None:
        with TaskRunner(self.root, slow_worker, cancel_grace=0.2) as runner:
            handle = runner.submit("tushare_daily_basic", "complete", {"duration": 10.0})
            runner.wait_for_status(handle, {"running"}, timeout=2)

            cancellation = runner.cancel(handle)
            terminal = runner.wait(handle, timeout=3)

            self.assertFalse(cancellation.shared_execution_continues)
            self.assertEqual(terminal.status, "canceled")

    def test_recovery_marks_active_records_failed_with_server_interrupted(self) -> None:
        runner = TaskRunner(self.root, slow_worker, cancel_grace=0.1)
        runner.start()
        handle = runner.submit("tushare_daily_basic", "complete", {"duration": 10.0})
        runner.wait_for_status(handle, {"running"}, timeout=2)
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
        with TaskRunner(self.root, worker) as runner:
            handle = runner.submit(
                "tushare_daily_basic",
                "complete",
                {"symbols": ["CSI300"], "timerange": "2026-06-29:2026-07-04"},
            )
            status = runner.wait(handle, timeout=10)

        self.assertEqual(status.status, "succeeded", status.error)
        table = DataLoader(self.root).dataset("tushare_daily_basic").query(
            keys=["000001.SZ", "600000.SH", "600519.SH"],
            time_range=("2026-06-29", "2026-07-04"),
            require_coverage=True,
        )
        self.assertGreater(table.num_rows, 0)

    def test_terminal_history_prunes_old_handles_and_unreferenced_executions(self) -> None:
        with TaskRunner(self.root, successful_worker, terminal_history=2) as runner:
            handles = []
            for sequence in range(3):
                handle = runner.submit("tushare_stock_basic", "update", {"sequence": sequence})
                runner.wait(handle, timeout=5)
                handles.append(handle)

            with self.assertRaises(TaskNotFoundError):
                runner.status(handles[0])
            self.assertEqual(len(list((self.root / "tasks" / "handles").glob("*.json"))), 2)
            self.assertEqual(len(list((self.root / "tasks" / "executions").glob("*.json"))), 2)


if __name__ == "__main__":
    unittest.main()
