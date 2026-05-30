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


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from maintool.dataset_specs import STK_FACTOR_MAX_ROWS_PER_REQUEST
from maintool.ingest import ingest_prepared_raw
from maintool.jsonio import read_json, write_json
from maintool.pipeline import run_full_pipeline
from maintool.prepare import prepare_raw, prepare_fake_raw
from maintool.qa import (
    classify_history_missingness,
    classify_prelist_missingness,
    run_qa,
    validate_stk_factor_pro_csv,
)
from maintool.run_sandbox import create_run_sandbox


REPO_ROOT = Path(__file__).resolve().parents[2]


class StkFactorProTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        shutil.copytree(
            REPO_ROOT / "published" / "datasets" / "tushare" / "stk_factor_pro",
            self.repo_root / "published" / "datasets" / "tushare" / "stk_factor_pro",
        )
        shutil.copytree(
            REPO_ROOT / "published" / "datasets" / "tushare" / "trade_cal",
            self.repo_root / "published" / "datasets" / "tushare" / "trade_cal",
        )
        (self.repo_root / "sandboxes" / "runs").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_fake_pipeline_publishes(self) -> None:
        context, result = run_full_pipeline(
            repo_root=self.repo_root,
            dataset_name="tushare_stk_factor_pro",
            symbols=["000001.SZ", "600000.SH"],
            trade_dates=["20240506"],
            run_id="stk-factor-fake",
            use_fake=True,
        )

        self.assertEqual(result["prepare"]["prepared"], 2)
        self.assertTrue(
            (context.dataset_root / "current" / "stk_factor_pro.csv").is_file()
        )
        self.assertTrue(result["qa"]["passed"])

    def test_scheduler_respects_10000_row_cap(self) -> None:
        symbols = [f"{index:06d}.SZ" for index in range(300)]
        expected_trade_dates = [f"2024{index:04d}" for index in range(1, 2428)]
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_stk_factor_pro",
            symbols=symbols,
            trade_dates=[],
            run_id="stk-factor-scheduled-range",
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

        self.assertEqual(manifest["request_plan"]["row_limit"], STK_FACTOR_MAX_ROWS_PER_REQUEST)
        self.assertTrue(all(request["estimated_max_rows"] <= STK_FACTOR_MAX_ROWS_PER_REQUEST for request in ledger["requests"].values()))
        self.assertTrue(all(request["api"] == "stk_factor_pro" for request in ledger["requests"].values()))

    def test_real_success_normalizes_raw_json(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_stk_factor_pro",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="stk-factor-real-success",
        )

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            summary = prepare_raw(context, transport=lambda request, timeout: stk_factor_response([stk_factor_row("000001.SZ", "20240506")]))

        self.assertEqual(summary["prepared"], 1)
        ledger = read_json(context.prepare_ledger_path)
        request = next(request for request in ledger["requests"].values() if request.get("ts_code") == "000001.SZ")
        raw_payload = read_json(context.sandbox_root / request["raw_path"])
        self.assertEqual(raw_payload["api"], "stk_factor_pro")
        self.assertEqual(raw_payload["items"][0]["ts_code"], "000001.SZ")
        self.assertNotIn("secret-token", context.prepare_ledger_path.read_text(encoding="utf-8"))

    def test_permission_error_does_not_retry(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_stk_factor_pro",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="stk-factor-real-permission",
        )

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            summary = prepare_raw(
                context,
                transport=lambda request, timeout: json.dumps({"code": 2002, "msg": "权限不足", "data": None}).encode("utf-8"),
            )

        ledger = read_json(context.prepare_ledger_path)
        request = next(request for request in ledger["requests"].values() if request.get("ts_code") == "000001.SZ")
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(request["attempts"], 1)
        self.assertEqual(request["error_type"], "permission")

    def test_ohlc_contradiction_blocks_qa(self) -> None:
        context = create_run_sandbox(
            repo_root=self.repo_root,
            dataset_name="tushare_stk_factor_pro",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="stk-factor-bad-ohlc",
            use_fake=True,
        )
        prepare_fake_raw(context)
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"][0]["high"] = "1"
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        validation = read_json(context.qa_root / "validation_report.json")
        self.assertTrue(any("high is lower" in error for error in validation["errors"]))

    def test_prelist_missingness_is_outside_scope(self) -> None:
        history = {"000001.SZ": {"first": "20170101", "last": "20260522"}}
        self.assertEqual(classify_prelist_missingness("000001.SZ", "20160527", history), "outside_scope")
        self.assertIsNone(classify_prelist_missingness("000001.SZ", "20180101", history))

    def test_internal_missingness_is_suspension(self) -> None:
        history = {"000001.SZ": {"first": "20170101", "last": "20260522"}}
        self.assertEqual(classify_history_missingness("000001.SZ", "20180102", history), "suspension")

    def test_free_share_warning_does_not_block_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "factor.csv"
            row = stk_factor_row("000001.SZ", "20240506")
            row["free_share"] = "90000.000"
            with csv_path.open("w", newline="", encoding="utf-8") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=list(row.keys()))
                writer.writeheader()
                writer.writerow(row)

            errors = validate_stk_factor_pro_csv(csv_path)

        self.assertFalse(any("free_share exceeds float_share" in error for error in errors))


def stk_factor_row(ts_code: str, trade_date: str) -> dict[str, str]:
    row = {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "open": "10.000",
        "open_hfq": "10.500",
        "open_qfq": "9.500",
        "high": "10.300",
        "high_hfq": "10.800",
        "high_qfq": "9.800",
        "low": "9.900",
        "low_hfq": "10.300",
        "low_qfq": "9.400",
        "close": "10.200",
        "close_hfq": "10.700",
        "close_qfq": "9.700",
        "pre_close": "9.950",
        "change": "0.250",
        "pct_chg": "2.5126",
        "vol": "100000",
        "amount": "102000.000",
        "turnover_rate": "1.250",
        "turnover_rate_f": "1.500",
        "volume_ratio": "0.800",
        "pe": "12.500",
        "pe_ttm": "13.100",
        "pb": "1.200",
        "ps": "2.300",
        "ps_ttm": "2.400",
        "dv_ratio": "1.100",
        "dv_ttm": "1.200",
        "total_share": "100000.000",
        "float_share": "80000.000",
        "free_share": "60000.000",
        "total_mv": "1020000.000",
        "circ_mv": "816000.000",
        "adj_factor": "1.000",
        "ma_bfq_5": "9.800",
        "ma_bfq_10": "9.900",
        "ma_bfq_20": "10.250",
        "ema_bfq_5": "9.880",
        "ema_bfq_10": "9.940",
        "ema_bfq_20": "10.280",
        "macd_bfq": "0.120",
        "macd_dea_bfq": "0.080",
        "macd_dif_bfq": "0.040",
        "boll_lower_bfq": "9.200",
        "boll_mid_bfq": "10.200",
        "boll_upper_bfq": "11.200",
        "dmi_adx_bfq": "15.000",
        "dmi_adxr_bfq": "14.000",
        "dmi_mdi_bfq": "10.000",
        "dmi_pdi_bfq": "12.000",
        "kdj_bfq": "45.000",
        "kdj_d_bfq": "40.000",
        "kdj_k_bfq": "50.000",
        "bias1_bfq": "1.200",
        "bias2_bfq": "-0.600",
        "bias3_bfq": "0.300",
        "atr_bfq": "0.800",
    }
    return row


def stk_factor_response(rows: list[dict[str, str]]) -> bytes:
    fields = list(rows[0].keys()) if rows else []
    items = [[row[field] for field in fields] for row in rows]
    return json.dumps(
        {
            "code": 0,
            "msg": None,
            "data": {
                "fields": fields,
                "items": items,
            },
        }
    ).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
