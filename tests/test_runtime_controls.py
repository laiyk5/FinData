from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from findata.cron import CronManager, CronSchedule
from findata.events import EventStore
from findata.rate_limit import FileRateLimiter
from findata.storage import Workspace


class EventStoreTests(unittest.TestCase):
    def test_acknowledgement_is_an_append_only_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            event = store.record("task_failed", "error", "download failed", dataset="prices")
            store.ack(event.event_id)

            listed = store.list_events()
            self.assertEqual(len(listed), 1)
            self.assertTrue(listed[0].acknowledged)
            records = [json.loads(line) for line in store.path.read_text().splitlines()]
            self.assertEqual(records[0]["kind"], "task_failed")
            self.assertEqual(records[1]["kind"], "acknowledgement")
            self.assertEqual(records[1]["reference"], event.event_id)

    def test_filters_unread_since_and_severity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            old = store.record("notice", "info", "old", timestamp=10)
            store.record("task_failed", "error", "new", timestamp=20)
            store.ack(old.event_id, timestamp=21)
            items = store.list_events(unread=True, since=15, severity="error")
            self.assertEqual([item.message for item in items], ["new"])


class FileRateLimiterTests(unittest.TestCase):
    def test_permits_are_shared_by_independent_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rate.json"
            first = FileRateLimiter(path, limit=2, period=60)
            second = FileRateLimiter(path, limit=2, period=60)
            self.assertFalse(first.try_acquire(now=100))  # buckets start empty
            self.assertTrue(second.try_acquire(now=130))
            self.assertFalse(first.try_acquire(now=131))
            self.assertTrue(second.try_acquire(now=160))
            # Idle time refills only to the bounded capacity.
            self.assertTrue(first.try_acquire(now=220))
            self.assertTrue(second.try_acquire(now=220))
            self.assertFalse(first.try_acquire(now=220))

    def test_invalid_limits_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                FileRateLimiter(Path(directory) / "rate.json", limit=0, period=60)


class CronScheduleTests(unittest.TestCase):
    def test_weekday_schedule_is_evaluated_in_iana_timezone(self) -> None:
        schedule = CronSchedule("40 17 * * 1-5", "Asia/Shanghai")
        after = datetime(2026, 7, 17, 9, 41, tzinfo=UTC)  # Friday, 17:41 local
        self.assertEqual(schedule.next_after(after), datetime(2026, 7, 20, 9, 40, tzinfo=UTC))

    def test_repeated_wall_time_uses_first_occurrence(self) -> None:
        schedule = CronSchedule("30 1 * * *", "America/New_York")
        before = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)
        self.assertEqual(schedule.next_after(before), datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
        # The second 01:30 is not considered another run.
        self.assertEqual(
            schedule.next_after(datetime(2026, 11, 1, 5, 31, tzinfo=UTC)),
            datetime(2026, 11, 2, 6, 30, tzinfo=UTC),
        )

    def test_detects_nonexistent_scheduled_wall_time(self) -> None:
        schedule = CronSchedule("30 2 * * *", "Europe/Berlin")
        skipped = schedule.skipped_between(
            datetime(2026, 3, 28, 12, 0, tzinfo=UTC),
            datetime(2026, 3, 29, 3, 0, tzinfo=UTC),
        )
        self.assertEqual(skipped, ["2026-03-29T02:30:00"])


class CronManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = Workspace.init(self.root)
        self.events = EventStore(self.root)
        self.submissions: list[tuple[str, str, dict[str, object]]] = []
        self.manager = CronManager(
            self.workspace,
            self.events,
            submit=lambda dataset, operation, operands: self.submissions.append(
                (dataset, operation, operands)
            ),
            provider_ready=lambda _dataset: True,
            update_ready=lambda _dataset: True,
        )

    def test_defaults_are_disabled_and_enabled_job_fires_update(self) -> None:
        jobs = self.manager.list_jobs(now=datetime(2026, 7, 20, 0, 0, tzinfo=UTC))
        self.assertEqual(len(jobs), 4)
        self.assertTrue(all(not job.enabled for job in jobs))
        self.manager.enable("tushare_daily_basic", now=datetime(2026, 7, 20, 0, 0, tzinfo=UTC))
        self.manager.tick(datetime(2026, 7, 20, 9, 40, tzinfo=UTC))
        self.assertEqual(self.submissions, [("tushare_daily_basic", "update", {})])

    def test_failed_precondition_skips_and_records_actionable_event(self) -> None:
        manager = CronManager(
            self.workspace,
            self.events,
            submit=lambda *_args: self.fail("must not submit"),
            provider_ready=lambda _dataset: False,
            update_ready=lambda _dataset: True,
        )
        with self.assertRaises(ValueError):
            manager.enable("tushare_daily_basic", now=datetime(2026, 7, 20, tzinfo=UTC))
        self.assertEqual(self.events.list_events()[0].kind, "cron_skipped")

    def test_missed_run_is_recorded_without_submission(self) -> None:
        self.manager.enable("tushare_trade_cal", now=datetime(2026, 7, 19, 0, 0, tzinfo=UTC))
        self.manager.note_shutdown(datetime(2026, 7, 19, 1, 0, tzinfo=UTC))
        self.manager.recover(datetime(2026, 7, 20, 2, 0, tzinfo=UTC))
        self.assertEqual(self.submissions, [])
        self.assertEqual(self.events.list_events()[0].kind, "cron_missed")

    def test_repeated_tick_within_one_minute_does_not_kill_scheduler(self) -> None:
        self.manager.enable(
            "tushare_daily_basic", now=datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
        )
        first = datetime(2026, 7, 20, 9, 0, 1, tzinfo=UTC)

        self.manager.tick(first)
        self.manager.tick(first + timedelta(seconds=10))

        self.assertEqual(
            self.workspace.get_config("cron.jobs")["tushare_daily_basic"]["last_checked"],
            "2026-07-20T09:00:00+00:00",
        )

    def test_dst_gap_records_warning_event(self) -> None:
        self.manager.set_schedule(
            "tushare_trade_cal", "30 2 * * *", "Europe/Berlin"
        )
        self.manager.enable(
            "tushare_trade_cal", now=datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
        )
        self.manager.tick(datetime(2026, 3, 29, 3, 0, tzinfo=UTC))
        event = next(item for item in self.events.list_events() if item.kind == "cron_dst_gap")
        self.assertEqual(event.severity, "warning")


if __name__ == "__main__":
    unittest.main()
