from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from datetime import date

from findata import DataLoader
from findata.sdk.plugins import register_plugins
from findata.storage import Workspace
from findata_plugins.tushare.plugins.datasets.stock.daily_basic import (
    DAILY_BASIC_SPEC,
    daily_basic_plugin,
)
from findata_plugins.tushare.plugins.datasets.index.index_basic import (
    INDEX_BASIC_SPEC,
    index_basic_plugin,
)
from findata_plugins.tushare.plugins.datasets.index.index_basic.operations import IndexBasicDatasetService
from findata_plugins.tushare.plugins.datasets.index.index_weight import (
    INDEX_WEIGHT_SPEC,
    index_weight_plugin,
)
from findata_plugins.tushare.plugins.datasets.index.index_weight.operations import (
    IndexWeightDatasetService,
)
from findata_plugins.tushare.plugins.providers.tushare.provider import tushare_provider_plugin
from findata_plugins.tushare.shared.engine import TushareClient, TushareHTTPTransport
from findata_plugins.tushare.plugins.datasets.stock.stock_basic import (
    STOCK_BASIC_SPEC,
    stock_basic_plugin,
)
from findata_plugins.tushare.plugins.datasets.stock.trade_cal import TRADE_CAL_SPEC, trade_cal_plugin
from findata_plugins.tushare.plugins.datasets.stock.trade_cal.operations import TradeCalDatasetService

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


@unittest.skipUnless(
    os.environ.get("FINDATA_ALLOW_REAL_API") == "1",
    "real API tests require explicit human approval via FINDATA_ALLOW_REAL_API=1",
)
class RealTushareContractTests(unittest.TestCase):
    def client(self) -> TushareClient:
        token = os.environ.get("TUSHARE_API_TOKEN") or os.environ.get("TUSHARE_API_KEY")
        self.assertTrue(
            token,
            "TUSHARE_API_TOKEN or TUSHARE_API_KEY is required after human approval",
        )
        return TushareClient(
            token=token or "",
            transport=TushareHTTPTransport(),
            permit=lambda: time.sleep(0.25),
            max_attempts=1,
        )

    def test_00_trade_calendar_contract_canary(self) -> None:
        client = self.client()

        table = client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_trade_cal"],
            exchange="SSE",
            start_date="20260720",
            end_date="20260720",
        )

        self.assertEqual(table.schema.names, ["exchange", "cal_date", "is_open", "pretrade_date"])
        self.assertEqual(table.num_rows, 1)

    def test_10_stock_basic_exact_code_contract(self) -> None:
        table = self.client().query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"], ts_code="600000.SH"
        )

        self.assertEqual(table.num_rows, 1)
        self.assertEqual(table.column("ts_code").to_pylist(), ["600000.SH"])

    def test_20_index_basic_exact_code_contract(self) -> None:
        table = self.client().query(
            TUSHARE_DATASETS["findata-plugins/tushare_index_basic"], ts_code="000300.SH"
        )

        self.assertEqual(table.num_rows, 1)
        self.assertEqual(table.column("ts_code").to_pylist(), ["000300.SH"])
        self.assertIn("name", table.schema.names)

    def test_30_index_weight_month_contract(self) -> None:
        table = self.client().query(
            TUSHARE_DATASETS["findata-plugins/tushare_index_weight"],
            index_code="000300.SH",
            start_date="20260601",
            end_date="20260630",
        )

        self.assertGreater(table.num_rows, 0)
        self.assertEqual(set(table.column("index_code").to_pylist()), {"000300.SH"})
        self.assertEqual(set(table.column("effective_month").to_pylist()), {date(2026, 6, 1)})

    def test_40_daily_basic_exact_symbol_contract(self) -> None:
        table = self.client().query(
            TUSHARE_DATASETS["findata-plugins/tushare_daily_basic"],
            ts_code="600000.SH",
            start_date="20260630",
            end_date="20260630",
        )

        self.assertEqual(table.num_rows, 1)
        self.assertEqual(table.column("ts_code").to_pylist(), ["600000.SH"])
        self.assertEqual(table.column("trade_date").to_pylist(), [date(2026, 6, 30)])

    def test_90_bounded_plugin_storage_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Workspace.init(root)
            register_v1_datasets(workspace)
            client = self.client()
            today = date(2026, 7, 22)

            calendar = TradeCalDatasetService(workspace, client, today=today).run(
                "complete",
                {"exchanges": ["SSE", "SZSE"], "timerange": "2026-06-30:2026-07-01"},
            )
            index = IndexBasicDatasetService(workspace, client, today=today).run(
                "complete",
                {"indexes": ["tushare:000300.SH"]},
            )
            weights = IndexWeightDatasetService(workspace, client, today=today).run(
                "complete",
                {
                    "indexes": ["tushare:000300.SH"],
                    "timerange": "2026-06-01:2026-07-01",
                },
            )

            self.assertEqual(calendar.fetched_requests, 2)
            self.assertEqual(index.fetched_requests, 1)
            self.assertEqual(weights.fetched_requests, 1)
            stored = (
                DataLoader(root)
                .dataset("findata-plugins/tushare_index_weight")
                .query(
                    keys=["000300.SH"],
                    time_range=("2026-06-01", "2026-07-01"),
                    require_coverage=True,
                )
            )
            self.assertGreater(stored.num_rows, 0)


if __name__ == "__main__":
    unittest.main()
