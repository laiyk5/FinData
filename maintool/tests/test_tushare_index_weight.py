from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from maintool.cli import main
from maintool.dataset_specs import plan_requests
from maintool.ingest import ingest_prepared_raw
from maintool.jsonio import read_json, write_json
from maintool.pipeline import run_full_pipeline
from maintool.prepare import prepare_raw
from maintool.publish import published_coverage
from maintool.qa import run_qa
from maintool.run_sandbox import create_run_sandbox


REPO_ROOT = Path(__file__).resolve().parents[2]


class TushareIndexWeightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        source = REPO_ROOT / "datasets" / "tushare" / "index_weight"
        target = self.repo_root / "datasets" / "tushare" / "index_weight"
        shutil.copytree(source, target)
        (self.repo_root / "sandboxes" / "runs").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_monthly_request_planning_from_launch_month(self) -> None:
        requests = plan_requests(
            "tushare_index_weight",
            symbols=[],
            trade_dates=[],
            extras={
                "index_code": "000300.SH",
                "start_date": "20050401",
                "end_date": "20050615",
            },
        )

        self.assertEqual(
            requests,
            [
                {
                    "api": "index_weight",
                    "request_mode": "index_month",
                    "index_code": "000300.SH",
                    "start_date": "20050401",
                    "end_date": "20050430",
                },
                {
                    "api": "index_weight",
                    "request_mode": "index_month",
                    "index_code": "000300.SH",
                    "start_date": "20050501",
                    "end_date": "20050531",
                },
                {
                    "api": "index_weight",
                    "request_mode": "index_month",
                    "index_code": "000300.SH",
                    "start_date": "20050601",
                    "end_date": "20050615",
                },
            ],
        )

    def test_full_fake_pipeline_publishes_index_weight(self) -> None:
        context, result = run_full_pipeline(
            repo_root=self.repo_root,
            dataset_name="tushare_index_weight",
            symbols=[],
            trade_dates=[],
            run_id="index-weight-run-ok",
            use_fake=True,
            extras={
                "index_code": "000300.SH",
                "start_date": "20260401",
                "end_date": "20260430",
            },
        )

        self.assertEqual(result["prepare"]["prepared"], 1)
        current_file = (
            context.sandbox_dataset_root
            / "published"
            / "current"
            / "index_code=000300.SH"
            / "index_weight.csv"
        )
        self.assertTrue(current_file.is_file())
        coverage = published_coverage("tushare_index_weight", context.dataset_root / "published" / "current")
        self.assertEqual(coverage["index_codes"], ["000300.SH"])
        self.assertEqual(coverage["latest_constituent_counts"]["000300.SH"], 2)

    def test_tushare_response_preserves_raw_index_weight_columns(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_index_weight",
            symbols=[],
            trade_dates=[],
            run_id="index-weight-real-success",
            extras={
                "index_code": "000300.SH",
                "start_date": "20260401",
                "end_date": "20260430",
            },
        )

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            summary = prepare_raw(
                context,
                transport=lambda request, timeout: index_weight_response(
                    [["000300.SH", "600000.SH", "20260430", 0.62]]
                ),
            )

        self.assertEqual(summary["prepared"], 1)
        ledger = read_json(context.prepare_ledger_path)
        request = ledger["requests"]["index_weight:000300.SH:20260401:20260430"]
        raw_payload = read_json(context.sandbox_root / request["raw_path"])
        self.assertEqual(raw_payload["fields"], ["index_code", "con_code", "trade_date", "weight"])
        self.assertEqual(
            raw_payload["items"][0],
            {
                "index_code": "000300.SH",
                "con_code": "600000.SH",
                "trade_date": "20260430",
                "weight": 0.62,
            },
        )
        self.assertNotIn("secret-token", context.prepare_ledger_path.read_text(encoding="utf-8"))

    def test_invalid_weight_blocks_qa(self) -> None:
        context = self.prepared_context("index-weight-bad-weight")
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"][0]["weight"] = "-1"
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        validation = read_json(context.qa_root / "validation_report.json")
        self.assertTrue(any("weight is negative" in error for error in validation["errors"]))

    def test_invalid_date_and_duplicate_keys_block_qa(self) -> None:
        context = self.prepared_context("index-weight-bad-date-duplicate")
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"][0]["trade_date"] = "20261340"
        payload["items"][1] = dict(payload["items"][0])
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        validation = read_json(context.qa_root / "validation_report.json")
        self.assertTrue(any("invalid trade_date" in error for error in validation["errors"]))
        self.assertTrue(any("duplicate primary key" in error for error in validation["errors"]))

    def test_empty_output_blocks_qa(self) -> None:
        context = self.prepared_context("index-weight-empty")
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"] = []
        payload["row_count"] = 0
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        validation = read_json(context.qa_root / "validation_report.json")
        self.assertTrue(any("empty published current" in error for error in validation["errors"]))

    def test_cli_maintain_plan_accepts_index_code(self) -> None:
        exit_code = main(
            [
                "--repo-root",
                str(self.repo_root),
                "maintain-plan",
                "tushare_index_weight",
                "--fake",
                "--index-code",
                "000300.SH",
                "--start-date",
                "20260401",
                "--end-date",
                "20260531",
                "--run-id",
                "index-weight-cli-plan",
            ]
        )

        self.assertEqual(exit_code, 0)
        manifest = read_json(
            self.repo_root
            / "sandboxes"
            / "runs"
            / "tushare_index_weight"
            / "index-weight-cli-plan"
            / "run_manifest.json"
        )
        self.assertEqual(manifest["index_weight_request"]["index_code"], "000300.SH")
        self.assertEqual(manifest["request_plan"]["request_count"], 2)

    def prepared_context(self, run_id: str):
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_index_weight",
            use_fake=True,
            symbols=[],
            trade_dates=[],
            run_id=run_id,
            extras={
                "index_code": "000300.SH",
                "start_date": "20260401",
                "end_date": "20260430",
            },
        )
        prepare_raw(context)
        return context


def index_weight_response(items) -> bytes:
    return json.dumps(
        {
            "code": 0,
            "msg": None,
            "data": {
                "fields": ["index_code", "con_code", "trade_date", "weight"],
                "items": items,
            },
        }
    ).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
