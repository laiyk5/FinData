from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from maintool.cli import main
from maintool.jsonio import read_json
from maintool.pipeline import run_full_pipeline


REPO_ROOT = Path(__file__).resolve().parents[2]


class InstrumentUniverseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        shutil.copytree(
            REPO_ROOT / "published" / "datasets" / "tushare" / "index_weight",
            self.repo_root / "published" / "datasets" / "tushare" / "index_weight",
        )
        shutil.copytree(
            REPO_ROOT / "published" / "datasets" / "tushare" / "daily",
            self.repo_root / "published" / "datasets" / "tushare" / "daily",
        )
        # Clear published data so tests start with a clean state
        _clear_current(self.repo_root / "published" / "datasets" / "tushare" / "index_weight")
        _clear_current(self.repo_root / "published" / "datasets" / "tushare" / "daily")
        (self.repo_root / "sandboxes" / "runs").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_symbols_resolve_from_published_index_weight_csi300(self) -> None:
        run_full_pipeline(
            repo_root=self.repo_root,
            dataset_name="tushare_index_weight",
            symbols=[],
            trade_dates=[],
            run_id="index-weight-for-csi300",
            use_fake=True,
            extras={
                "index_code": "000300.SH",
                "start_date": "20260401",
                "end_date": "20260430",
            },
        )

        exit_code = main(
            [
                "--repo-root",
                str(self.repo_root),
                "maintain-plan",
                "tushare_daily",
                "--fake",
                "--trade-date",
                "20240506",
                "--symbols",
                "@universe:index:CSI300",
                "--run-id",
                "daily-from-csi300",
            ]
        )

        self.assertEqual(exit_code, 0)
        manifest = read_json(
            self.repo_root
            / "sandboxes"
            / "runs"
            / "tushare_daily"
            / "daily-from-csi300"
            / "run_manifest.json"
        )
        self.assertEqual(manifest["symbol_selector"], "@universe:index:CSI300")
        self.assertEqual(manifest["symbol_selector_resolved_at"], "20260430")
        self.assertEqual(manifest["resolved_symbols"], manifest["symbols"])
        self.assertIn("000001.SZ", manifest["resolved_symbols"])
        self.assertIn("600000.SH", manifest["resolved_symbols"])

    def test_symbols_resolve_from_published_index_weight_sse50(self) -> None:
        run_full_pipeline(
            repo_root=self.repo_root,
            dataset_name="tushare_index_weight",
            symbols=[],
            trade_dates=[],
            run_id="index-weight-for-sse50",
            use_fake=True,
            extras={
                "index_code": "000016.SH",
                "start_date": "20260401",
                "end_date": "20260430",
            },
        )

        exit_code = main(
            [
                "--repo-root",
                str(self.repo_root),
                "maintain-plan",
                "tushare_daily",
                "--fake",
                "--trade-date",
                "20240506",
                "--symbols",
                "@universe:index:SSE50",
                "--run-id",
                "daily-from-sse50",
            ]
        )

        self.assertEqual(exit_code, 0)
        manifest = read_json(
            self.repo_root
            / "sandboxes"
            / "runs"
            / "tushare_daily"
            / "daily-from-sse50"
            / "run_manifest.json"
        )
        self.assertEqual(manifest["symbol_selector"], "@universe:index:SSE50")
        self.assertEqual(manifest["symbol_selector_resolved_at"], "20260430")
        self.assertEqual(manifest["resolved_symbols"], manifest["symbols"])
        self.assertIn("000001.SZ", manifest["resolved_symbols"])
        self.assertIn("600000.SH", manifest["resolved_symbols"])

    def test_universe_resolution_fails_without_published_index_weight(self) -> None:
        with self.assertRaises((ValueError, RuntimeError)):
            main(
                [
                    "--repo-root",
                    str(self.repo_root),
                    "maintain-plan",
                    "tushare_daily",
                    "--fake",
                    "--trade-date",
                    "20240506",
                    "--symbols",
                    "@universe:index:CSI300",
                    "--run-id",
                    "daily-no-index-weight",
                ]
            )


def _clear_current(dataset_root: Path) -> None:
    current_dir = dataset_root / "current"
    if not current_dir.exists():
        return
    for path in sorted(current_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()


if __name__ == "__main__":
    unittest.main()
