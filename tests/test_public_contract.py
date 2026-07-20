from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from findata.cli import resolve_workspace
from findata.contracts import OperandError
from findata.operations import normalize_operation
from findata.storage import Workspace


class OperationNormalizationTests(unittest.TestCase):
    def test_resolves_today_once_and_canonicalizes_array_operands(self) -> None:
        values = normalize_operation(
            "tushare_daily_basic",
            "complete",
            {
                "symbols": ["600000.SH", "600000.SH", "000001.SZ"],
                "timerange": "2026-07-01:today",
            },
            today=date(2026, 7, 20),
        )
        self.assertEqual(values["symbols"], ["000001.SZ", "600000.SH"])
        self.assertEqual(values["timerange"], "2026-07-01:2026-07-20")

    def test_rejects_unknown_dataset_operation_and_fields_before_submission(self) -> None:
        with self.assertRaises(OperandError):
            normalize_operation("unknown", "update", {}, today=date(2026, 7, 20))
        with self.assertRaises(OperandError):
            normalize_operation(
                "tushare_stock_basic", "complete", {}, today=date(2026, 7, 20)
            )
        with self.assertRaises(OperandError):
            normalize_operation(
                "tushare_trade_cal",
                "complete",
                {"exchanges": ["SSE"], "timerange": "2026-07-01:2026-07-02", "extra": 1},
                today=date(2026, 7, 20),
            )


class WorkspaceResolutionTests(unittest.TestCase):
    def test_explicit_then_environment_then_nearest_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            explicit = root / "explicit"
            environment = root / "environment"
            parent = root / "parent"
            for workspace in (explicit, environment, parent):
                Workspace.init(workspace)
            nested = parent / "one" / "two"
            nested.mkdir(parents=True)

            self.assertEqual(
                resolve_workspace(explicit, environ={"FINDATA_WORKSPACE": str(environment)}, cwd=nested),
                explicit.resolve(),
            )
            self.assertEqual(
                resolve_workspace(None, environ={"FINDATA_WORKSPACE": str(environment)}, cwd=nested),
                environment.resolve(),
            )
            self.assertEqual(resolve_workspace(None, environ={}, cwd=nested), parent.resolve())

    def test_missing_workspace_has_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "findata-server init"):
                resolve_workspace(None, environ={}, cwd=Path(directory))


if __name__ == "__main__":
    unittest.main()
