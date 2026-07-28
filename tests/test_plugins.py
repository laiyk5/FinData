from __future__ import annotations

import multiprocessing as mp
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from findata.sdk.contracts import OperationReporter, OperationRequest
from findata.sdk.plugins import (
    DatasetRuntime,
    PluginRegistrationError,
    ProviderPlugin,
    ProviderRuntime,
    register_plugins,
    validate_plugins,
    validate_provider_plugins,
)
from findata.storage import Workspace
from findata.server.taskrunner import TaskContext
from findata_plugins.tushare.plugins.datasets.stock.daily_basic import daily_basic_plugin
from findata_plugins.tushare.plugins.datasets.stock.daily_basic.operations import DailyBasicDatasetRuntime
from findata_plugins.tushare.plugins.datasets.index.index_basic import index_basic_plugin
from findata_plugins.tushare.plugins.datasets.index.index_basic.operations import IndexBasicDatasetRuntime
from findata_plugins.tushare.plugins.datasets.index.index_weight import index_weight_plugin
from findata_plugins.tushare.plugins.datasets.index.index_weight.operations import (
    IndexWeightDatasetRuntime,
)
from findata_plugins.tushare.plugins.providers.tushare.provider import (
    TushareProviderRuntime,
    tushare_provider_plugin,
)
from findata_plugins.tushare.plugins.datasets.stock.stock_basic import stock_basic_plugin
from findata_plugins.tushare.plugins.datasets.stock.stock_basic.operations import StockBasicDatasetRuntime
from findata_plugins.tushare.plugins.datasets.stock.trade_cal import trade_cal_plugin
from findata_plugins.tushare.plugins.datasets.stock.trade_cal.operations import TradeCalDatasetRuntime


def tushare_dataset_plugins():
    return [
        trade_cal_plugin(),
        stock_basic_plugin(),
        index_basic_plugin(),
        index_weight_plugin(),
        daily_basic_plugin(),
    ]


class PluginRegistryTests(unittest.TestCase):
    def test_builtin_plugins_validate_and_register_all_v1_datasets(self) -> None:
        plugins = tushare_dataset_plugins()
        self.assertEqual(len(plugins), 5)
        validate_plugins(plugins, providers=[tushare_provider_plugin()])
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace.init(Path(directory))
            register_plugins(workspace, plugins, providers=[tushare_provider_plugin()])
            self.assertEqual(
                {
                    str(path.parent.relative_to(workspace.datasets_root))
                    for path in workspace.datasets_root.rglob("dataset.duckdb")
                },
                {plugin.name for plugin in plugins},
            )

    def test_plugin_families_follow_the_published_namespace_taxonomy(self) -> None:
        self.assertEqual(tushare_provider_plugin().family, ("tushare",))
        self.assertEqual(trade_cal_plugin().family, ("tushare", "stock"))
        self.assertEqual(stock_basic_plugin().family, ("tushare", "stock"))
        self.assertEqual(daily_basic_plugin().family, ("tushare", "stock"))
        self.assertEqual(index_basic_plugin().family, ("tushare", "index"))
        self.assertEqual(index_weight_plugin().family, ("tushare", "index"))

    def test_registration_rejects_missing_update_and_dependency_cycles(self) -> None:
        first, second, *_ = tushare_dataset_plugins()
        without_update = replace(first, operations=("complete",))
        with self.assertRaisesRegex(PluginRegistrationError, "parameterless update"):
            validate_plugins([without_update], providers=[tushare_provider_plugin()])

        cyclic_first = replace(first, dependencies=(second.name,))
        cyclic_second = replace(second, dependencies=(first.name,))
        with self.assertRaisesRegex(PluginRegistrationError, "dependency cycle"):
            validate_plugins([cyclic_first, cyclic_second], providers=[tushare_provider_plugin()])

    def test_provider_contracts_are_registered_before_datasets(self) -> None:
        provider = tushare_provider_plugin()
        validate_provider_plugins([provider])
        with self.assertRaisesRegex(PluginRegistrationError, "duplicate provider"):
            validate_provider_plugins([provider, provider])
        malformed = ProviderPlugin(
            provider_id="broken/provider",
            configuration_schema={},
            secret_fields=(),
            rate_limit=0,
            period=60,
        )
        with self.assertRaisesRegex(PluginRegistrationError, "rate limit"):
            validate_provider_plugins([malformed])
        with self.assertRaisesRegex(PluginRegistrationError, "unknown provider"):
            validate_plugins(tushare_dataset_plugins(), providers=[])

    def test_core_and_toolkit_import_boundaries_are_enforced(self) -> None:
        root = Path(__file__).parents[1]
        package = root / "src" / "findata"
        forbidden = (
            "from findata_plugins",
            "import findata_plugins",
            "from findata.toolkit",
        )
        for path in package.glob("*.py"):
            content = path.read_text(encoding="utf-8")
            self.assertFalse(
                any(item in content for item in forbidden),
                f"core module imports a concrete or optional package: {path}",
            )
        for path in (package / "toolkit").glob("*.py"):
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("findata_plugins", content)

    def test_plugin_distributions_never_import_another_plugin(self) -> None:
        # Shared may import only findata.* and third-party libraries; the
        # provider and dataset leaves may additionally import
        # findata_plugins.tushare.shared. No leaf may import another leaf.
        shared = "findata_plugins.tushare.shared"
        provider_leaf = "findata_plugins.tushare.plugins.providers.tushare"
        dataset_leaves = {
            "findata_plugins.tushare.plugins.datasets.stock.trade_cal",
            "findata_plugins.tushare.plugins.datasets.stock.stock_basic",
            "findata_plugins.tushare.plugins.datasets.stock.daily_basic",
            "findata_plugins.tushare.plugins.datasets.etf.fund_daily",
            "findata_plugins.tushare.plugins.datasets.index.index_basic",
            "findata_plugins.tushare.plugins.datasets.index.index_weight",
        }
        retired_core_paths = (
            "from findata.datasets.",
            "from findata.providers.",
            "from findata.testing.",
        )
        plugin_root = Path(__file__).parents[1] / "plugins" / "tushare"
        leaves: dict[str, Path] = {}
        for init in plugin_root.glob("*/src/findata_plugins/tushare/**/__init__.py"):
            package = init.parent
            src = next(parent for parent in package.parents if parent.name == "src")
            leaves[str(package.relative_to(src)).replace("/", ".")] = package
        self.assertEqual(set(leaves), {shared, provider_leaf} | dataset_leaves)
        for name, package in leaves.items():
            if name == shared:
                forbidden = ("findata_plugins.tushare.plugins",)
            elif name == provider_leaf:
                forbidden = ("findata_plugins.tushare.plugins.datasets",)
            else:
                forbidden = ("findata_plugins.tushare.plugins.providers",) + tuple(
                    sorted(dataset_leaves - {name})
                )
            for path in package.rglob("*.py"):
                content = path.read_text(encoding="utf-8")
                self.assertFalse(
                    any(item in content for item in retired_core_paths),
                    f"plugin module imports a retired core plugin path: {path}",
                )
                for other in forbidden:
                    self.assertNotIn(
                        other,
                        content,
                        f"plugin module imports another plugin distribution: {path}",
                    )


class PluginProtocolConformanceTests(unittest.TestCase):
    def test_tushare_runtime_satisfies_the_provider_runtime_protocol(self) -> None:
        self.assertIsInstance(TushareProviderRuntime(), ProviderRuntime)

    def test_tushare_dataset_runtimes_satisfy_the_dataset_runtime_protocol(self) -> None:
        for runtime in (
            TradeCalDatasetRuntime(),
            StockBasicDatasetRuntime(),
            IndexBasicDatasetRuntime(),
            IndexWeightDatasetRuntime(),
            DailyBasicDatasetRuntime(),
        ):
            self.assertIsInstance(runtime, DatasetRuntime)

    def test_provider_validation_rejects_an_incomplete_runtime(self) -> None:
        incomplete = replace(tushare_provider_plugin(), runtime=object())
        with self.assertRaisesRegex(PluginRegistrationError, "readiness runtime"):
            validate_provider_plugins([incomplete])

    def test_core_task_context_satisfies_the_operation_reporter_protocol(self) -> None:
        local, remote = mp.Pipe(duplex=True)
        try:
            context = TaskContext(local, threading.Event())
            self.assertIsInstance(context, OperationReporter)
        finally:
            local.close()
            remote.close()

    def test_operation_request_documents_the_taskrunner_payload(self) -> None:
        # TaskRunner._run_execution builds exactly these keys for the worker.
        self.assertEqual(
            set(OperationRequest.__annotations__),
            {
                "execution_id",
                "dataset",
                "operation",
                "operands",
                "configuration_revision",
                "settings",
            },
        )


if __name__ == "__main__":
    unittest.main()
