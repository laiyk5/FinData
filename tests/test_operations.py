from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from findata import DataLoader
from findata.contracts import OperandError
from findata.plugins import register_plugins
from findata.storage import Workspace
from findata_plugins.plugins.datasets.tushare_daily_basic import (
    DAILY_BASIC_SPEC,
    daily_basic_plugin,
)
from findata_plugins.plugins.datasets.tushare_daily_basic.operations import DailyBasicDatasetService
from findata_plugins.plugins.datasets.tushare_index_basic import index_basic_plugin
from findata_plugins.plugins.datasets.tushare_index_basic.operations import IndexBasicDatasetService
from findata_plugins.plugins.datasets.tushare_index_weight import index_weight_plugin
from findata_plugins.plugins.datasets.tushare_index_weight.operations import (
    IndexWeightDatasetService,
)
from findata_plugins.plugins.providers.tushare.provider import tushare_provider_plugin
from findata_plugins.shared.engine import TushareClient
from findata_plugins.shared.testing import MockTushareTransport
from findata_plugins.plugins.datasets.tushare_stock_basic import stock_basic_plugin
from findata_plugins.plugins.datasets.tushare_stock_basic.operations import StockBasicDatasetService
from findata_plugins.plugins.datasets.tushare_trade_cal import trade_cal_plugin
from findata_plugins.plugins.datasets.tushare_trade_cal.operations import TradeCalDatasetService


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


_DATASET_SERVICES = {
    "findata-plugins/tushare_trade_cal": TradeCalDatasetService,
    "findata-plugins/tushare_stock_basic": StockBasicDatasetService,
    "findata-plugins/tushare_index_basic": IndexBasicDatasetService,
    "findata-plugins/tushare_index_weight": IndexWeightDatasetService,
    "findata-plugins/tushare_daily_basic": DailyBasicDatasetService,
}


class DatasetService:
    """Test helper dispatching each dataset to its own per-dataset engine."""

    def __init__(
        self,
        workspace: Workspace,
        client: TushareClient,
        *,
        today: date,
        reporter: Any = None,
        now: datetime | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.workspace = workspace
        self.client = client
        self.loader = DataLoader(workspace.root)
        self._options = {
            "today": today,
            "reporter": reporter,
            "now": now,
            "settings": settings,
        }

    def run(
        self, dataset: str, operation: str = "update", operands: dict[str, Any] | None = None
    ) -> Any:
        service = _DATASET_SERVICES[dataset](self.workspace, self.client, **self._options)
        return service.run(operation, operands)


class DatedSnapshotTransport(MockTushareTransport):
    """Return dated snapshots and no new observation in the current month."""

    def __init__(self, *, today: date) -> None:
        super().__init__(today=today)
        self.july_snapshot = False

    def _index_weight(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        start = str(params.get("start_date"))
        index = str(params.get("index_code"))
        if start == "20260501":
            return [_weight_row(index, "000001.SZ", "20260529")]
        if start == "20260601":
            return [_weight_row(index, "600000.SH", "20260630")]
        if start == "20260701" and self.july_snapshot:
            return [_weight_row(index, "600519.SH", "20260715")]
        return []


class SaturatedMarketTransport(MockTushareTransport):
    """Full-market daily_basic responses always reach the declared 6000-row limit."""

    def _daily_basic(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        if params.get("trade_date") and not params.get("ts_code"):
            rows: list[dict[str, Any]] = []
            for index in range(6000):
                row: dict[str, Any] = {
                    "ts_code": f"{index:06d}.SH",
                    "trade_date": str(params["trade_date"]),
                    "limit_status": 1,
                }
                for field in DAILY_BASIC_SPEC.provider_fields[2:-1]:
                    row[field] = 1.0
                rows.append(row)
            return rows
        return super()._daily_basic(params)


def _weight_row(index: str, constituent: str, trade_date: str) -> dict[str, Any]:
    return {
        "index_code": index,
        "con_code": constituent,
        "trade_date": trade_date,
        "weight": 100.0,
    }


class RecordingReporter:
    """Minimal OperationReporter that captures log messages for assertions."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def checkpoint(self) -> None:
        pass

    def log(self, message: str) -> None:
        self.messages.append(message)

    def fulfill(self, dataset: str, requirement: dict[str, Any]) -> Any:
        raise AssertionError(f"unexpected dependency on {dataset}")

    def begin_subtask(self, *, timeout: float) -> None:
        pass

    def end_subtask(self) -> None:
        pass

    def waiting(self, reason: str) -> None:
        pass

    def running(self) -> None:
        pass

    def progress(self, current: int | float, total: int | float, **metrics: int | float) -> None:
        pass

    def stage(self, value: str) -> None:
        pass


class DatasetOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.workspace = Workspace.init(self.root)
        register_v1_datasets(self.workspace)
        self.transport = MockTushareTransport(today=date(2026, 7, 20))
        self.service = DatasetService(
            self.workspace,
            TushareClient(token="test-token", transport=self.transport),
            today=date(2026, 7, 20),
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_operation_logs_plan_fetch_commit_and_completion(self) -> None:
        reporter = RecordingReporter()
        service = DatasetService(
            self.workspace,
            TushareClient(token="test-token", transport=self.transport),
            today=date(2026, 7, 20),
            reporter=reporter,
        )
        service.run(
            "findata-plugins/tushare_trade_cal",
            "complete",
            {"exchanges": ["SSE"], "timerange": "2026-07-17:2026-07-21"},
        )

        messages = reporter.messages
        self.assertTrue(messages[0].startswith("plan: 1 exchanges over "))
        fetch = next(message for message in messages if message.startswith("fetch trade_cal("))
        self.assertIn("exchange=SSE", fetch)
        fetched = next(message for message in messages if message.startswith("fetched trade_cal("))
        self.assertRegex(fetched, r": [1-9]\d* rows$")
        self.assertTrue(
            any(
                message.startswith("committed checkpoint: ") and " publication " in message
                for message in messages
            )
        )
        self.assertTrue(any(message.startswith("coverage: 1 keys, ") for message in messages))
        self.assertRegex(
            messages[-1],
            r"^completed findata-plugins/tushare_trade_cal complete: \d+ requests, \d+ rows, "
            r"\d+ checkpoints in [\d.]+s → publication .+",
        )

    def test_trade_calendar_complete_publishes_and_rerun_skips_coverage(self) -> None:
        first = self.service.run(
            "findata-plugins/tushare_trade_cal",
            "complete",
            {"exchanges": ["SSE"], "timerange": "2026-07-17:2026-07-21"},
        )
        request_count = len(self.transport.requests)
        second = self.service.run(
            "findata-plugins/tushare_trade_cal",
            "complete",
            {"exchanges": ["SSE"], "timerange": "2026-07-17:2026-07-21"},
        )

        self.assertEqual(first.fetched_requests, 1)
        self.assertEqual(second.fetched_requests, 0)
        self.assertEqual(second.publication_id, first.publication_id)
        self.assertEqual(len(self.transport.requests), request_count)
        table = (
            DataLoader(self.root)
            .dataset("findata-plugins/tushare_trade_cal")
            .query(
                keys=["SSE"],
                time_range=("2026-07-17", "2026-07-21"),
                require_coverage=True,
            )
        )
        self.assertEqual(table.num_rows, 4)

    def test_stock_basic_update_merges_all_status_and_exchange_requests(self) -> None:
        result = self.service.run("findata-plugins/tushare_stock_basic", "update", {})

        self.assertEqual(result.fetched_requests, 12)
        table = DataLoader(self.root).dataset("findata-plugins/tushare_stock_basic").query()
        self.assertEqual(table.num_rows, 5)
        self.assertEqual(set(table.column("list_status").to_pylist()), {"L", "D", "G"})

    def test_primary_story_fulfills_dependencies_and_resumes_without_requests(self) -> None:
        result = self.service.run(
            "findata-plugins/tushare_daily_basic",
            "complete",
            {"symbols": ["tushare:000300.SH"], "timerange": "2026-06-29:2026-07-04"},
        )

        self.assertGreaterEqual(result.fetched_requests, 3)
        self.assertGreater(
            DataLoader(self.root).dataset("findata-plugins/tushare_trade_cal").query().num_rows,
            0,
        )
        weights = (
            DataLoader(self.root)
            .dataset("findata-plugins/tushare_index_weight")
            .query(
                keys=["000300.SH"],
                time_range=("2026-06-01", "2026-08-01"),
                require_coverage=True,
            )
        )
        self.assertEqual(weights.num_rows, 6)
        daily = (
            DataLoader(self.root)
            .dataset("findata-plugins/tushare_daily_basic")
            .query(
                keys=["000001.SZ", "600000.SH", "600519.SH"],
                time_range=("2026-06-29", "2026-07-04"),
                require_coverage=True,
            )
        )
        self.assertEqual(
            set(daily.column("ts_code").to_pylist()), {"000001.SZ", "600000.SH", "600519.SH"}
        )

        request_count = len(self.transport.requests)
        resumed = self.service.run(
            "findata-plugins/tushare_daily_basic",
            "complete",
            {
                "symbols": ["tushare:000300.SH"],
                "timerange": "2026-06-29:2026-07-04",
            },
        )
        self.assertEqual(resumed.fetched_requests, 1)
        self.assertEqual(len(self.transport.requests), request_count + 1)

    def test_constituents_use_dated_snapshots_and_allow_an_empty_current_month(self) -> None:
        transport = DatedSnapshotTransport(today=date(2026, 7, 20))
        service = DatasetService(
            self.workspace,
            TushareClient(token="test-token", transport=transport),
            today=date(2026, 7, 20),
        )

        service.run(
            "findata-plugins/tushare_daily_basic",
            "complete",
            {
                "symbols": ["tushare:000300.SH"],
                "timerange": "2026-06-29:2026-07-04",
            },
        )

        weight_requests = [
            item["params"] for item in transport.requests if item["api_name"] == "index_weight"
        ]
        self.assertEqual(
            [item["start_date"] for item in weight_requests],
            ["20260501", "20260601", "20260701"],
        )
        daily = DataLoader(self.root).dataset("findata-plugins/tushare_daily_basic").query()
        self.assertEqual(
            set(daily.column("ts_code").to_pylist()),
            {"000001.SZ", "600000.SH"},
        )

    def test_current_snapshot_month_is_refetched_after_empty_coverage(self) -> None:
        transport = DatedSnapshotTransport(today=date(2026, 7, 20))
        service = DatasetService(
            self.workspace,
            TushareClient(token="test-token", transport=transport),
            today=date(2026, 7, 20),
        )
        operands = {
            "indexes": ["tushare:000300.SH"],
            "timerange": "2026-06-01:2026-08-01",
        }

        service.run("findata-plugins/tushare_index_weight", "complete", operands)
        transport.july_snapshot = True
        result = service.run("findata-plugins/tushare_index_weight", "complete", operands)

        self.assertEqual(result.fetched_requests, 1)
        stored = DataLoader(self.root).dataset("findata-plugins/tushare_index_weight").query()
        self.assertIn(date(2026, 7, 15), stored.column("trade_date").to_pylist())

    def test_daily_update_uses_plugin_owned_update_symbols(self) -> None:
        self.service.run(
            "findata-plugins/tushare_index_basic", "complete", {"indexes": ["tushare:000300.SH"]}
        )
        self.workspace.set_config(
            "dataset.findata-plugins/tushare_daily_basic.update_symbols",
            ["tushare:000300.SH@latest"],
        )

        result = self.service.run("findata-plugins/tushare_daily_basic", "update", {})

        self.assertGreater(result.fetched_requests, 0)
        status = self.workspace.get_config(
            "dataset.findata-plugins/tushare_daily_basic.update_symbols"
        )
        self.assertEqual(status, ["tushare:000300.SH@latest"])
        coverage = DataLoader(self.root).dataset("findata-plugins/tushare_daily_basic").coverage()
        self.assertEqual(
            set(coverage.column("key").to_pylist()), {"000001.SZ", "600000.SH", "600519.SH"}
        )

    def test_mid_backfill_failure_keeps_checkpoints_and_rerun_fetches_only_missing_work(
        self,
    ) -> None:
        # Calendar (2), metadata (1), three snapshot months (3), first daily
        # symbol (1), then fail on the second daily symbol.
        self.transport.fail_on_call(8, code=-1, message="injected terminal failure")
        operands = {
            "symbols": ["tushare:000300.SH"],
            "timerange": "2026-06-29:2026-07-04",
        }

        with self.assertRaisesRegex(RuntimeError, "injected terminal failure"):
            self.service.run("findata-plugins/tushare_daily_basic", "complete", operands)

        coverage = (
            DataLoader(self.root)
            .dataset("findata-plugins/tushare_daily_basic")
            .coverage()
            .to_pylist()
        )
        self.assertEqual([item["key"] for item in coverage], ["000001.SZ"])
        requests_before_resume = len(self.transport.requests)
        resumed = self.service.run("findata-plugins/tushare_daily_basic", "complete", operands)

        self.assertEqual(resumed.fetched_requests, 3)
        self.assertEqual(len(self.transport.requests) - requests_before_resume, 3)
        self.assertEqual(
            set(
                DataLoader(self.root)
                .dataset("findata-plugins/tushare_daily_basic")
                .coverage()
                .column("key")
                .to_pylist()
            ),
            {"000001.SZ", "600000.SH", "600519.SH"},
        )

    def test_past_daily_empty_is_resolved_but_current_inside_window_empty_is_not(self) -> None:
        self.transport.empty_next("daily_basic")
        past = self.service.run(
            "findata-plugins/tushare_daily_basic",
            "complete",
            {"symbols": ["000001.SZ"], "timerange": "2026-07-18:2026-07-20"},
        )
        self.assertTrue(past.publication_id)
        self.assertEqual(
            DataLoader(self.root).dataset("findata-plugins/tushare_daily_basic").query().num_rows,
            0,
        )

        self.transport.empty_next("daily_basic")
        with self.assertRaisesRegex(RuntimeError, "inside publication window"):
            self.service.run(
                "findata-plugins/tushare_daily_basic",
                "complete",
                {"symbols": ["600000.SH"], "timerange": "2026-07-20:2026-07-21"},
            )

    def test_latest_constituents_use_the_latest_snapshot_as_of_the_target(self) -> None:
        self.service.run(
            "findata-plugins/tushare_index_weight",
            "complete",
            {"indexes": ["tushare:000300.SH"], "timerange": "2026-06-01:2026-07-01"},
        )
        self.transport.empty_next("index_weight")

        result = self.service.run(
            "findata-plugins/tushare_daily_basic",
            "complete",
            {
                "symbols": ["tushare:000300.SH@latest"],
                "timerange": "2026-07-20:2026-07-21",
            },
        )

        self.assertTrue(result.publication_id)
        daily = DataLoader(self.root).dataset("findata-plugins/tushare_daily_basic").query()
        self.assertEqual(
            set(daily.column("ts_code").to_pylist()),
            {"000001.SZ", "600000.SH", "600519.SH"},
        )

    def test_stock_basic_update_accepts_null_market_for_delisted_security(self) -> None:
        self.service.run("findata-plugins/tushare_stock_basic", "update", {})

        table = DataLoader(self.root).dataset("findata-plugins/tushare_stock_basic").query()
        rows = {row["ts_code"]: row for row in table.to_pylist()}
        self.assertIsNone(rows["600001.SH"]["market"])
        self.assertEqual(rows["600001.SH"]["list_status"], "D")

    def test_full_market_per_date_batches_many_symbols_into_one_request(self) -> None:
        self.service.run(
            "findata-plugins/tushare_daily_basic",
            "complete",
            {"symbols": ["000001.SZ", "600000.SH"], "timerange": "2026-07-20:2026-07-21"},
        )

        requests = [
            item["params"] for item in self.transport.requests if item["api_name"] == "daily_basic"
        ]
        self.assertEqual(requests, [{"trade_date": "20260720"}])
        daily = DataLoader(self.root).dataset("findata-plugins/tushare_daily_basic").query()
        # The full-market mock universe includes 600519.SH; it is filtered out before commit.
        self.assertEqual(set(daily.column("ts_code").to_pylist()), {"000001.SZ", "600000.SH"})
        self.assertEqual(daily.column("trade_date").to_pylist(), [date(2026, 7, 20)] * 2)
        coverage = {
            row["key"]: (row["start"], row["end"])
            for row in DataLoader(self.root)
            .dataset("findata-plugins/tushare_daily_basic")
            .coverage()
            .to_pylist()
        }
        self.assertEqual(
            coverage,
            {
                "000001.SZ": (date(2026, 7, 20), date(2026, 7, 21)),
                "600000.SH": (date(2026, 7, 20), date(2026, 7, 21)),
            },
        )

    def test_per_symbol_shape_when_few_symbols_cover_many_dates(self) -> None:
        self.service.run(
            "findata-plugins/tushare_daily_basic",
            "complete",
            {"symbols": ["000001.SZ"], "timerange": "2026-07-13:2026-07-21"},
        )

        requests = [
            item["params"] for item in self.transport.requests if item["api_name"] == "daily_basic"
        ]
        self.assertEqual(
            requests,
            [{"ts_code": "000001.SZ", "start_date": "20260713", "end_date": "20260720"}],
        )

    def test_full_market_row_limit_falls_back_to_per_symbol_for_that_date(self) -> None:
        transport = SaturatedMarketTransport(today=date(2026, 7, 20))
        service = DatasetService(
            self.workspace,
            TushareClient(token="test-token", transport=transport),
            today=date(2026, 7, 20),
        )

        service.run(
            "findata-plugins/tushare_daily_basic",
            "complete",
            {"symbols": ["000001.SZ", "600000.SH"], "timerange": "2026-07-20:2026-07-21"},
        )

        requests = [
            item["params"] for item in transport.requests if item["api_name"] == "daily_basic"
        ]
        self.assertEqual(
            requests,
            [
                {"trade_date": "20260720"},
                {"ts_code": "000001.SZ", "start_date": "20260720", "end_date": "20260720"},
                {"ts_code": "600000.SH", "start_date": "20260720", "end_date": "20260720"},
            ],
        )
        daily = DataLoader(self.root).dataset("findata-plugins/tushare_daily_basic").query()
        self.assertEqual(set(daily.column("ts_code").to_pylist()), {"000001.SZ", "600000.SH"})
        self.assertEqual(daily.num_rows, 2)

    def test_full_market_absent_symbol_resolves_empty_only_after_the_window(self) -> None:
        result = self.service.run(
            "findata-plugins/tushare_daily_basic",
            "complete",
            {
                "symbols": ["000001.SZ", "600000.SH", "600519.SH", "999999.SH"],
                "timerange": "2026-07-17:2026-07-18",
            },
        )

        self.assertTrue(result.publication_id)
        requests = [
            item["params"] for item in self.transport.requests if item["api_name"] == "daily_basic"
        ]
        self.assertEqual(requests, [{"trade_date": "20260717"}])
        daily = DataLoader(self.root).dataset("findata-plugins/tushare_daily_basic").query()
        self.assertEqual(
            set(daily.column("ts_code").to_pylist()),
            {"000001.SZ", "600000.SH", "600519.SH"},
        )
        coverage = (
            DataLoader(self.root)
            .dataset("findata-plugins/tushare_daily_basic")
            .coverage()
            .to_pylist()
        )
        resolved = {row["key"] for row in coverage}
        self.assertIn("999999.SH", resolved)

    def test_inside_window_empty_full_market_response_fails_unresolved(self) -> None:
        self.transport.empty_next("daily_basic")

        with self.assertRaisesRegex(RuntimeError, "inside publication window"):
            self.service.run(
                "findata-plugins/tushare_daily_basic",
                "complete",
                {
                    "symbols": ["000001.SZ", "600000.SH"],
                    "timerange": "2026-07-20:2026-07-21",
                },
            )
        requests = [
            item["params"] for item in self.transport.requests if item["api_name"] == "daily_basic"
        ]
        self.assertEqual(requests, [{"trade_date": "20260720"}])

    def test_complete_clamps_tail_to_due_boundary_and_update_fetches_newly_due_date(self) -> None:
        symbols = ["000001.SZ", "600000.SH", "600519.SH"]
        self.service.run(
            "findata-plugins/tushare_daily_basic",
            "complete",
            {"symbols": symbols, "timerange": "2026-07-17:2026-07-25"},
        )

        requests = [
            item["params"] for item in self.transport.requests if item["api_name"] == "daily_basic"
        ]
        self.assertEqual(requests, [{"trade_date": "20260717"}, {"trade_date": "20260720"}])
        coverage = {
            row["key"]: (row["start"], row["end"])
            for row in DataLoader(self.root)
            .dataset("findata-plugins/tushare_daily_basic")
            .coverage()
            .to_pylist()
        }
        self.assertEqual(
            coverage,
            {symbol: (date(2026, 7, 17), date(2026, 7, 21)) for symbol in symbols},
        )

        next_day = DatasetService(
            self.workspace,
            TushareClient(token="test-token", transport=self.transport),
            today=date(2026, 7, 21),
        )
        self.workspace.set_config(
            "dataset.findata-plugins/tushare_daily_basic.update_symbols", symbols
        )
        next_day.run("findata-plugins/tushare_daily_basic", "update", {})

        requests = [
            item["params"]
            for item in self.transport.requests
            if item["api_name"] == "daily_basic" and item["params"] not in requests
        ]
        # The clamped coverage left 2026-07-21 unresolved, so update fetches it.
        self.assertIn({"trade_date": "20260721"}, requests)
        stored = (
            DataLoader(self.root)
            .dataset("findata-plugins/tushare_daily_basic")
            .query(time_range=("2026-07-21", "2026-07-22"))
        )
        self.assertEqual(stored.num_rows, 3)

    def test_per_symbol_shape_also_clamps_to_the_due_boundary(self) -> None:
        self.service.run(
            "findata-plugins/tushare_daily_basic",
            "complete",
            {"symbols": ["000001.SZ"], "timerange": "2026-07-13:2026-07-25"},
        )

        requests = [
            item["params"] for item in self.transport.requests if item["api_name"] == "daily_basic"
        ]
        self.assertEqual(
            requests,
            [{"ts_code": "000001.SZ", "start_date": "20260713", "end_date": "20260720"}],
        )
        coverage = (
            DataLoader(self.root)
            .dataset("findata-plugins/tushare_daily_basic")
            .coverage()
            .to_pylist()
        )
        self.assertEqual(
            [(row["key"], row["start"], row["end"]) for row in coverage],
            [("000001.SZ", date(2026, 7, 13), date(2026, 7, 21))],
        )

    def test_update_before_publication_window_is_a_noop_on_initialized_dataset(self) -> None:
        published = self.service.run(
            "findata-plugins/tushare_daily_basic",
            "complete",
            {"symbols": ["000001.SZ"], "timerange": "2026-07-17:2026-07-18"},
        )
        self.workspace.set_config(
            "dataset.findata-plugins/tushare_daily_basic.update_symbols", ["000001.SZ"]
        )
        morning = DatasetService(
            self.workspace,
            TushareClient(token="test-token", transport=self.transport),
            today=date(2026, 7, 20),
            now=datetime(2026, 7, 20, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        request_count = len(self.transport.requests)

        result = morning.run("findata-plugins/tushare_daily_basic", "update", {})

        self.assertEqual(result.fetched_requests, 0)
        self.assertEqual(result.publication_id, published.publication_id)
        self.assertEqual(len(self.transport.requests), request_count)

    def test_update_before_publication_window_on_uninitialized_dataset_fails(self) -> None:
        self.workspace.set_config(
            "dataset.findata-plugins/tushare_daily_basic.update_symbols", ["000001.SZ"]
        )
        morning = DatasetService(
            self.workspace,
            TushareClient(token="test-token", transport=self.transport),
            today=date(2026, 7, 20),
            now=datetime(2026, 7, 20, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

        with self.assertRaisesRegex(OperandError, "uninitialized"):
            morning.run("findata-plugins/tushare_daily_basic", "update", {})

    def test_complete_with_entirely_future_range_raises(self) -> None:
        with self.assertRaisesRegex(OperandError, "before the publication window"):
            self.service.run(
                "findata-plugins/tushare_daily_basic",
                "complete",
                {"symbols": ["000001.SZ"], "timerange": "2026-07-21:2026-07-22"},
            )
        self.assertEqual(self.transport.requests, [])


if __name__ == "__main__":
    unittest.main()
