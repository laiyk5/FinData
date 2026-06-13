from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from maintool.ingest import ingest_prepared_raw
from maintool.jsonio import read_json, write_json
from maintool.pipeline import run_full_pipeline
from maintool.prepare import prepare_fake_raw
from maintool.qa import run_qa
from maintool.run_sandbox import create_run_sandbox


REPO_ROOT = Path(__file__).resolve().parents[2]


class DailyBasicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        for dataset_name, api_name in (("tushare_daily_basic", "daily_basic"), ("trade_calendar", "trade_cal")):
            source = REPO_ROOT / "workspace" / "published" / "datasets" / "tushare" / api_name
            target = self.workspace_root / "published" / "datasets" / "tushare" / api_name
            shutil.copytree(source, target)
        (self.workspace_root / "sandboxes" / "runs").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_fake_pipeline_publishes_daily_basic(self) -> None:
        context, result = run_full_pipeline(
            workspace_root=self.workspace_root,
            dataset_name="tushare_daily_basic",
            symbols=["000001.SZ", "600000.SH"],
            trade_dates=["20240506"],
            run_id="daily-basic-run-ok",
            use_fake=True,
        )

        self.assertEqual(result["prepare"]["prepared"], 2)
        current_file = context.sandbox_dataset_root / "current" / "daily_basic.csv"
        self.assertTrue(current_file.is_file())
        self.assertTrue((context.sandbox_root / "logs" / "prepare_summary.json").is_file())

    def test_daily_basic_range_scheduler_uses_one_request_per_symbol(self) -> None:
        symbols = [f"{index:06d}.SZ" for index in range(300)]
        expected_trade_dates = [f"2024{index:04d}" for index in range(1, 2428)]
        context = create_run_sandbox(
            workspace_root=self.workspace_root,
            dataset_name="tushare_daily_basic",
            symbols=symbols,
            trade_dates=[],
            run_id="daily-basic-scheduled-range",
            use_fake=True,
            extras={
                "start_date": expected_trade_dates[0],
                "end_date": expected_trade_dates[-1],
                "expected_trade_dates": expected_trade_dates,
                "daily_request_strategy": "auto",
            },
        )
        manifest = read_json(context.run_manifest_path)
        ledger = read_json(context.prepare_ledger_path)

        self.assertEqual(manifest["request_plan"]["request_count"], 300)
        self.assertEqual(manifest["request_plan"]["max_estimated_rows_per_request"], len(expected_trade_dates))
        self.assertEqual(set(manifest["request_plan"]["modes"]), {"symbol_range"})
        self.assertTrue(all(request["api"] == "daily_basic" for request in ledger["requests"].values()))
        self.assertTrue(all(request["estimated_max_rows"] <= 6000 for request in ledger["requests"].values()))

    def test_total_share_contradiction_blocks_qa(self) -> None:
        context = create_run_sandbox(
            workspace_root=self.workspace_root,
            dataset_name="tushare_daily_basic",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="daily-basic-bad-shares",
            use_fake=True,
        )
        prepare_fake_raw(context)
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"][0]["float_share"] = "999999999"
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        validation = read_json(context.qa_root / "validation_report.json")
        self.assertTrue(any("float_share exceeds total_share" in error for error in validation["errors"]))

    def test_daily_basic_missingness_uses_prepared_raw_keys(self) -> None:
        context = create_run_sandbox(
            workspace_root=self.workspace_root,
            dataset_name="tushare_daily_basic",
            symbols=["000001.SZ", "600000.SH"],
            trade_dates=["20240506"],
            run_id="daily-basic-missing-prepared-key",
            use_fake=True,
        )
        prepare_fake_raw(context)
        ingest_prepared_raw(context)

        current_file = context.sandbox_dataset_root / "current" / "daily_basic.csv"
        current_file.unlink()

        status = run_qa(context)

        self.assertFalse(status["passed"])
        missingness = read_json(context.qa_root / "missingness_report.json")
        self.assertEqual(missingness["expected_source"], "prepared_raw")
        self.assertEqual(missingness["expected_count"], 2)
        self.assertEqual(missingness["actual_count"], 0)
        self.assertEqual(missingness["missing_count"], 2)


if __name__ == "__main__":
    unittest.main()
