"""Tests for the findata-plugins/tushare_fund_daily dataset plugin."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any

from findata.sdk.contracts import OperandError
from findata.sdk.plugins import register_plugins
from findata.storage import Workspace
from findata_plugins.tushare.plugins.datasets.etf.fund_daily import (
    FUND_DAILY_FIELDS,
    FUND_DAILY_SPEC,
    fund_daily_plugin,
)
from findata_plugins.tushare.plugins.datasets.etf.fund_daily import FUND_DAILY_FLOAT_FIELDS
from findata_plugins.tushare.plugins.providers.tushare.provider import tushare_provider_plugin
from findata_plugins.tushare.plugins.datasets.etf.fund_daily.operations import (
    FundDailyDatasetRuntime,
    normalize_operation,
)
from findata_plugins.tushare.shared.testing import MockTushareTransport


class FundDailySpecTests(unittest.TestCase):
    def test_spec_has_correct_identity(self) -> None:
        self.assertEqual(FUND_DAILY_SPEC.name, "findata-plugins/tushare_fund_daily")
        self.assertEqual(FUND_DAILY_SPEC.api_name, "fund_daily")
        self.assertEqual(FUND_DAILY_SPEC.primary_key, ("ts_code", "trade_date"))
        self.assertEqual(FUND_DAILY_SPEC.partition_key, "ts_code")
        self.assertEqual(FUND_DAILY_SPEC.time_field, "trade_date")

    def test_spec_fields_match_provider_fields(self) -> None:
        schema_names = set(FUND_DAILY_SPEC.schema.names)
        for field in FUND_DAILY_FIELDS:
            self.assertIn(field, schema_names)

    def test_plugin_returns_valid_plugin(self) -> None:
        plugin = fund_daily_plugin()
        self.assertEqual(plugin.name, FUND_DAILY_SPEC.name)
        self.assertEqual(plugin.provider, "tushare")
        self.assertEqual(plugin.operations, ("update", "complete", "refresh"))


class FundDailyMockOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.workspace = Workspace.init(Path(self.tempdir.name))
        self.plugin = fund_daily_plugin()
        self.runtime = FundDailyDatasetRuntime()
        register_plugins(
            self.workspace,
            [self.plugin],
            providers=[tushare_provider_plugin()],
        )

    def _service(self, transport: Any, *, today: date | None = None) -> Any:
        from findata_plugins.tushare.plugins.datasets.etf.fund_daily.operations import (
            FundDailyDatasetService,
        )
        from findata_plugins.tushare.shared.engine import TushareClient

        today = today or date(2026, 7, 20)
        client = TushareClient(
            token="mock-token", transport=transport, permit=lambda: None, max_attempts=1
        )
        return FundDailyDatasetService(
            self.workspace,
            client,
            today=today,
            now=today,
            reporter=_RecordingReporter(),
            settings={},
        )

    def test_complete_fetches_and_commits_mock_data(self) -> None:
        transport = MockTushareTransport(today=date(2026, 7, 20))
        service = self._service(transport, today=date(2026, 7, 15))
        publication = service.run(
            "complete", {"symbols": ["159919.SZ"], "timerange": "2026-07-01:2026-07-10"}
        )
        self.assertIsNotNone(publication)
        # Verify data was committed by reading it back
        from findata import DataLoader
        table = DataLoader(self.workspace.root).dataset(FUND_DAILY_SPEC.name).query(
            keys=["159919.SZ"],
            time_range=("2026-07-01", "2026-07-10"),
        )
        self.assertGreater(table.num_rows, 0)

    def test_complete_defaults_to_all_funds(self) -> None:
        transport = MockTushareTransport(today=date(2026, 7, 20))
        self._service(transport).run("complete", {"timerange": "2026-07-20:2026-07-21"})

        requests = [item["params"] for item in transport.requests if item["api_name"] == "fund_daily"]
        self.assertEqual(requests, [{"trade_date": "20260720"}])

    def test_update_defaults_to_stored_fund_codes(self) -> None:
        transport = MockTushareTransport(today=date(2026, 7, 20))
        self._service(transport, today=date(2026, 7, 15)).run(
            "complete", {"symbols": ["159919.SZ"], "timerange": "2026-07-01:2026-07-10"}
        )

        self._service(transport).run("update", {})

        requests = [item["params"] for item in transport.requests if item["api_name"] == "fund_daily"]
        self.assertIn(
            {"ts_code": "159919.SZ", "start_date": "20260710", "end_date": "20260720"},
            requests,
        )

    def test_unknown_operation_rejected(self) -> None:
        transport = MockTushareTransport(today=date(2026, 7, 20))
        service = self._service(transport)
        with self.assertRaises(OperandError):
            service.run("nonexistent", {})


class FundDailyNormalizationTests(unittest.TestCase):
    def test_normalize_complete_requires_timerange(self) -> None:
        with self.assertRaises(OperandError):
            normalize_operation("complete", {}, today=date(2026, 7, 20))

    def test_normalize_complete_defaults_symbols_to_all(self) -> None:
        result = normalize_operation(
            "complete",
            {"timerange": "2026-07-01:2026-07-10"},
            today=date(2026, 7, 20),
        )
        self.assertEqual(result["symbols"], ["all"])

    def test_normalize_complete_deduplicates_symbols(self) -> None:
        result = normalize_operation(
            "complete",
            {"symbols": ["A", "B", "A"], "timerange": "2026-07-01:2026-07-10"},
            today=date(2026, 7, 20),
        )
        self.assertEqual(result["symbols"], ["A", "B"])

    def test_normalize_update_rejects_operands(self) -> None:
        with self.assertRaises(OperandError):
            normalize_operation(
                "update", {"symbols": ["A"]}, today=date(2026, 7, 20)
            )

    def test_normalize_unknown_operation_rejected(self) -> None:
        with self.assertRaises(OperandError):
            normalize_operation("nope", {}, today=date(2026, 7, 20))


class MockTushareTransportFundDailyTests(unittest.TestCase):
    def test_mock_returns_fund_daily_rows(self) -> None:
        transport = MockTushareTransport(today=date(2026, 7, 20))
        rows = transport._fund_daily({
            "ts_code": "159919.SZ",
            "start_date": "20260701",
            "end_date": "20260703",
        })
        self.assertEqual(len(rows), 3)  # 3 weekdays
        for row in rows:
            self.assertEqual(row["ts_code"], "159919.SZ")
            for field in FUND_DAILY_FLOAT_FIELDS:
                self.assertIn(field, row)
                self.assertIsInstance(row[field], float)


class _RecordingReporter:
    """Minimal OperationReporter for tests."""

    def checkpoint(self) -> None:
        pass

    def log(self, message: str) -> None:
        pass

    def diagnostic(self, severity: str, code: str, message: str, **kwargs: Any) -> None:
        pass

    def progress(self, current: int | float, total: int | float, **metrics: int | float) -> None:
        pass

    def stage(self, value: str) -> None:
        pass

    def waiting(self, reason: str) -> None:
        pass

    def running(self) -> None:
        pass

    def begin_subtask(self, *, timeout: float) -> None:
        pass

    def end_subtask(self) -> None:
        pass

    def fulfill(self, dataset: str, requirement: Any) -> Any:
        raise RuntimeError(f"no fulfill for {dataset}")


if __name__ == "__main__":
    unittest.main()
