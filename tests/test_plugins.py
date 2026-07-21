from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from findata.datasets.tushare import builtin_plugins
from findata.plugins import (
    PluginRegistrationError,
    ProviderPlugin,
    register_plugins,
    validate_plugins,
    validate_provider_plugins,
)
from findata.providers.tushare import tushare_provider_plugin
from findata.storage import Workspace


class PluginRegistryTests(unittest.TestCase):
    def test_builtin_plugins_validate_and_register_all_v1_datasets(self) -> None:
        plugins = builtin_plugins()
        self.assertEqual(len(plugins), 5)
        validate_plugins(plugins, providers=[tushare_provider_plugin()])
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace.init(Path(directory))
            register_plugins(workspace, plugins, providers=[tushare_provider_plugin()])
            self.assertEqual(
                {path.name for path in workspace.datasets_root.iterdir()},
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
            validate_plugins(
                [cyclic_first, cyclic_second], providers=[tushare_provider_plugin()]
            )

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
        package = Path(__file__).parents[1] / "src" / "findata"
        forbidden = (
            "from findata.datasets.",
            "from findata.providers.",
            "from findata.testing.",
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
            self.assertNotIn("from findata.datasets.", content)
            self.assertNotIn("from findata.providers.", content)


if __name__ == "__main__":
    unittest.main()
