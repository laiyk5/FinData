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

from maintool.ingest import ingest_prepared_raw
from maintool.jsonio import read_json, write_json
from maintool.pipeline import run_full_pipeline
from maintool.prepare import prepare_fake_raw, prepare_raw
from maintool.qa import run_qa, validate_moneyflow_csv
from maintool.run_sandbox import create_run_sandbox


REPO_ROOT = Path(__file__).resolve().parents[2]


class MoneyflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        shutil.copytree(
            REPO_ROOT / "workspace" / "published" / "datasets" / "tushare" / "moneyflow",
            self.workspace_root / "published" / "datasets" / "tushare" / "moneyflow",
        )
        shutil.copytree(
            REPO_ROOT / "workspace" / "published" / "datasets" / "tushare" / "trade_cal",
            self.workspace_root / "published" / "datasets" / "tushare" / "trade_cal",
        )
        (self.workspace_root / "sandboxes" / "runs").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_fake_pipeline_publishes(self) -> None:
        context, result = run_full_pipeline(
            workspace_root=self.workspace_root,
            dataset_name="tushare_moneyflow",
            symbols=["000001.SZ", "600000.SH"],
            trade_dates=["20240506"],
            run_id="moneyflow-fake",
            use_fake=True,
        )

        self.assertEqual(result["prepare"]["prepared"], 1)
        self.assertTrue((context.dataset_root / "current" / "moneyflow.csv").is_file())
        self.assertTrue(result["qa"]["passed"])

    def test_real_success_normalizes_raw_json(self) -> None:
        context = create_run_sandbox(
            workspace_root=self.workspace_root,
            dataset_name="tushare_moneyflow",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="moneyflow-real-success",
        )

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            summary = prepare_raw(
                context,
                transport=lambda request, timeout: moneyflow_response([moneyflow_row("000001.SZ", "20240506")]),
            )

        self.assertEqual(summary["prepared"], 1)
        ledger = read_json(context.prepare_ledger_path)
        request = next(request for request in ledger["requests"].values() if request.get("ts_code") == "000001.SZ")
        raw_payload = read_json(context.sandbox_root / request["raw_path"])
        self.assertEqual(raw_payload["api"], "moneyflow")
        self.assertEqual(raw_payload["items"][0]["ts_code"], "000001.SZ")
        self.assertNotIn("secret-token", context.prepare_ledger_path.read_text(encoding="utf-8"))

    def test_permission_error_does_not_retry(self) -> None:
        context = create_run_sandbox(
            workspace_root=self.workspace_root,
            dataset_name="tushare_moneyflow",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="moneyflow-real-permission",
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

    def test_negative_component_blocks_qa(self) -> None:
        context = create_run_sandbox(
            workspace_root=self.workspace_root,
            dataset_name="tushare_moneyflow",
            symbols=["000001.SZ"],
            trade_dates=["20240506"],
            run_id="moneyflow-negative-component",
            use_fake=True,
        )
        prepare_fake_raw(context)
        raw_path = next(context.raw_root.glob("*.json"))
        payload = read_json(raw_path)
        payload["items"][0]["buy_sm_amount"] = "-1"
        write_json(raw_path, payload)

        ingest_prepared_raw(context)
        status = run_qa(context)

        self.assertFalse(status["passed"])
        validation = read_json(context.qa_root / "validation_report.json")
        self.assertTrue(any("buy_sm_amount is negative" in error for error in validation["errors"]))

    def test_duplicate_row_blocks_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "moneyflow.csv"
            row = moneyflow_row("000001.SZ", "20240506")
            with csv_path.open("w", newline="", encoding="utf-8") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=list(row.keys()))
                writer.writeheader()
                writer.writerow(row)
                writer.writerow(row)

            errors = validate_moneyflow_csv(csv_path)

        self.assertTrue(any("duplicate primary key" in error for error in errors))

    def test_blank_historical_numeric_fields_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "moneyflow.csv"
            row = moneyflow_row("000001.SZ", "20100513")
            row["net_mf_vol"] = ""
            row["net_mf_amount"] = ""
            row["sell_elg_vol"] = ""
            row["sell_elg_amount"] = ""
            with csv_path.open("w", newline="", encoding="utf-8") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=list(row.keys()))
                writer.writeheader()
                writer.writerow(row)

            errors = validate_moneyflow_csv(csv_path)

        self.assertEqual(errors, [])


def moneyflow_row(ts_code: str, trade_date: str) -> dict[str, str]:
    return {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "buy_sm_vol": "100",
        "buy_sm_amount": "10.000",
        "sell_sm_vol": "90",
        "sell_sm_amount": "9.000",
        "buy_md_vol": "200",
        "buy_md_amount": "20.000",
        "sell_md_vol": "180",
        "sell_md_amount": "18.000",
        "buy_lg_vol": "300",
        "buy_lg_amount": "30.000",
        "sell_lg_vol": "250",
        "sell_lg_amount": "25.000",
        "buy_elg_vol": "400",
        "buy_elg_amount": "40.000",
        "sell_elg_vol": "350",
        "sell_elg_amount": "35.000",
        "net_mf_vol": "130",
        "net_mf_amount": "13.000",
    }


def moneyflow_response(rows: list[dict[str, str]]) -> bytes:
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
