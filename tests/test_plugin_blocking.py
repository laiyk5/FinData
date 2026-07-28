from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from findata.sdk.plugins import (
    apply_plugin_blocklist,
    discover_dataset_plugins,
    discover_provider_plugins,
    plugin_blocklist,
)
from findata.server.server import initialize_workspace
from findata.storage import Workspace
from findata_plugins.tushare.plugins.datasets.stock.daily_basic import daily_basic_plugin
from findata_plugins.tushare.plugins.datasets.index.index_basic import index_basic_plugin
from findata_plugins.tushare.plugins.datasets.index.index_weight import index_weight_plugin
from findata_plugins.tushare.plugins.providers.tushare.provider import tushare_provider_plugin
from findata_plugins.tushare.plugins.datasets.stock.stock_basic import stock_basic_plugin
from findata_plugins.tushare.plugins.datasets.stock.trade_cal import trade_cal_plugin


def builtin_plugins():
    return [
        trade_cal_plugin(),
        stock_basic_plugin(),
        index_basic_plugin(),
        index_weight_plugin(),
        daily_basic_plugin(),
    ]


class PluginBlocklistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.workspace = Workspace.init(Path(self.tempdir.name))
        self.plugins = builtin_plugins()
        self.providers = [tushare_provider_plugin()]

    def block(self, *names: str) -> tuple[list[object], list[object]]:
        warnings: list[str] = []
        active = apply_plugin_blocklist(self.plugins, self.providers, names, warn=warnings.append)
        self.warnings = warnings
        return active

    def test_unrequired_dataset_block_sticks(self) -> None:
        plugins, providers = self.block("findata-plugins/tushare_stock_basic")
        self.assertEqual(len(plugins), 4)
        self.assertNotIn("findata-plugins/tushare_stock_basic", {plugin.name for plugin in plugins})
        self.assertEqual(providers, self.providers)
        self.assertEqual(self.warnings, [])

    def test_blocked_dependency_mounts_anyway_with_warning(self) -> None:
        plugins, _ = self.block("findata-plugins/tushare_trade_cal")
        self.assertEqual(len(plugins), 5)
        self.assertTrue(
            any("ineffective" in message for message in self.warnings),
            self.warnings,
        )

    def test_dependency_closure_repairs_transitively(self) -> None:
        plugins, _ = self.block(
            "findata-plugins/tushare_index_basic", "findata-plugins/tushare_index_weight"
        )
        self.assertEqual(len(plugins), 5)
        self.assertEqual(
            sum("ineffective" in message for message in self.warnings),
            2,
        )

    def test_blocked_provider_mounts_when_datasets_require_it(self) -> None:
        plugins, providers = self.block("findata-plugins/tushare")
        # Datasets still mount, so the provider block is ineffective.
        self.assertEqual(len(plugins), 5)
        self.assertEqual(providers, self.providers)
        self.assertTrue(any("ineffective" in message for message in self.warnings))

    def test_provider_block_sticks_when_no_dataset_mounts(self) -> None:
        all_datasets = [plugin.name for plugin in self.plugins]
        plugins, providers = self.block("findata-plugins/tushare", *all_datasets)
        self.assertEqual(plugins, [])
        self.assertEqual(providers, [])

    def test_unknown_entries_warn_and_change_nothing(self) -> None:
        plugins, providers = self.block("nobody/no_such_dataset")
        self.assertEqual(len(plugins), 5)
        self.assertEqual(providers, self.providers)
        self.assertTrue(any("matches no installed plugin" in m for m in self.warnings))

    def test_malformed_config_value_reads_as_empty(self) -> None:
        self.workspace.set_config("plugins.blocked", "not-a-list")
        with self.assertLogs("findata.sdk.plugins", level="WARNING"):
            self.assertEqual(plugin_blocklist(self.workspace), [])

    def test_workspace_initialization_honors_the_blocklist(self) -> None:
        self.workspace.set_config("plugins.blocked", ["findata-plugins/tushare_stock_basic"])
        initialize_workspace(self.workspace.root)
        registered = {
            str(path.parent.relative_to(self.workspace.datasets_root))
            for path in self.workspace.datasets_root.rglob("dataset.duckdb")
        }
        self.assertNotIn("findata-plugins/tushare_stock_basic", registered)
        # All unblocked datasets are still registered.
        for name in {"findata-plugins/tushare_trade_cal", "findata-plugins/tushare_index_basic",
                     "findata-plugins/tushare_index_weight", "findata-plugins/tushare_daily_basic",
                     "findata-test/demo_hello", "findata-test/demo_random"}:
            self.assertIn(name, registered, f"{name} should still be registered")

    def test_discovery_path_applies_the_same_filter_as_registration(self) -> None:
        self.workspace.set_config("plugins.blocked", ["findata-plugins/tushare_stock_basic"])
        initialize_workspace(self.workspace.root)
        providers = discover_provider_plugins()
        plugins = discover_dataset_plugins(providers=providers)
        active, _ = apply_plugin_blocklist(
            plugins,
            providers,
            plugin_blocklist(self.workspace),
            warn=None,
        )
        self.assertNotIn("findata-plugins/tushare_stock_basic", {plugin.name for plugin in active})


if __name__ == "__main__":
    unittest.main()
