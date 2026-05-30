from __future__ import annotations

import csv
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from maintool.cninfo import CninfoProviderError, fetch_cninfo_announcements, normalize_cninfo_announcement
from maintool.ingest import ingest_prepared_raw
from maintool.jsonio import read_json
from maintool.pipeline import run_full_pipeline
from maintool.prepare import prepare_fake_raw
from maintool.qa import run_qa
from maintool.run_sandbox import create_run_sandbox


REPO_ROOT = Path(__file__).resolve().parents[2]


class ReportCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        shutil.copytree(REPO_ROOT / "datasets" / "cninfo" / "report_catalog", self.repo_root / "datasets" / "cninfo" / "report_catalog")
        self.reset_runtime_dataset_state(self.repo_root / "datasets" / "cninfo" / "report_catalog")
        (self.repo_root / "sandboxes" / "runs").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def reset_runtime_dataset_state(self, dataset_root: Path) -> None:
        for relative in ("published/current",):
            path = dataset_root / relative
            if path.exists():
                shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)

        for forbidden in ("data", "checks", "logs"):
            forbidden_path = dataset_root / forbidden
            if forbidden_path.exists():
                shutil.rmtree(forbidden_path)

    def test_fake_pipeline_filters_summary_and_marks_latest_version(self) -> None:
        context, result = run_full_pipeline(
            repo_root=self.repo_root,
            dataset_name="report_catalog",
            symbols=["600000.SH"],
            trade_dates=[],
            run_id="report-catalog-fake",
            use_fake=True,
            extras={
                "universe_id": "manual",
                "start_year": "2024",
                "end_year": "2024",
                "report_types": ["annual", "semiannual", "q1", "q3"],
                "max_pages_per_request": "1",
            },
        )

        self.assertEqual(result["prepare"]["prepared"], 1)
        current_path = (
            context.dataset_root
            / "published"
            / "current"
            / "universe_id=manual"
            / "report_catalog.csv"
        )
        rows = self.read_rows(current_path)
        self.assertFalse(any("摘要" in row["announcement_title"] for row in rows))
        annual_rows = [row for row in rows if row["report_type"] == "annual"]
        self.assertEqual([row["version_no"] for row in annual_rows], ["1", "2"])
        self.assertEqual([row["latest_version"] for row in annual_rows], ["false", "true"])

    def test_universe_selector_is_recorded_in_manifest(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="report_catalog",
            symbols=["600000.SH", "600519.SH"],
            trade_dates=[],
            run_id="report-catalog-selector",
            use_fake=True,
            extras={
                "universe_id": "index:SSE50",
                "symbol_selector": "@universe:index:SSE50",
                "symbol_selector_resolved_at": "20260430",
                "start_year": "2024",
                "end_year": "2024",
                "report_types": ["annual"],
                "max_pages_per_request": "1",
            },
        )
        manifest = read_json(context.run_manifest_path)

        self.assertEqual(manifest["symbol_selector"], "@universe:index:SSE50")
        self.assertEqual(manifest["symbol_selector_resolved_at"], "20260430")
        self.assertEqual(manifest["resolved_symbols"], ["600000.SH", "600519.SH"])

    def test_report_catalog_qa_rejects_multiple_latest_versions(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="report_catalog",
            symbols=["600000.SH"],
            trade_dates=[],
            run_id="report-catalog-bad-latest",
            use_fake=True,
            extras={
                "universe_id": "manual",
                "start_year": "2024",
                "end_year": "2024",
                "report_types": ["annual"],
                "max_pages_per_request": "1",
            },
        )
        prepare_fake_raw(context)
        ingest_prepared_raw(context)
        current_path = next((context.sandbox_dataset_root / "published" / "current").rglob("report_catalog.csv"))
        rows = self.read_rows(current_path)
        for row in rows:
            if row["report_type"] == "annual":
                row["latest_version"] = "true"
        with current_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        status = run_qa(context)

        self.assertFalse(status["passed"])
        validation = read_json(context.qa_root / "validation_report.json")
        self.assertTrue(any("multiple latest_version" in error for error in validation["errors"]))

    def test_cninfo_non_json_response_is_blocked(self) -> None:
        def blocked_transport(request, timeout):
            return b"<html>captcha verify</html>"

        with self.assertRaises(CninfoProviderError) as raised:
            fetch_cninfo_announcements({"report_year": "2024", "page_num": "1"}, transport=blocked_transport)

        self.assertEqual(raised.exception.error_type, "blocked")

    def test_cninfo_announcement_normalization(self) -> None:
        row = normalize_cninfo_announcement(
            {
                "universe_id": "manual",
                "ts_code": "600000.SH",
                "stock_code": "600000",
                "stock_exchange": "sse",
                "report_year": "2024",
            },
            {
                "announcementId": "121",
                "announcementTitle": "2024年第三季度报告",
                "announcementTime": 1761782400000,
                "adjunctUrl": "finalpage/2025-10-30/121.PDF",
                "secName": "浦发银行",
            },
            seen_at="20260524T000000Z",
        )

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["report_type"], "q3")
        self.assertEqual(row["period_end"], "20240930")
        self.assertEqual(row["pdf_url"], "https://static.cninfo.com.cn/finalpage/2025-10-30/121.PDF")

    def read_rows(self, path: Path) -> list[dict[str, str]]:
        with path.open(newline="", encoding="utf-8") as input_file:
            return list(csv.DictReader(input_file))


if __name__ == "__main__":
    unittest.main()
