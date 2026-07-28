"""Tests for the official Tushare index daily plugin."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from findata.sdk.plugins import register_plugins
from findata.storage import Workspace
from findata_plugins.tushare.plugins.datasets.index.index_daily import (
    INDEX_DAILY_SPEC,
    index_daily_plugin,
)
from findata_plugins.tushare.plugins.datasets.index.index_daily.operations import (
    IndexDailyDatasetRuntime,
    normalize_operation,
)
from findata_plugins.tushare.plugins.datasets.index.index_basic import index_basic_plugin
from findata_plugins.tushare.plugins.datasets.stock.trade_cal import trade_cal_plugin
from findata_plugins.tushare.plugins.providers.tushare.provider import tushare_provider_plugin


class IndexDailyPluginTests(unittest.TestCase):
    def test_contract(self) -> None:
        plugin = index_daily_plugin()
        self.assertEqual(INDEX_DAILY_SPEC.api_name, "index_daily")
        self.assertEqual(INDEX_DAILY_SPEC.primary_key, ("ts_code", "trade_date"))
        self.assertEqual(plugin.family, ("tushare", "index"))
        self.assertEqual(plugin.operations, ("update", "complete", "refresh"))

    def test_normalizes_index_range(self) -> None:
        self.assertEqual(
            normalize_operation("complete", {"indexes": ["tushare:000300.SH"], "timerange": "2026-07-01:2026-07-02"}, today=date(2026, 7, 20)),
            {"indexes": ["tushare:000300.SH"], "timerange": "2026-07-01:2026-07-02"},
        )

    def test_registers_with_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace.init(Path(directory))
            register_plugins(workspace, [trade_cal_plugin(), index_basic_plugin(), index_daily_plugin()], providers=[tushare_provider_plugin()])
            self.assertTrue((workspace.root / "datasets" / INDEX_DAILY_SPEC.name).exists())
            self.assertIsInstance(IndexDailyDatasetRuntime(), IndexDailyDatasetRuntime)
