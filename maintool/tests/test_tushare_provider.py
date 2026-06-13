from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from maintool.cli import maintain_plan
from maintool.ingest import ingest_prepared_raw
from maintool.jsonio import read_json
from maintool.prepare import prepare_raw
from maintool.qa import run_qa
from maintool.run_sandbox import create_run_sandbox


REPO_ROOT = Path(__file__).resolve().parents[2]


def success_response() -> bytes:
    return json.dumps(
        {
            "code": 0,
            "msg": None,
            "data": {
                "fields": [
                    "ts_code",
                    "trade_date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "pre_close",
                    "change",
                    "pct_chg",
                    "vol",
                    "amount",
                ],
                "items": [
                    [
                        "000001.SZ",
                        "20240510",
                        10.0,
                        10.3,
                        9.9,
                        10.2,
                        9.95,
                        0.25,
                        2.5126,
                        100000,
                        102000.0,
                    ]
                ],
            },
        }
    ).encode("utf-8")


def daily_basic_success_response() -> bytes:
    return json.dumps(
        {
            "code": 0,
            "msg": None,
            "data": {
                "fields": [
                    "ts_code",
                    "trade_date",
                    "close",
                    "turnover_rate",
                    "turnover_rate_f",
                    "volume_ratio",
                    "pe",
                    "pe_ttm",
                    "pb",
                    "ps",
                    "ps_ttm",
                    "dv_ratio",
                    "dv_ttm",
                    "total_share",
                    "float_share",
                    "free_share",
                    "total_mv",
                    "circ_mv",
                ],
                "items": [
                    [
                        "000001.SZ",
                        "20240510",
                        10.2,
                        1.2,
                        1.5,
                        0.8,
                        12.3,
                        13.1,
                        1.2,
                        2.3,
                        2.4,
                        1.1,
                        1.2,
                        100000,
                        80000,
                        60000,
                        1020000,
                        816000,
                    ]
                ],
            },
        }
    ).encode("utf-8")


def empty_response() -> bytes:
    return json.dumps(
        {
            "code": 0,
            "msg": None,
            "data": {
                "fields": [
                    "ts_code",
                    "trade_date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "pre_close",
                    "change",
                    "pct_chg",
                    "vol",
                    "amount",
                ],
                "items": [],
            },
        }
    ).encode("utf-8")


def permission_response() -> bytes:
    return json.dumps({"code": 2002, "msg": "权限不足", "data": None}).encode("utf-8")


def rate_limit_response() -> bytes:
    return json.dumps({"code": 5000, "msg": "每分钟访问频次超过限制", "data": None}).encode("utf-8")


class TushareProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        shutil.copytree(REPO_ROOT / "workspace" / "published" / "datasets" / "tushare" / "daily", self.workspace_root / "published" / "datasets" / "tushare" / "daily")
        shutil.copytree(
            REPO_ROOT / "workspace" / "published" / "datasets" / "tushare" / "daily_basic",
            self.workspace_root / "published" / "datasets" / "tushare" / "daily_basic",
        )
        clear_current(self.workspace_root / "published" / "datasets" / "tushare" / "daily")
        clear_current(self.workspace_root / "published" / "datasets" / "tushare" / "daily_basic")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_successful_response_writes_raw_and_ledger_without_token(self) -> None:
        context = self.create_tushare_context("real-success")
        captured_body: dict[str, object] = {}

        def transport(request, timeout):
            captured_body.update(json.loads(request.data.decode("utf-8")))
            return success_response()

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            summary = prepare_raw(context, transport=transport)

        ledger = read_json(context.prepare_ledger_path)
        request = ledger["requests"]["daily:20240510:000001.SZ"]
        raw_path = context.sandbox_root / request["raw_path"]
        raw_payload = read_json(raw_path)

        self.assertEqual(summary["prepared"], 1)
        self.assertEqual(request["status"], "success")
        self.assertEqual(request["row_count"], 1)
        self.assertEqual(raw_payload["provider"], "tushare")
        self.assertNotIn("secret-token", context.run_manifest_path.read_text(encoding="utf-8"))
        self.assertNotIn("secret-token", context.prepare_ledger_path.read_text(encoding="utf-8"))
        self.assertNotIn("secret-token", raw_path.read_text(encoding="utf-8"))
        self.assertEqual(captured_body["token"], "secret-token")

    def test_daily_basic_response_uses_daily_basic_api(self) -> None:
        context = self.create_tushare_context("real-daily-basic-success", dataset_name="tushare_daily_basic")
        captured_body: dict[str, object] = {}

        def transport(request, timeout):
            captured_body.update(json.loads(request.data.decode("utf-8")))
            return daily_basic_success_response()

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            summary = prepare_raw(context, transport=transport)

        ledger = read_json(context.prepare_ledger_path)
        request = ledger["requests"]["daily_basic:20240510:20240510:000001.SZ"]
        raw_path = context.sandbox_root / request["raw_path"]
        raw_payload = read_json(raw_path)

        self.assertEqual(summary["prepared"], 1)
        self.assertEqual(captured_body["api_name"], "daily_basic")
        self.assertEqual(raw_payload["api"], "daily_basic")
        self.assertEqual(request["row_count"], 1)

    def test_empty_response_succeeds_and_missingness_blocks_publish(self) -> None:
        context = self.create_tushare_context("real-empty", trade_date="20990105")

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            summary = prepare_raw(context, transport=lambda request, timeout: empty_response())

        self.assertEqual(summary["prepared"], 1)
        ledger = read_json(context.prepare_ledger_path)
        self.assertEqual(ledger["requests"]["daily:20990105:000001.SZ"]["row_count"], 0)

        ingest_prepared_raw(context)
        status = run_qa(context)
        self.assertFalse(status["passed"])
        missingness = read_json(context.qa_root / "missingness_report.json")
        self.assertEqual(missingness["missing_count"], 1)
        self.assertTrue(missingness["blocks_publish"])

    def test_permission_error_fails_without_retry(self) -> None:
        context = self.create_tushare_context("real-permission")

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            summary = prepare_raw(context, transport=lambda request, timeout: permission_response())

        ledger = read_json(context.prepare_ledger_path)
        request = ledger["requests"]["daily:20240510:000001.SZ"]
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(request["attempts"], 1)
        self.assertEqual(request["error_type"], "permission")

    def test_network_error_retries_and_resume_skips_success(self) -> None:
        context = self.create_tushare_context("real-network-retry", max_retries=2)
        calls = {"count": 0}

        def transport(request, timeout):
            calls["count"] += 1
            if calls["count"] == 1:
                raise URLError("temporary network issue")
            return success_response()

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            first = prepare_raw(context, transport=transport)
            second = prepare_raw(context, transport=transport)

        ledger = read_json(context.prepare_ledger_path)
        request = ledger["requests"]["daily:20240510:000001.SZ"]
        self.assertEqual(first["prepared"], 1)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(request["attempts"], 2)
        self.assertEqual(len(request["attempt_history"]), 2)

    def test_rate_limit_error_retries(self) -> None:
        context = self.create_tushare_context("real-rate-limit", max_retries=2)
        calls = {"count": 0}

        def transport(request, timeout):
            calls["count"] += 1
            if calls["count"] == 1:
                return rate_limit_response()
            return success_response()

        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            summary = prepare_raw(context, transport=transport)

        ledger = read_json(context.prepare_ledger_path)
        request = ledger["requests"]["daily:20240510:000001.SZ"]
        self.assertEqual(summary["prepared"], 1)
        self.assertEqual(request["attempts"], 2)
        self.assertEqual(request["attempt_history"][0]["error_type"], "rate_limit")

    def test_real_provider_succeeds_with_api_key(self) -> None:
        with patch.dict(os.environ, {"TUSHARE_API_KEY": "secret-token"}):
            with redirect_stdout(StringIO()):
                exit_code = maintain_plan(
                    workspace_root=self.workspace_root,
                    dataset_name="tushare_daily",
                    symbols=["000001.SZ"],
                    trade_dates=["20240510"],
                    run_id="with-key",
                    rate_limit_seconds=None,
                    max_retries=3,
                    retry_backoff_seconds=None,
                    use_fake=False,
                )

        self.assertEqual(exit_code, 0)
        self.assertTrue((self.workspace_root / "sandboxes" / "runs" / "tushare_daily" / "with-key").exists())

    def test_real_provider_requires_token(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with redirect_stdout(StringIO()):
                exit_code = maintain_plan(
                    workspace_root=self.workspace_root,
                    dataset_name="tushare_daily",
                    symbols=["000001.SZ"],
                    trade_dates=["20240510"],
                    run_id="no-token",
                    rate_limit_seconds=None,
                    max_retries=3,
                    retry_backoff_seconds=None,
                    use_fake=False,
                )

        self.assertEqual(exit_code, 1)
        self.assertFalse((self.workspace_root / "sandboxes" / "runs" / "tushare_daily" / "no-token").exists())

    def create_tushare_context(
        self,
        run_id: str,
        max_retries: int = 3,
        trade_date: str = "20240510",
        dataset_name: str = "tushare_daily",
    ):
        return create_run_sandbox(
            workspace_root=self.workspace_root,
            dataset_name=dataset_name,
            symbols=["000001.SZ"],
            trade_dates=[trade_date],
            run_id=run_id,
            rate_limit_seconds=0.0,
            max_retries=max_retries,
            retry_backoff_seconds=0.0,
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
