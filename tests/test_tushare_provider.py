from __future__ import annotations

import unittest
from collections.abc import Mapping
from datetime import date
from typing import Any

import pyarrow as pa
from urllib.error import URLError

from findata_plugins.plugins.datasets.tushare_daily_basic import DAILY_BASIC_SPEC
from findata_plugins.plugins.datasets.tushare_index_basic import INDEX_BASIC_SPEC
from findata_plugins.plugins.datasets.tushare_index_weight import INDEX_WEIGHT_SPEC
from findata_plugins.shared.engine import (
    ProviderProtocolError,
    TushareAPIError,
    TushareClient,
)
from findata_plugins.shared.testing import MockTushareTransport
from findata_plugins.plugins.datasets.tushare_stock_basic import STOCK_BASIC_SPEC
from findata_plugins.plugins.datasets.tushare_trade_cal import TRADE_CAL_SPEC

TUSHARE_DATASETS = {
    spec.name: spec
    for spec in (
        TRADE_CAL_SPEC,
        STOCK_BASIC_SPEC,
        INDEX_BASIC_SPEC,
        INDEX_WEIGHT_SPEC,
        DAILY_BASIC_SPEC,
    )
}


class TushareClientTests(unittest.TestCase):
    def test_rate_permit_is_acquired_before_transport(self) -> None:
        calls: list[str] = []

        def permit() -> None:
            calls.append("permit")

        def transport(payload: Mapping[str, Any]) -> Mapping[str, Any]:
            calls.append("transport")
            return MockTushareTransport(today=date(2026, 7, 20))(payload)

        client = TushareClient(token="secret", transport=transport, permit=permit)
        client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_trade_cal"],
            exchange="SSE",
            start_date="20260720",
            end_date="20260720",
        )
        self.assertEqual(calls, ["permit", "transport"])

    def test_transient_transport_failure_retries_with_a_new_rate_permit(self) -> None:
        attempts = 0
        permits = 0

        def permit() -> None:
            nonlocal permits
            permits += 1

        def transport(payload: Mapping[str, Any]) -> Mapping[str, Any]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise URLError("temporary")
            return MockTushareTransport(today=date(2026, 7, 20))(payload)

        client = TushareClient(
            token="secret",
            transport=transport,
            permit=permit,
            max_attempts=2,
            retry_delay=0,
        )
        table = client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_trade_cal"],
            exchange="SSE",
            start_date="20260720",
            end_date="20260720",
        )
        self.assertEqual(table.num_rows, 1)
        self.assertEqual((attempts, permits), (2, 2))

    def test_mock_can_inject_legitimate_empty_response_for_one_api(self) -> None:
        transport = MockTushareTransport(today=date(2026, 7, 20))
        transport.empty_next("daily_basic")
        client = TushareClient(token="secret", transport=transport)
        table = client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_daily_basic"],
            ts_code="000001.SZ",
            start_date="20260717",
            end_date="20260717",
        )
        self.assertEqual(table.num_rows, 0)

    def setUp(self) -> None:
        self.transport = MockTushareTransport(today=date(2026, 7, 20))
        self.client = TushareClient(token="test-token", transport=self.transport)

    def test_builds_official_envelope_without_exposing_token(self) -> None:
        table = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_trade_cal"],
            exchange="SSE",
            start_date="20260717",
            end_date="20260720",
        )

        request = self.transport.requests[0]
        self.assertEqual(request["api_name"], "trade_cal")
        self.assertEqual(request["token"], "test-token")
        self.assertEqual(
            request["params"],
            {"exchange": "SSE", "start_date": "20260717", "end_date": "20260720"},
        )
        self.assertEqual(request["fields"], "exchange,cal_date,is_open,pretrade_date")
        self.assertNotIn("test-token", repr(self.client))
        self.assertEqual(table.schema.field("cal_date").type, pa.date32())
        self.assertEqual(table.column("is_open").to_pylist(), [True, False, False, True])

    def test_stock_basic_mock_filters_status_and_exchange(self) -> None:
        table = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"],
            list_status="L",
            exchange="SSE",
        )

        self.assertGreater(table.num_rows, 0)
        self.assertEqual(set(table.column("list_status").to_pylist()), {"L"})
        self.assertEqual(set(table.column("exchange").to_pylist()), {"SSE"})
        self.assertEqual(table.schema.field("list_date").type, pa.date32())

    def test_index_weight_mock_is_monthly_and_adds_effective_month(self) -> None:
        table = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_index_weight"],
            index_code="000300.SH",
            start_date="20260601",
            end_date="20260630",
        )

        self.assertEqual(table.num_rows, 3)
        self.assertEqual(
            set(table.column("effective_month").to_pylist()),
            {date(2026, 6, 1)},
        )
        self.assertEqual(sum(table.column("weight").to_pylist()), 100.0)

    def test_daily_basic_mock_is_deterministic_and_nullable(self) -> None:
        first = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_daily_basic"],
            ts_code="000001.SZ",
            start_date="20260717",
            end_date="20260720",
        )
        second = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_daily_basic"],
            ts_code="000001.SZ",
            start_date="20260717",
            end_date="20260720",
        )

        self.assertEqual(first.to_pylist(), second.to_pylist())
        self.assertEqual(first.num_rows, 2)
        self.assertEqual(first.schema.field("limit_status").type, pa.int8())

    def test_provider_error_envelope_raises_sanitized_exception(self) -> None:
        self.transport.fail_next(code=2002, message="no permission for test-token")

        with self.assertRaises(TushareAPIError) as caught:
            self.client.query(
                TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"],
                list_status="L",
                exchange="SSE",
            )

        self.assertEqual(caught.exception.code, 2002)
        self.assertNotIn("test-token", str(caught.exception))

    def test_missing_required_response_field_is_protocol_error(self) -> None:
        self.transport.drop_field_next("cal_date")

        with self.assertRaises(ProviderProtocolError):
            self.client.query(
                TUSHARE_DATASETS["findata-plugins/tushare_trade_cal"],
                exchange="SSE",
                start_date="20260720",
                end_date="20260720",
            )

    def test_unknown_dataset_is_rejected_before_transport(self) -> None:
        with self.assertRaises(KeyError):
            self.client.query(TUSHARE_DATASETS["not_registered"])

        self.assertEqual(self.transport.requests, [])


if __name__ == "__main__":
    unittest.main()
