from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from maintool.ingest import ingest_prepared_raw
from maintool.dataset_specs import chunk_date_range_by_year
from maintool.jsonio import read_json, write_json
from maintool.pipeline import run_full_pipeline
from maintool.prepare import prepare_raw
from maintool.qa import run_qa
from maintool.run_sandbox import create_run_sandbox


REPO_ROOT = Path(__file__).resolve().parents[2]


class TradeCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        shutil.copytree(REPO_ROOT / "published" / "datasets" / "tushare" / "trade_cal", self.repo_root / "published" / "datasets" / "tushare" / "trade_cal")
        clear_current(self.repo_root / "published" / "datasets" / "tushare" / "trade_cal")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_mock_pipeline_publishes(self) -> None:
        context, result = run_full_pipeline(
            repo_root=self.repo_root,
            dataset_name="trade_calendar",
            use_fake=True,
            symbols=[],
            trade_dates=[],
            run_id="calendar-mock",
            extras={"exchange": "SSE", "start_date": "20240501", "end_date": "20240507", "is_open": None},
        )

        self.assertEqual(result["prepare"]["prepared"], 1)
        self.assertTrue(
            (
                context.dataset_root
                / "current"
                / "exchange=SSE"
                / "trade_calendar.csv"
            ).is_file()
        )
        self.assertTrue(result["qa"]["passed"])

    def test_trade_calendar_requests_are_chunked_by_year(self) -> None:
        self.assertEqual(
            chunk_date_range_by_year("20231230", "20240102"),
            [("20231230", "20231231"), ("20240101", "20240102")],
        )

    def test_duplicate_key_blocks_qa(self) -> None:
        context = self.prepared_context("calendar-duplicate")
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"].append(dict(payload["items"][0]))
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        validation = read_json(context.qa_root / "validation_report.json")
        self.assertTrue(any("duplicate primary key" in error for error in validation["errors"]))

    def test_missing_date_blocks_qa(self) -> None:
        context = self.prepared_context("calendar-missing")
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"] = [row for row in payload["items"] if row["cal_date"] != "20240503"]
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        missingness = read_json(context.qa_root / "missingness_report.json")
        self.assertTrue(missingness["blocks_publish"])
        self.assertEqual(missingness["missing_count"], 1)

    def test_invalid_is_open_blocks_qa(self) -> None:
        context = self.prepared_context("calendar-invalid-open")
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"][0]["is_open"] = "7"
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        validation = read_json(context.qa_root / "validation_report.json")
        self.assertTrue(any("invalid is_open" in error for error in validation["errors"]))

    def prepared_context(self, run_id: str):
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="trade_calendar",
            use_fake=True,
            symbols=[],
            trade_dates=[],
            run_id=run_id,
            extras={"exchange": "SSE", "start_date": "20240501", "end_date": "20240507", "is_open": None},
        )
        prepare_raw(context)
        return context


def trade_cal_response(items) -> bytes:
    return json.dumps(
        {
            "code": 0,
            "msg": None,
            "data": {
                "fields": ["exchange", "cal_date", "is_open", "pretrade_date"],
                "items": items,
            },
        }
    ).encode("utf-8")


class TradeCalendarTushareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        shutil.copytree(REPO_ROOT / "published" / "datasets" / "tushare" / "trade_cal", self.repo_root / "published" / "datasets" / "tushare" / "trade_cal")
        clear_current(self.repo_root / "published" / "datasets" / "tushare" / "trade_cal")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_tushare_trade_cal_success_normalizes(self) -> None:
        context = self.context("calendar-real-success")

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            summary = prepare_raw(
                context,
                transport=lambda request, timeout: trade_cal_response(
                    [["SSE", "20240501", 0, "20240430"], ["SSE", "20240502", 1, "20240430"]]
                ),
            )

        self.assertEqual(summary["prepared"], 1)
        ledger = read_json(context.prepare_ledger_path)
        request = ledger["requests"]["trade_cal:SSE:20240501:20240502"]
        raw_payload = read_json(context.sandbox_root / request["raw_path"])
        self.assertEqual(raw_payload["items"][0]["exchange"], "SSE")
        self.assertNotIn("secret-token", context.prepare_ledger_path.read_text(encoding="utf-8"))

    def test_tushare_empty_response_fails_missingness(self) -> None:
        context = self.context("calendar-real-empty")

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            prepare_raw(context, transport=lambda request, timeout: trade_cal_response([]))

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        missingness = read_json(context.qa_root / "missingness_report.json")
        self.assertEqual(missingness["missing_count"], 2)

    def test_tushare_leading_gap_blocks_as_missingness(self) -> None:
        context = self.context("calendar-real-leading-gap")

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            prepare_raw(
                context,
                transport=lambda request, timeout: trade_cal_response([["SSE", "20240502", 1, None]]),
            )

        report = ingest_prepared_raw(context)
        status = run_qa(context)
        current_file = (
            context.sandbox_dataset_root
            / "current"
            / "exchange=SSE"
            / "trade_calendar.csv"
        )

        self.assertEqual(report["prepared_rows"], 1)
        self.assertFalse(status["passed"])
        missingness = read_json(context.qa_root / "missingness_report.json")
        self.assertEqual(missingness["missing_count"], 1)
        with current_file.open(newline="", encoding="utf-8") as input_file:
            rows = list(csv.DictReader(input_file))
        self.assertEqual(rows[0]["cal_date"], "20240502")
        self.assertEqual(rows[0]["pretrade_date"], "")

    def test_tushare_permission_error_does_not_retry(self) -> None:
        context = self.context("calendar-real-permission")

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            summary = prepare_raw(
                context,
                transport=lambda request, timeout: json.dumps({"code": 2002, "msg": "权限不足", "data": None}).encode("utf-8"),
            )

        ledger = read_json(context.prepare_ledger_path)
        request = ledger["requests"]["trade_cal:SSE:20240501:20240502"]
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(request["attempts"], 1)
        self.assertEqual(request["error_type"], "permission")

    def test_tushare_network_error_retries(self) -> None:
        context = self.context("calendar-real-network", max_retries=2)
        calls = {"count": 0}

        def transport(request, timeout):
            calls["count"] += 1
            if calls["count"] == 1:
                raise URLError("temporary")
            return trade_cal_response([["SSE", "20240501", 0, "20240430"], ["SSE", "20240502", 1, "20240430"]])

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            summary = prepare_raw(context, transport=transport)

        ledger = read_json(context.prepare_ledger_path)
        request = ledger["requests"]["trade_cal:SSE:20240501:20240502"]
        self.assertEqual(summary["prepared"], 1)
        self.assertEqual(request["attempts"], 2)

    def context(self, run_id: str, max_retries: int = 3):
        return create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="trade_calendar",
            symbols=[],
            trade_dates=[],
            run_id=run_id,
            max_retries=max_retries,
            extras={"exchange": "SSE", "start_date": "20240501", "end_date": "20240502", "is_open": None},
        )


def clear_current(dataset_root: Path) -> None:
    current_dir = dataset_root / "current"
    if not current_dir.exists():
        return
    for path in sorted(current_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
