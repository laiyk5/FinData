from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from findata.datasets.tushare import builtin_plugins
from findata.plugins import PluginRegistrationError, register_plugins, validate_plugins
from findata.storage import Workspace


class PluginRegistryTests(unittest.TestCase):
    def test_builtin_plugins_validate_and_register_all_v1_datasets(self) -> None:
        plugins = builtin_plugins()
        self.assertEqual(len(plugins), 4)
        validate_plugins(plugins)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace.init(Path(directory))
            register_plugins(workspace, plugins)
            self.assertEqual(
                {path.name for path in workspace.datasets_root.iterdir()},
                {plugin.name for plugin in plugins},
            )

    def test_registration_rejects_missing_update_and_dependency_cycles(self) -> None:
        first, second, *_ = builtin_plugins()
        without_update = replace(first, operations=("complete",))
        with self.assertRaisesRegex(PluginRegistrationError, "parameterless update"):
            validate_plugins([without_update])

        cyclic_first = replace(first, dependencies=(second.name,))
        cyclic_second = replace(second, dependencies=(first.name,))
        with self.assertRaisesRegex(PluginRegistrationError, "dependency cycle"):
            validate_plugins([cyclic_first, cyclic_second])


if __name__ == "__main__":
    unittest.main()
