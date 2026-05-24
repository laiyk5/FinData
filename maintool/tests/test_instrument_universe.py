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
from maintool.ingest import ingest_prepared_raw
from maintool.jsonio import read_json, write_json
from maintool.pipeline import run_full_pipeline
from maintool.prepare import prepare_raw
from maintool.qa import run_qa
from maintool.run_sandbox import create_run_sandbox


REPO_ROOT = Path(__file__).resolve().parents[2]


class InstrumentUniverseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        shutil.copytree(REPO_ROOT / "datasets" / "instrument_universe", self.repo_root / "datasets" / "instrument_universe")
        shutil.copytree(REPO_ROOT / "datasets" / "tushare_daily", self.repo_root / "datasets" / "tushare_daily")
        clear_current(self.repo_root / "datasets" / "instrument_universe")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_mock_pipeline_publishes_universe_partition(self) -> None:
        context, result = run_full_pipeline(
            repo_root=self.repo_root,
            dataset_name="instrument_universe",
            provider="mock",
            symbols=[],
            trade_dates=[],
            run_id="universe-mock",
            extras={
                "universe_id": "index:SSE50",
                "source_id": "000016.SH",
                "start_date": "20260401",
                "end_date": "20260430",
            },
        )

        current_file = (
            context.dataset_root
            / "data"
            / "published"
            / "current"
            / "universe_id=index:SSE50"
            / "instrument_universe.csv"
        )
        self.assertEqual(result["prepare"]["prepared"], 1)
        self.assertTrue(current_file.is_file())
        self.assertTrue(result["qa"]["passed"])

    def test_duplicate_member_snapshot_blocks_qa(self) -> None:
        context = self.prepared_context("universe-duplicate")
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"].append(dict(payload["items"][0]))
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        validation = read_json(context.qa_root / "validation_report.json")
        self.assertTrue(any("duplicate primary key" in error for error in validation["errors"]))

    def test_empty_universe_blocks_qa(self) -> None:
        context = self.prepared_context("universe-empty")
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"] = []
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        validation = read_json(context.qa_root / "validation_report.json")
        self.assertTrue(any("empty" in error for error in validation["errors"]))

    def test_daily_symbols_can_resolve_from_published_universe_selector(self) -> None:
        run_full_pipeline(
            repo_root=self.repo_root,
            dataset_name="instrument_universe",
            provider="mock",
            symbols=[],
            trade_dates=[],
            run_id="universe-for-selector",
            extras={
                "universe_id": "index:SSE50",
                "source_id": "000016.SH",
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
                "--provider",
                "fake",
                "--trade-date",
                "20240506",
                "--symbols",
                "@universe:index:SSE50",
                "--run-id",
                "daily-from-universe",
            ]
        )

        self.assertEqual(exit_code, 0)
        manifest = read_json(
            self.repo_root / "sandboxes" / "runs" / "tushare_daily" / "daily-from-universe" / "run_manifest.json"
        )
        self.assertEqual(manifest["symbol_selector"], "@universe:index:SSE50")
        self.assertEqual(manifest["symbol_selector_resolved_at"], "20260430")
        self.assertEqual(manifest["resolved_symbols"], ["600000.SH", "600519.SH"])
        self.assertEqual(manifest["symbols"], ["600000.SH", "600519.SH"])

    def test_tushare_index_weight_success_normalizes(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="instrument_universe",
            provider="tushare",
            symbols=[],
            trade_dates=[],
            run_id="universe-real-success",
            extras={
                "universe_id": "index:SSE50",
                "source_id": "000016.SH",
                "start_date": "20260401",
                "end_date": "20260430",
            },
        )

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            summary = prepare_raw(
                context,
                transport=lambda request, timeout: index_weight_response(
                    [["000016.SH", "600519.SH", "20260430", 9.422]]
                ),
            )

        self.assertEqual(summary["prepared"], 1)
        ledger = read_json(context.prepare_ledger_path)
        request = ledger["requests"]["index_weight:index:SSE50:000016.SH:20260401:20260430"]
        raw_payload = read_json(context.sandbox_root / request["raw_path"])
        self.assertEqual(raw_payload["items"][0]["universe_id"], "index:SSE50")
        self.assertEqual(raw_payload["items"][0]["member_code"], "600519.SH")
        self.assertEqual(raw_payload["items"][0]["as_of_date"], "20260430")
        self.assertNotIn("secret-token", context.prepare_ledger_path.read_text(encoding="utf-8"))

    def prepared_context(self, run_id: str):
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="instrument_universe",
            provider="mock",
            symbols=[],
            trade_dates=[],
            run_id=run_id,
            extras={
                "universe_id": "index:SSE50",
                "source_id": "000016.SH",
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


def clear_current(dataset_root: Path) -> None:
    current_dir = dataset_root / "data" / "published" / "current"
    if not current_dir.exists():
        return
    for path in sorted(current_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
