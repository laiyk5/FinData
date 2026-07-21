from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from findata import DataLoader
from findata.datasets.tushare.operations import DatasetService, register_v1_datasets
from findata.providers.tushare import TushareClient
from findata.storage import Workspace
from findata.testing.tushare import MockTushareTransport


class EndpointAliasTransport(MockTushareTransport):
    """Mirror Tushare's distinct CSI 300 code in the index_weight endpoint."""

    def _index_basic(self, params):  # type: ignore[no-untyped-def]
        if params.get("name") == "沪深300":
            canonical = super()._index_basic({"ts_code": "000300.SH"})[0]
            alias = dict(canonical)
            alias["ts_code"] = "399300.SZ"
            return [canonical, alias]
        return super()._index_basic(params)

    def _index_weight(self, params):  # type: ignore[no-untyped-def]
        if params.get("index_code") == "000300.SH":
            return []
        return super()._index_weight(params)


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

    def test_trade_calendar_complete_publishes_and_rerun_skips_coverage(self) -> None:
        first = self.service.run(
            "tushare_trade_cal",
            "complete",
            {"exchanges": ["SSE"], "timerange": "2026-07-17:2026-07-21"},
        )
        request_count = len(self.transport.requests)
        second = self.service.run(
            "tushare_trade_cal",
            "complete",
            {"exchanges": ["SSE"], "timerange": "2026-07-17:2026-07-21"},
        )

        self.assertEqual(first.fetched_requests, 1)
        self.assertEqual(second.fetched_requests, 0)
        self.assertEqual(second.publication_id, first.publication_id)
        self.assertEqual(len(self.transport.requests), request_count)
        table = DataLoader(self.root).dataset("tushare_trade_cal").query(
            keys=["SSE"],
            time_range=("2026-07-17", "2026-07-21"),
            require_coverage=True,
        )
        self.assertEqual(table.num_rows, 4)

    def test_stock_basic_update_merges_all_status_and_exchange_requests(self) -> None:
        result = self.service.run("tushare_stock_basic", "update", {})

        self.assertEqual(result.fetched_requests, 12)
        table = DataLoader(self.root).dataset("tushare_stock_basic").query()
        self.assertEqual(table.num_rows, 5)
        self.assertEqual(set(table.column("list_status").to_pylist()), {"L", "D", "G"})

    def test_primary_story_fulfills_dependencies_and_resumes_without_requests(self) -> None:
        result = self.service.run(
            "tushare_daily_basic",
            "complete",
            {"symbols": ["tushare:000300.SH"], "timerange": "2026-06-29:2026-07-04"},
        )

        self.assertGreaterEqual(result.fetched_requests, 3)
        self.assertGreater(
            DataLoader(self.root).dataset("tushare_trade_cal").query().num_rows,
            0,
        )
        weights = DataLoader(self.root).dataset("tushare_index_weight").query(
            keys=["000300.SH"],
            time_range=("2026-06-01", "2026-08-01"),
            require_coverage=True,
        )
        self.assertEqual(weights.num_rows, 6)
        daily = DataLoader(self.root).dataset("tushare_daily_basic").query(
            keys=["000001.SZ", "600000.SH", "600519.SH"],
            time_range=("2026-06-29", "2026-07-04"),
            require_coverage=True,
        )
        self.assertEqual(set(daily.column("ts_code").to_pylist()), {"000001.SZ", "600000.SH", "600519.SH"})

        request_count = len(self.transport.requests)
        resumed = self.service.run(
            "tushare_daily_basic",
            "complete",
            {
                "symbols": ["tushare:000300.SH"],
                "timerange": "2026-06-29:2026-07-04",
            },
        )
        self.assertEqual(resumed.fetched_requests, 0)
        self.assertEqual(len(self.transport.requests), request_count)

    def test_weight_endpoint_alias_is_resolved_but_storage_stays_canonical(self) -> None:
        transport = EndpointAliasTransport(today=date(2026, 7, 20))
        service = DatasetService(
            self.workspace,
            TushareClient(token="test-token", transport=transport),
            today=date(2026, 7, 20),
        )

        service.run(
            "tushare_daily_basic",
            "complete",
            {
                "symbols": ["tushare:000300.SH"],
                "timerange": "2026-06-29:2026-07-04",
            },
        )

        requests = [(item["api_name"], item["params"]) for item in transport.requests]
        self.assertIn(("index_basic", {"name": "沪深300"}), requests)
        self.assertTrue(
            any(
                api == "index_weight" and params.get("index_code") == "399300.SZ"
                for api, params in requests
            )
        )
        stored = DataLoader(self.root).dataset("tushare_index_weight").query()
        self.assertEqual(set(stored.column("index_code").to_pylist()), {"000300.SH"})

    def test_daily_update_uses_plugin_owned_update_symbols(self) -> None:
        self.service.run(
            "tushare_index_basic", "complete", {"indexes": ["tushare:000300.SH"]}
        )
        self.workspace.set_config(
            "dataset.tushare_daily_basic.update_symbols",
            ["tushare:000300.SH@latest"],
        )

        result = self.service.run("tushare_daily_basic", "update", {})

        self.assertGreater(result.fetched_requests, 0)
        status = self.workspace.get_config("dataset.tushare_daily_basic.update_symbols")
        self.assertEqual(status, ["tushare:000300.SH@latest"])
        coverage = DataLoader(self.root).dataset("tushare_daily_basic").coverage()
        self.assertEqual(set(coverage.column("key").to_pylist()), {"000001.SZ", "600000.SH", "600519.SH"})

    def test_mid_backfill_failure_keeps_checkpoints_and_rerun_fetches_only_missing_work(self) -> None:
        # calendar (2), two index months (2), first daily symbol (1), then fail.
        self.transport.fail_on_call(7, code=-1, message="injected terminal failure")
        operands = {
            "symbols": ["tushare:000300.SH"],
            "timerange": "2026-06-29:2026-07-04",
        }

        with self.assertRaisesRegex(RuntimeError, "injected terminal failure"):
            self.service.run("tushare_daily_basic", "complete", operands)

        coverage = DataLoader(self.root).dataset("tushare_daily_basic").coverage().to_pylist()
        self.assertEqual([item["key"] for item in coverage], ["000001.SZ"])
        requests_before_resume = len(self.transport.requests)
        resumed = self.service.run("tushare_daily_basic", "complete", operands)

        self.assertEqual(resumed.fetched_requests, 2)
        self.assertEqual(len(self.transport.requests) - requests_before_resume, 2)
        self.assertEqual(
            set(DataLoader(self.root).dataset("tushare_daily_basic").coverage().column("key").to_pylist()),
            {"000001.SZ", "600000.SH", "600519.SH"},
        )

    def test_past_daily_empty_is_resolved_but_current_inside_window_empty_is_not(self) -> None:
        self.transport.empty_next("daily_basic")
        past = self.service.run(
            "tushare_daily_basic",
            "complete",
            {"symbols": ["000001.SZ"], "timerange": "2026-07-18:2026-07-20"},
        )
        self.assertTrue(past.publication_id)
        self.assertEqual(
            DataLoader(self.root).dataset("tushare_daily_basic").query().num_rows,
            0,
        )

        self.transport.empty_next("daily_basic")
        with self.assertRaisesRegex(RuntimeError, "inside publication window"):
            self.service.run(
                "tushare_daily_basic",
                "complete",
                {"symbols": ["600000.SH"], "timerange": "2026-07-20:2026-07-21"},
            )

    def test_latest_constituents_never_fall_back_to_older_covered_month(self) -> None:
        self.service.run(
            "tushare_index_weight",
            "complete",
            {"indexes": ["tushare:000300.SH"], "timerange": "2026-06-01:2026-07-01"},
        )
        self.transport.empty_next("index_weight")

        with self.assertRaisesRegex(RuntimeError, "empty historical month"):
            self.service.run(
                "tushare_daily_basic",
                "complete",
                {
                    "symbols": ["tushare:000300.SH@latest"],
                    "timerange": "2026-07-20:2026-07-21",
                },
            )


if __name__ == "__main__":
    unittest.main()
