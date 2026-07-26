from __future__ import annotations

import multiprocessing as mp
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from findata.contracts import OperationReporter, OperationRequest
from findata_tushare.datasets import builtin_plugins
from findata.plugins import (
    PluginRegistrationError,
    ProviderPlugin,
    ProviderRuntime,
    register_plugins,
    validate_plugins,
    validate_provider_plugins,
)
from findata_tushare.provider import TushareProviderRuntime, tushare_provider_plugin
from findata.storage import Workspace
from findata.taskrunner import TaskContext


class PluginRegistryTests(unittest.TestCase):
    def test_builtin_plugins_validate_and_register_all_v1_datasets(self) -> None:
        plugins = builtin_plugins()
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

    def test_registration_rejects_missing_update_and_dependency_cycles(self) -> None:
        first, second, *_ = builtin_plugins()
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
            provider_id="broken",
            configuration_schema={},
            secret_fields=(),
            rate_limit=0,
            period=60,
        )
        with self.assertRaisesRegex(PluginRegistrationError, "rate limit"):
            validate_provider_plugins([malformed])
        with self.assertRaisesRegex(PluginRegistrationError, "unknown provider"):
            validate_plugins(builtin_plugins(), providers=[])

    def test_core_and_toolkit_import_boundaries_are_enforced(self) -> None:
        root = Path(__file__).parents[1]
        package = root / "src" / "findata"
        forbidden = (
            "from findata_tushare",
            "import findata_tushare",
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
            self.assertNotIn("findata_tushare", content)

    def test_plugin_distributions_never_import_another_plugin(self) -> None:
        plugin_src = Path(__file__).parents[1] / "plugins" / "tushare" / "src"
        forbidden = (
            "from findata.datasets.",
            "from findata.providers.",
            "from findata.testing.",
        )
        for path in plugin_src.rglob("*.py"):
            content = path.read_text(encoding="utf-8")
            self.assertFalse(
                any(item in content for item in forbidden),
                f"plugin module imports a retired core plugin path or another plugin: {path}",
            )


class PluginProtocolConformanceTests(unittest.TestCase):
    def test_tushare_runtime_satisfies_the_provider_runtime_protocol(self) -> None:
        self.assertIsInstance(TushareProviderRuntime(), ProviderRuntime)

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
