from __future__ import annotations

import csv
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from maintool.dataset_specs import get_spec, plan_requests
from maintool.pipeline import run_full_pipeline
from maintool.storage import read_table


REPO_ROOT = Path(__file__).resolve().parents[2]


class AdjFactorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        source = REPO_ROOT / "workspace" / "published" / "datasets" / "tushare" / "adj_factor"
        target = self.workspace_root / "published" / "datasets" / "tushare" / "adj_factor"
        shutil.copytree(source, target)
        write_minimal_adj_factor_current(target / "current")
        (self.workspace_root / "sandboxes" / "runs").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_request_planning_uses_adj_factor_endpoint(self) -> None:
        requests = plan_requests(
            "tushare_adj_factor",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            extras={},
        )

        self.assertEqual(requests[0]["api"], "adj_factor")

    def test_all_market_request_planning_uses_trade_date_all(self) -> None:
        requests = plan_requests(
            "tushare_adj_factor",
            symbols=[],
            trade_dates=[],
            extras={
                "start_date": "20240506",
                "end_date": "20240507",
                "expected_trade_dates": ["20240506", "20240507"],
                "daily_request_strategy": "trade_date_all",
                "all_market": True,
            },
        )

        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request["api"] == "adj_factor" for request in requests))
        self.assertTrue(all(request["request_mode"] == "trade_date_all" for request in requests))
        self.assertTrue(all(request["ts_code"] == "" for request in requests))

    def test_full_fake_pipeline_publishes_monthly_parquet(self) -> None:
        context, result = run_full_pipeline(
            workspace_root=self.workspace_root,
            dataset_name="tushare_adj_factor",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="adj-factor-fake",
            use_fake=True,
        )

        current_file = context.dataset_root / "current" / "trade_month=202405" / "adj_factor.parquet"
        self.assertTrue(current_file.is_file())
        rows = read_table(current_file, get_spec("tushare_adj_factor"))
        self.assertEqual({row["trade_date"] for row in rows}, {"20240503", "20240506"})
        self.assertTrue(result["qa"]["passed"])


def write_minimal_adj_factor_current(current_dir: Path) -> None:
    shutil.rmtree(current_dir)
    current_dir.mkdir(parents=True)
    with (current_dir / "adj_factor.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=["ts_code", "trade_date", "adj_factor"])
        writer.writeheader()
        writer.writerow({"ts_code": "000001.SZ", "trade_date": "20240503", "adj_factor": "1.0"})
