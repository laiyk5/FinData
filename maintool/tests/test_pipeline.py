from __future__ import annotations

import csv
import json
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
from maintool.publish import publish_sandbox, published_coverage, render_coverage_block
from maintool.qa import run_qa
from maintool.run_sandbox import create_run_sandbox, get_run_context


REPO_ROOT = Path(__file__).resolve().parents[2]


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        dataset_source = REPO_ROOT / "published" / "datasets" / "tushare" / "daily"
        dataset_target = self.repo_root / "published" / "datasets" / "tushare" / "daily"
        shutil.copytree(dataset_source, dataset_target)
        (self.repo_root / "sandboxes" / "runs").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_fake_pipeline_publishes_current_and_archives_previous_current(self) -> None:
        context, result = run_full_pipeline(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=["000001.SZ", "600000.SH"],
            trade_dates=["20240506"],
            run_id="run-ok",
            use_fake=True,
        )

        self.assertEqual(result["prepare"]["prepared"], 1)
        self.assertTrue((context.sandbox_root / "run_manifest.json").is_file())
        self.assertTrue((context.dataset_root / "current" / "daily.csv").is_file())
        backup_dir = context.repo_root / "backups" / "tushare" / "daily"
        self.assertTrue(backup_dir.is_dir())
        backups = [path for path in backup_dir.iterdir() if path.is_dir()]
        self.assertTrue(backups)
        backup_package = backups[0]
        self.assertTrue((backup_package / "current" / "daily.csv").is_file())
        self.assertTrue((backup_package / "dataset_card.md").is_file())
        self.assertTrue((backup_package / "schema.yaml").is_file())
        self.assertTrue((context.qa_root / "checksum_manifest.json").is_file())
        for stage in ("prepare", "ingest", "qa", "publish"):
            self.assertTrue((context.sandbox_root / "logs" / f"{stage}_summary.json").is_file())
            self.assertTrue((context.sandbox_root / "logs" / f"{stage}_events.jsonl").is_file())

    def test_prepare_is_restartable_and_skips_successful_requests(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="run-restart",
            use_fake=True,
        )

        first = prepare_fake_raw(context)
        second = prepare_fake_raw(context)

        self.assertEqual(first["prepared"], 1)
        self.assertEqual(second["prepared"], 0)
        self.assertEqual(second["skipped"], 1)
        ledger = read_json(context.prepare_ledger_path)
        request = next(iter(ledger["requests"].values()))
        self.assertIsNotNone(request["cache_path"])
        self.assertTrue((context.repo_root / request["cache_path"]).is_file())

    def test_prepare_can_restore_sandbox_raw_from_cache(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="run-restore-cache",
            use_fake=True,
        )

        first = prepare_fake_raw(context)
        sandbox_raw = next(context.raw_root.glob("*.json"))
        sandbox_raw.unlink()
        second = prepare_fake_raw(context)

        self.assertEqual(first["prepared"], 1)
        self.assertEqual(second["prepared"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertTrue(any(context.raw_root.glob("*.json")))

    def test_daily_range_scheduler_batches_symbols_under_row_limit(self) -> None:
        symbols = [f"{index:06d}.SZ" for index in range(300)]
        expected_trade_dates = [f"2024{index:04d}" for index in range(1, 2428)]
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=symbols,
            trade_dates=[],
            run_id="run-scheduled-range",
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

        self.assertEqual(manifest["request_plan"]["request_count"], 122)
        self.assertEqual(manifest["request_plan"]["max_estimated_rows_per_request"], 6000)
        self.assertTrue(all(request["estimated_max_rows"] <= 6000 for request in ledger["requests"].values()))
        self.assertEqual(set(manifest["request_plan"]["modes"]), {"symbol_range_batch"})

    def test_trade_date_all_ingest_filters_to_requested_symbols(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="run-trade-date-all-filter",
            use_fake=True,
            extras={"daily_request_strategy": "trade_date_all"},
        )
        prepare_fake_raw(context)
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        extra_row = dict(payload["items"][0])
        extra_row["ts_code"] = "600000.SH"
        payload["items"].append(extra_row)
        payload["row_count"] = len(payload["items"])
        write_json(raw_path, payload)

        report = ingest_prepared_raw(context)

        self.assertEqual(report["prepared_rows"], 1)
        self.assertTrue((context.sandbox_root / "logs" / "ingest_summary.json").is_file())
        self.assertTrue((context.sandbox_root / "logs" / "ingest_events.jsonl").is_file())

    def test_duplicate_staged_rows_block_qa_and_publish(self) -> None:
        context = self.create_prepared_context("run-duplicate")
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"].append(dict(payload["items"][0]))
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        self.assertTrue((context.sandbox_root / "logs" / "qa_summary.json").is_file())
        self.assertTrue((context.sandbox_root / "logs" / "qa_events.jsonl").is_file())
        validation = read_json(context.qa_root / "validation_report.json")
        self.assertTrue(any("duplicate primary key" in error for error in validation["errors"]))
        with self.assertRaises(RuntimeError):
            publish_sandbox(context)

    def test_ohlc_contradiction_blocks_qa(self) -> None:
        context = self.create_prepared_context("run-ohlc")
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"][0]["high"] = "1"
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        validation = read_json(context.qa_root / "validation_report.json")
        self.assertTrue(any("high is lower" in error for error in validation["errors"]))

    def test_unknown_missingness_blocks_until_accepted(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=["000001.SZ", "600001.SH"],
            trade_dates=["20240506"],
            run_id="run-missing",
            use_fake=True,
        )
        prepare_fake_raw(context)
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"] = [item for item in payload["items"] if item["ts_code"] != "600001.SH"]
        payload["row_count"] = len(payload["items"])
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        blocked_status = run_qa(context)
        self.assertFalse(blocked_status["passed"])

        write_json(
            context.accepted_missingness_path,
            {
                "accepted": [
                    {
                        "ts_code": "600001.SH",
                        "trade_date": "20240506",
                        "reason": "suspension",
                        "status": "accepted",
                    }
                ]
            },
        )
        accepted_status = run_qa(context)
        self.assertTrue(accepted_status["passed"])

    def test_wildcard_missingness_acceptance_can_clear_unknowns(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=["000001.SZ", "600001.SH"],
            trade_dates=["20240506"],
            run_id="run-missing-wildcard",
            use_fake=True,
        )
        prepare_fake_raw(context)
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"] = [item for item in payload["items"] if item["ts_code"] != "600001.SH"]
        payload["row_count"] = len(payload["items"])
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        write_json(
            context.accepted_missingness_path,
            {
                "accepted": [
                    {
                        "ts_code": "*",
                        "trade_date": "*",
                        "reason": "outside_scope",
                        "status": "accepted",
                    }
                ]
            },
        )

        status = run_qa(context)
        self.assertTrue(status["passed"])

    def test_trade_calendar_closed_day_accepts_missing_daily_row(self) -> None:
        self.write_trade_calendar("SSE", [("20240506", "0")])
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=["600001.SH"],
            trade_dates=["20240506"],
            run_id="run-calendar-holiday",
            use_fake=True,
        )
        prepare_fake_raw(context)
        ledger = read_json(context.prepare_ledger_path)
        request = ledger["requests"]["daily:20240506:600001.SH"]
        request["status"] = "failed"
        request["raw_path"] = None
        write_json(context.prepare_ledger_path, ledger)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertTrue(status["passed"])
        missingness = read_json(context.qa_root / "missingness_report.json")
        self.assertFalse(missingness["blocks_publish"])
        self.assertEqual(missingness["missing"][0]["reason"], "market_holiday")

    def test_trade_calendar_open_day_missing_daily_row_blocks(self) -> None:
        self.write_trade_calendar("SSE", [("20240506", "1")])
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=["600001.SH"],
            trade_dates=["20240506"],
            run_id="run-calendar-open-missing",
            use_fake=True,
        )
        prepare_fake_raw(context)
        ledger = read_json(context.prepare_ledger_path)
        request = ledger["requests"]["daily:20240506:600001.SH"]
        request["status"] = "failed"
        request["raw_path"] = None
        write_json(context.prepare_ledger_path, ledger)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        missingness = read_json(context.qa_root / "missingness_report.json")
        self.assertTrue(missingness["blocks_publish"])
        self.assertEqual(missingness["missing"][0]["reason"], "unknown")

    def test_daily_coverage_renders_symbol_ranges(self) -> None:
        coverage = published_coverage("tushare_daily", self.repo_root / "published" / "datasets" / "tushare" / "daily" / "current")
        rendered = render_coverage_block("tushare_daily", coverage)

        self.assertIn("  symbol_ranges:", rendered)
        self.assertIn("    000001.SZ:", rendered)
        self.assertTrue(any(line.startswith("      start_date:") for line in rendered))

    def test_failed_publish_before_final_rename_leaves_current_unchanged(self) -> None:
        context, _ = run_full_pipeline(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="run-first",
            use_fake=True,
        )
        current_file = context.dataset_root / "current" / "daily.csv"
        before = current_file.read_text(encoding="utf-8")

        second = self.create_prepared_context("run-failed-publish", trade_date="20240507")
        ingest_prepared_raw(second)
        run_qa(second)

        with self.assertRaises(RuntimeError):
            publish_sandbox(second, fail_before_final_rename=True)

        after = current_file.read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def create_prepared_context(self, run_id: str, trade_date: str = "20240506"):
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_daily",
            symbols=["000001.SZ"],
            trade_dates=[trade_date],
            run_id=run_id,
            use_fake=True,
        )
        prepare_fake_raw(context)
        return context

    def write_trade_calendar(self, exchange: str, rows: list[tuple[str, str]]) -> None:
        current_dir = self.repo_root / "published" / "datasets" / "tushare" / "trade_cal" / "current" / f"exchange={exchange}"
        current_dir.mkdir(parents=True, exist_ok=True)
        with (current_dir / "trade_calendar.csv").open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=["exchange", "cal_date", "is_open", "pretrade_date"])
            writer.writeheader()
            for cal_date, is_open in rows:
                writer.writerow({"exchange": exchange, "cal_date": cal_date, "is_open": is_open, "pretrade_date": ""})
