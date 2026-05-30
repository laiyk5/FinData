from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
import json
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from maintool.ingest import ingest_prepared_raw
from maintool.pipeline import run_full_pipeline
from maintool.prepare import prepare_fake_raw
from maintool.qa import run_qa
from maintool.review import build_review
from maintool.run_sandbox import create_run_sandbox, get_run_context


REPO_ROOT = Path(__file__).resolve().parents[2]


class ReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        shutil.copytree(REPO_ROOT / "published" / "datasets" / "tushare" / "daily", self.repo_root / "published" / "datasets" / "tushare" / "daily")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_review_completed_run(self) -> None:
        context, _ = run_full_pipeline(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="review-complete",
            use_fake=True,
        )

        output = build_review(context)

        self.assertIn("Run Review: tushare_daily/review-complete", output)
        self.assertIn("Provider: fake", output)
        self.assertIn("- prepared: done", output)
        self.assertIn("- ingested: done", output)
        self.assertIn("- qa_passed: done", output)
        self.assertIn("- published: done", output)
        self.assertIn("- requests: 1", output)
        self.assertIn("- rows prepared: 1", output)
        self.assertIn("Run is published. No immediate action required.", output)

    def test_review_incomplete_run_recommends_prepare(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="review-incomplete",
            use_fake=True,
        )

        output = build_review(context)

        self.assertIn("- prepared: not done", output)
        self.assertIn("- pending: 1", output)
        self.assertIn("Rerun prepare after checking provider errors.", output)

    def test_review_failed_qa_recommends_investigating_missingness(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=["000001.SZ", "600001.SH"],
            trade_dates=["20240506"],
            run_id="review-missing",
            use_fake=True,
        )
        prepare_fake_raw(context)
        raw_path = next(context.raw_root.glob("*.json"))
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        payload["items"] = [item for item in payload["items"] if item["ts_code"] != "600001.SH"]
        payload["row_count"] = len(payload["items"])
        raw_path.write_text(json.dumps(payload), encoding="utf-8")
        ingest_prepared_raw(context)
        run_qa(context)

        output = build_review(context)

        self.assertIn("- failed: 0", output)
        self.assertIn("- missing rows: 1", output)
        self.assertIn("Investigate unknown missingness", output)


class ReviewCliPathTests(unittest.TestCase):
    def test_context_path_is_stable(self) -> None:
        context = get_run_context(Path("/tmp/repo"), "tushare_daily", "run-id")
        self.assertEqual(
            context.sandbox_root,
            Path("/tmp/repo/sandboxes/runs/tushare_daily/run-id"),
        )
