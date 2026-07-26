from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from findata_tushare.datasets import builtin_plugins
from findata_tushare.datasets.operations import DatasetService, register_v1_datasets
from findata_tushare.provider import TushareClient
from findata.storage import Workspace
from findata_tushare.testing import MockTushareTransport


class PluginSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.workspace = Workspace.init(self.root)
        register_v1_datasets(self.workspace)
        self.transport = MockTushareTransport(today=date(2026, 7, 20))
        self.service = DatasetService(
            self.workspace,
            TushareClient(token="mock", transport=self.transport),
            today=date(2026, 7, 20),
        )

    def test_config_has_version_revision_and_atomic_typed_values(self) -> None:
        initial = self.workspace.config_snapshot()
        self.assertEqual(initial, {"config_version": 1, "revision": 0, "values": {}})
        self.workspace.set_config("display.timezone", "UTC")
        updated = self.workspace.config_snapshot()
        self.assertEqual(updated["revision"], 1)
        self.assertEqual(updated["values"]["display.timezone"], "UTC")

    def test_dataset_setting_is_plugin_normalized_and_requires_local_metadata(self) -> None:
        plugin = next(item for item in builtin_plugins() if item.name == "tushare_daily_basic")
        key = "dataset.tushare_daily_basic.update_symbols"
        with self.assertRaisesRegex(ValueError, "tushare_index_basic complete"):
            plugin.normalize_setting(key, ["tushare:000300.SH@latest"], workspace=self.workspace)

        self.service.run("tushare_index_basic", "complete", {"indexes": ["tushare:000300.SH"]})
        normalized = plugin.normalize_setting(
            key,
            ["tushare:000300.SH@latest", "000001.SZ", "000001.SZ"],
            workspace=self.workspace,
        )
        self.assertEqual(normalized, ["000001.SZ", "tushare:000300.SH@latest"])

    def test_index_metadata_is_materialized_one_reference_at_a_time(self) -> None:
        first = self.service.run(
            "tushare_index_basic", "complete", {"indexes": ["tushare:000300.SH"]}
        )
        self.assertEqual(first.fetched_requests, 1)
        request = self.transport.requests[-1]["params"]
        self.assertEqual(request, {"ts_code": "000300.SH"})
        table = self.service.loader.dataset("tushare_index_basic").query()
        self.assertEqual(table.column("ts_code").to_pylist(), ["000300.SH"])

        self.service.run("tushare_index_basic", "complete", {"indexes": ["tushare:000905.SH"]})
        table = self.service.loader.dataset("tushare_index_basic").query()
        self.assertEqual(set(table.column("ts_code").to_pylist()), {"000300.SH", "000905.SH"})
        self.assertTrue(all("market" not in item["params"] for item in self.transport.requests))

    def test_config_file_never_contains_a_universes_map(self) -> None:
        content = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertNotIn("universes", content)


if __name__ == "__main__":
    unittest.main()
