from __future__ import annotations

import os
import random
import re
import shutil
import time
import hashlib
from pathlib import Path
from typing import Any

from .dataset_specs import (
    REPORT_CATALOG_FIELDS,
    TUSHARE_INDEX_WEIGHT_FIELDS,
    get_spec,
    parse_symbols,
    request_file_stem as dataset_request_file_stem,
)
from .moneyflow import FIELDS as MONEYFLOW_FIELDS, fake_moneyflow_rows
from .cninfo import CninfoProviderError, fetch_cninfo_announcements, normalize_cninfo_announcement
from .jsonio import write_json
from .run_sandbox import (
    RunContext,
    load_prepare_ledger,
    load_run_manifest,
    mark_step,
    utc_stamp,
)
from .stage_logs import append_stage_event, write_stage_summary
from .tushare_daily import FIELDS, fake_daily_rows
from .tushare_daily_basic import FIELDS as DAILY_BASIC_FIELDS, fake_daily_basic_rows
from .stk_factor_pro import FIELDS as STK_FACTOR_PRO_FIELDS, fake_stk_factor_pro_rows
from .trade_calendar import FIELDS as TRADE_CALENDAR_FIELDS, write_mock_trade_calendar_response
from .tushare_http import (
    TushareProviderError,
    Transport,
    fetch_daily,
    fetch_daily_basic,
    fetch_raw_index_weight,
    fetch_moneyflow,
    fetch_stk_factor_pro,
    fetch_trade_cal,
)


def prepare_raw(context: RunContext, transport: Transport | None = None) -> dict[str, Any]:
    manifest = load_run_manifest(context)
    provider = manifest["provider"]
    if provider in {"fake", "mock"}:
        return prepare_provider_raw(context, fetcher=lambda request: fetch_mock_response(context, request))
    if provider == "tushare":
        token = os.environ.get("TUSHARE_API_KEY", "")
        if not token:
            raise RuntimeError("TUSHARE_API_KEY is required for provider=tushare.")
        return prepare_provider_raw(
            context,
            fetcher=lambda request: fetch_tushare_response(context, request, token, transport),
        )
    if provider == "cninfo":
        return prepare_provider_raw(
            context,
            fetcher=lambda request: fetch_cninfo_response(context, request, transport),
        )
    raise RuntimeError(f"Unsupported provider: {provider}")


def prepare_fake_raw(context: RunContext) -> dict[str, Any]:
    return prepare_provider_raw(context, fetcher=lambda request: fetch_mock_response(context, request))


def prepare_provider_raw(context: RunContext, fetcher) -> dict[str, Any]:
    manifest = load_run_manifest(context)
    ledger = load_prepare_ledger(context)
    provider = manifest["provider"]
    settings = manifest["request_settings"]
    max_retries = max(1, int(settings["max_retries"]))
    rate_limit_seconds = float(settings["rate_limit_seconds"])
    retry_backoff_seconds = float(settings["retry_backoff_seconds"])
    jitter_seconds = parse_jitter_seconds(settings.get("jitter_seconds"))
    request_budget = int(settings.get("request_budget") or 0)
    requests = list(ledger["requests"].values())
    started_at = time.monotonic()
    progress_enabled = manifest.get("provider") == "tushare" or len(requests) > 20

    summary = {
        "run_id": context.run_id,
        "prepared": 0,
        "skipped": 0,
        "failed": 0,
        "requests": len(requests),
    }

    if progress_enabled:
        record_prepare_progress(context, "start", 0, len(requests), summary, None, started_at)

    attempted_requests = 0
    for request_index, request in enumerate(requests, start=1):
        if request_budget and attempted_requests >= request_budget:
            if progress_enabled:
                record_prepare_progress(context, "budget_exhausted", request_index - 1, len(requests), summary, request, started_at)
            break
        if request["status"] == "success" and request["raw_path"]:
            raw_path = context.sandbox_root / request["raw_path"]
            if raw_path.is_file():
                summary["skipped"] += 1
                if progress_enabled:
                    record_prepare_progress(context, "skip", request_index, len(requests), summary, request, started_at)
                continue
            if restore_cached_raw(context, provider, request):
                write_json(context.prepare_ledger_path, ledger)
                summary["skipped"] += 1
                if progress_enabled:
                    record_prepare_progress(context, "restore_from_cache", request_index, len(requests), summary, request, started_at)
                continue

        attempted_requests += 1
        for attempt_index in range(max_retries):
            request["attempts"] += 1
            request["updated_at"] = utc_stamp()
            if progress_enabled:
                record_prepare_progress(context, "request", request_index, len(requests), summary, request, started_at)
            try:
                raw_path, row_count = fetcher(request)
            except KeyboardInterrupt:
                request["status"] = "pending"
                request["last_error"] = "Interrupted during prepare."
                request["error_type"] = "interrupted"
                record_attempt(request, success=False, error="Interrupted during prepare.", error_type="interrupted")
                write_json(context.prepare_ledger_path, ledger)
                if progress_enabled:
                    record_prepare_progress(context, "interrupted", request_index, len(requests), summary, request, started_at)
                write_prepare_summary(context, summary, "interrupted", started_at)
                raise
            except TushareProviderError as exc:
                request["status"] = "failed"
                request["last_error"] = str(exc)
                request["error_type"] = exc.error_type
                record_attempt(request, success=False, error=str(exc), error_type=exc.error_type)
                write_json(context.prepare_ledger_path, ledger)
                if not exc.retryable:
                    break
                if attempt_index < max_retries - 1:
                    sleep_for_retry(retry_backoff_seconds, attempt_index)
            except CninfoProviderError as exc:
                request["status"] = "failed"
                request["last_error"] = str(exc)
                request["error_type"] = exc.error_type
                record_attempt(request, success=False, error=str(exc), error_type=exc.error_type)
                write_json(context.prepare_ledger_path, ledger)
                if exc.error_type == "blocked":
                    summary["failed"] += 1
                    if progress_enabled:
                        record_prepare_progress(context, "blocked", request_index, len(requests), summary, request, started_at)
                    write_prepare_summary(context, summary, "blocked", started_at)
                    return summary
                if not exc.retryable:
                    break
                if attempt_index < max_retries - 1:
                    sleep_for_retry(retry_backoff_seconds, attempt_index)
            except Exception as exc:  # pragma: no cover - defensive path for real providers later.
                request["status"] = "failed"
                request["last_error"] = str(exc)
                request["error_type"] = "unknown"
                record_attempt(request, success=False, error=str(exc), error_type="unknown")
                write_json(context.prepare_ledger_path, ledger)
                break
            else:
                cache_path = persist_raw_to_cache(context, provider, request, raw_path)
                request["status"] = "success"
                request["raw_path"] = str(raw_path.relative_to(context.sandbox_root))
                request["cache_path"] = str(cache_path.relative_to(context.repo_root))
                request["last_error"] = None
                request["error_type"] = None
                request["row_count"] = row_count
                record_attempt(request, success=True, error=None, error_type=None)
                write_json(context.prepare_ledger_path, ledger)
                summary["prepared"] += 1
                if progress_enabled:
                    record_prepare_progress(context, "success", request_index, len(requests), summary, request, started_at)
                break

        if request["status"] != "success":
            summary["failed"] += 1
            if progress_enabled:
                record_prepare_progress(context, "failed", request_index, len(requests), summary, request, started_at)

        write_json(context.prepare_ledger_path, ledger)
        sleep_for_rate_limit(rate_limit_seconds, jitter_seconds)

    if summary["failed"] == 0:
        mark_step(context, "prepared")
    if progress_enabled:
        record_prepare_progress(context, "done", len(requests), len(requests), summary, None, started_at)
    write_prepare_summary(context, summary, "completed", started_at)
    return summary


def record_prepare_progress(
    context: RunContext,
    event: str,
    index: int,
    total: int,
    summary: dict[str, Any],
    request: dict[str, Any] | None,
    started_at: float,
) -> None:
    elapsed = time.monotonic() - started_at
    event_record = prepare_progress_record(event, index, total, summary, request, elapsed)
    append_prepare_event(context, event_record)
    key = request.get("key", "-") if request else "-"
    status = request.get("status", "-") if request else "-"
    attempts = request.get("attempts", "-") if request else "-"
    rows = request.get("row_count", "-") if request else "-"
    print(
        "[prepare] "
        f"{event} {index}/{total} "
        f"prepared={summary['prepared']} skipped={summary['skipped']} failed={summary['failed']} "
        f"status={status} attempts={attempts} rows={rows} elapsed={elapsed:.1f}s key={key}",
        flush=True,
    )


def prepare_progress_record(
    event: str,
    index: int,
    total: int,
    summary: dict[str, Any],
    request: dict[str, Any] | None,
    elapsed: float,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "event": event,
        "created_at": utc_stamp(),
        "index": index,
        "total": total,
        "prepared": summary["prepared"],
        "skipped": summary["skipped"],
        "failed": summary["failed"],
        "elapsed_seconds": round(elapsed, 3),
    }
    if request:
        record.update(
            {
                "request_key": request.get("key"),
                "status": request.get("status"),
                "attempts": request.get("attempts"),
                "row_count": request.get("row_count"),
                "error_type": request.get("error_type"),
                "last_error": request.get("last_error"),
                "raw_path": request.get("raw_path"),
            }
        )
    return record


def append_prepare_event(context: RunContext, event_record: dict[str, Any]) -> None:
    append_stage_event(context, "prepare", event_record)


def write_prepare_summary(context: RunContext, summary: dict[str, Any], status: str, started_at: float) -> None:
    write_stage_summary(
        context,
        "prepare",
        {
            **summary,
            "ledger": str(context.prepare_ledger_path.relative_to(context.sandbox_root)),
        },
        status=status,
        elapsed_seconds=time.monotonic() - started_at,
    )


def record_attempt(request: dict[str, Any], success: bool, error: str | None, error_type: str | None) -> None:
    request.setdefault("attempt_history", []).append(
        {
            "attempt": request["attempts"],
            "success": success,
            "error": error,
            "error_type": error_type,
            "finished_at": utc_stamp(),
        }
    )


def sleep_for_retry(base_seconds: float, attempt_index: int) -> None:
    if base_seconds <= 0:
        return
    delay = base_seconds * (2**attempt_index) + random.uniform(0, min(base_seconds, 0.25))
    time.sleep(delay)


def parse_jitter_seconds(value: object) -> tuple[float, float] | None:
    if not value:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return float(value[0]), float(value[1])
    text = str(value)
    if "," not in text:
        return (0.0, float(text))
    left, right = text.split(",", 1)
    return float(left), float(right)


def sleep_for_rate_limit(base_seconds: float, jitter_seconds: tuple[float, float] | None) -> None:
    delay = base_seconds
    if jitter_seconds:
        delay += random.uniform(jitter_seconds[0], jitter_seconds[1])
    if delay > 0:
        time.sleep(delay)


def fetch_mock_response(context: RunContext, request: dict[str, Any]) -> tuple[Path, int]:
    if context.dataset_name == "trade_calendar":
        return write_mock_trade_calendar_response(context.raw_root, request)
    if context.dataset_name == "tushare_index_weight":
        return write_mock_index_weight_response(context.raw_root, request)
    if context.dataset_name == "report_catalog":
        return write_mock_report_catalog_response(context.raw_root, request)
    return fetch_fake_response(context, request)


def fetch_fake_response(context: RunContext, request: dict[str, Any]) -> tuple[Path, int]:
    if request.get("start_date") and request.get("end_date"):
        raw_path, row_count = write_fake_range_response(context, request)
        return raw_path, row_count
    raw_path, row_count = write_fake_response(context, request)
    return raw_path, row_count


def request_target_symbols(request: dict[str, Any]) -> list[str]:
    return list(request.get("symbols") or parse_symbols(request.get("ts_code", "")))


def persist_raw_to_cache(context: RunContext, provider: str, request: dict[str, Any], raw_path: Path) -> Path:
    api_name = str(request.get("api") or context.dataset_name)
    cache_path = build_cache_path(context, provider, api_name, request)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if raw_path.resolve() != cache_path.resolve():
        shutil.copyfile(raw_path, cache_path)
    return cache_path


def restore_cached_raw(context: RunContext, provider: str, request: dict[str, Any]) -> bool:
    cache_path = resolve_cache_path(context, provider, request)
    if cache_path is None or not cache_path.is_file():
        return False
    sandbox_raw_path = context.raw_root / cache_path.name
    sandbox_raw_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cache_path, sandbox_raw_path)
    request["raw_path"] = str(sandbox_raw_path.relative_to(context.sandbox_root))
    request["cache_path"] = str(cache_path.relative_to(context.repo_root))
    request["updated_at"] = utc_stamp()
    return True


def resolve_cache_path(context: RunContext, provider: str, request: dict[str, Any]) -> Path | None:
    cache_path_text = request.get("cache_path")
    if cache_path_text:
        return context.repo_root / cache_path_text
    api_name = str(request.get("api") or context.dataset_name)
    return build_cache_path(context, provider, api_name, request)


def build_cache_path(context: RunContext, provider: str, api_name: str, request: dict[str, Any]) -> Path:
    request_id = safe_cache_token(str(request.get("key") or dataset_request_file_stem(context.dataset_name, request)))
    return context.cache_root / provider / api_name / f"{request_id}.json"


def safe_cache_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "request"
    if len(token) <= 180:
        return token
    digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:16]
    return f"{token[:80]}_{digest}"


def fake_rows_for_dataset(dataset_name: str, symbols: list[str], trade_date: str) -> list[dict[str, str]]:
    if dataset_name == "tushare_daily_basic":
        return [row.as_dict() for row in fake_daily_basic_rows(symbols, trade_date)]
    if dataset_name == "tushare_stk_factor_pro":
        return fake_stk_factor_pro_rows(symbols, trade_date)
    if dataset_name == "tushare_moneyflow":
        return fake_moneyflow_rows(symbols, trade_date)
    return [row.as_dict() for row in fake_daily_rows(symbols, trade_date)]


def fake_fields_for_dataset(dataset_name: str) -> tuple[str, ...]:
    if dataset_name == "tushare_daily_basic":
        return DAILY_BASIC_FIELDS
    if dataset_name == "tushare_stk_factor_pro":
        return STK_FACTOR_PRO_FIELDS
    if dataset_name == "tushare_moneyflow":
        return MONEYFLOW_FIELDS
    return FIELDS


def write_fake_range_response(context: RunContext, request: dict[str, Any]) -> tuple[Path, int]:
    rows = []
    trade_dates = request.get("expected_trade_dates") or [request["end_date"]]
    for trade_date in trade_dates:
        rows.extend(fake_rows_for_dataset(context.dataset_name, request_target_symbols(request), trade_date))
    payload = {
        "provider": "fake_tushare",
        "api": request.get("api", "daily"),
        "request_mode": request.get("request_mode"),
        "start_date": request["start_date"],
        "end_date": request["end_date"],
        "ts_code": request.get("ts_code", ""),
        "symbols": request_target_symbols(request),
        "fields": list(fake_fields_for_dataset(context.dataset_name)),
        "items": rows,
        "row_count": len(rows),
        "prepared_at": utc_stamp(),
    }
    raw_path = context.raw_root / f"{dataset_request_file_stem(context.dataset_name, request)}.json"
    write_json(raw_path, payload)
    return raw_path, len(rows)


def write_fake_response(context: RunContext, request: dict[str, Any]) -> tuple[Path, int]:
    if str(request.get("ts_code", "")).upper().startswith("FAIL"):
        raise RuntimeError(f"Fake provider failure requested for {request['ts_code']}")

    rows = fake_rows_for_dataset(context.dataset_name, request_target_symbols(request), request["trade_date"])
    payload = {
        "provider": "fake_tushare",
        "api": request.get("api", "daily"),
        "request_mode": request.get("request_mode"),
        "trade_date": request["trade_date"],
        "ts_code": request.get("ts_code", ""),
        "symbols": request_target_symbols(request),
        "fields": list(fake_fields_for_dataset(context.dataset_name)),
        "items": rows,
        "row_count": len(rows),
        "prepared_at": utc_stamp(),
    }
    raw_path = context.raw_root / f"{dataset_request_file_stem(context.dataset_name, request)}.json"
    write_json(raw_path, payload)
    return raw_path, len(rows)


def write_mock_index_weight_response(raw_root: Path, request: dict[str, Any]) -> tuple[Path, int]:
    rows = [
        {
            "index_code": request["index_code"],
            "con_code": "600000.SH",
            "trade_date": request["end_date"],
            "weight": "0.62",
        },
        {
            "index_code": request["index_code"],
            "con_code": "000001.SZ",
            "trade_date": request["end_date"],
            "weight": "0.58",
        },
    ]
    payload = {
        "provider": "mock",
        "api": "index_weight",
        "index_code": request["index_code"],
        "start_date": request["start_date"],
        "end_date": request["end_date"],
        "fields": list(TUSHARE_INDEX_WEIGHT_FIELDS),
        "items": rows,
        "row_count": len(rows),
        "prepared_at": utc_stamp(),
    }
    raw_path = raw_root / f"{dataset_request_file_stem('tushare_index_weight', request)}.json"
    write_json(raw_path, payload)
    return raw_path, len(rows)


def write_mock_report_catalog_response(raw_root: Path, request: dict[str, Any]) -> tuple[Path, int]:
    year = str(request["report_year"])
    base = {
        "secCode": request["stock_code"],
        "secName": "示例公司",
        "announcementTime": int(time.mktime(time.strptime(f"{year}0430", "%Y%m%d"))) * 1000,
    }
    announcements = [
        {
            **base,
            "announcementId": f"{request['stock_code']}{year}001",
            "announcementTitle": f"{year}年年度报告摘要",
            "adjunctUrl": f"finalpage/{year}-04-30/{request['stock_code']}{year}001.PDF",
        },
        {
            **base,
            "announcementId": f"{request['stock_code']}{year}002",
            "announcementTitle": f"{year}年年度报告",
            "adjunctUrl": f"finalpage/{year}-04-30/{request['stock_code']}{year}002.PDF",
        },
        {
            **base,
            "announcementId": f"{request['stock_code']}{year}003",
            "announcementTitle": f"{year}年年度报告（修订版）",
            "adjunctUrl": f"finalpage/{year}-05-10/{request['stock_code']}{year}003.PDF",
            "announcementTime": int(time.mktime(time.strptime(f"{year}0510", "%Y%m%d"))) * 1000,
        },
        {
            **base,
            "announcementId": f"{request['stock_code']}{year}004",
            "announcementTitle": f"{year}年半年度报告",
            "adjunctUrl": f"finalpage/{year}-08-30/{request['stock_code']}{year}004.PDF",
            "announcementTime": int(time.mktime(time.strptime(f"{year}0830", "%Y%m%d"))) * 1000,
        },
    ]
    rows = [
        row
        for announcement in announcements
        if (row := normalize_cninfo_announcement(request, announcement, seen_at=utc_stamp())) is not None
    ]
    payload = {
        "provider": "mock",
        "api": "hisAnnouncement/query",
        "request": request,
        "fields": list(REPORT_CATALOG_FIELDS),
        "items": rows,
        "row_count": len(rows),
        "raw_response": {"announcements": announcements},
        "prepared_at": utc_stamp(),
    }
    raw_path = raw_root / f"{dataset_request_file_stem('report_catalog', request)}.json"
    write_json(raw_path, payload)
    return raw_path, len(rows)


def fetch_cninfo_response(
    context: RunContext,
    request: dict[str, Any],
    transport: Transport | None,
) -> tuple[Path, int]:
    response = fetch_cninfo_announcements(request, transport=transport)
    rows = [
        row
        for announcement in response.announcements
        if (row := normalize_cninfo_announcement(request, announcement, seen_at=utc_stamp())) is not None
    ]
    payload = {
        "provider": "cninfo",
        "api": "hisAnnouncement/query",
        "request": request,
        "fields": list(REPORT_CATALOG_FIELDS),
        "items": rows,
        "row_count": len(rows),
        "raw_response": response.raw_response,
        "prepared_at": utc_stamp(),
    }
    raw_path = context.raw_root / f"{dataset_request_file_stem('report_catalog', request)}.json"
    write_json(raw_path, payload)
    return raw_path, len(rows)


def fetch_tushare_response(
    context: RunContext,
    request: dict[str, Any],
    token: str,
    transport: Transport | None,
) -> tuple[Path, int]:
    if context.dataset_name == "trade_calendar":
        response = fetch_trade_cal(
            token=token,
            exchange=request["exchange"],
            start_date=request["start_date"],
            end_date=request["end_date"],
            is_open=request.get("is_open"),
            transport=transport,
        )
        spec = get_spec(context.dataset_name)
        rows = response.rows
        payload = {
            "provider": spec.provider,
            "api": spec.api_name,
            "exchange": request["exchange"],
            "start_date": request["start_date"],
            "end_date": request["end_date"],
            "fields": list(TRADE_CALENDAR_FIELDS),
            "items": rows,
            "row_count": len(rows),
            "prepared_at": utc_stamp(),
        }
        raw_path = context.raw_root / f"trade_cal_{request['exchange']}_{request['start_date']}_{request['end_date']}.json"
        write_json(raw_path, payload)
        return raw_path, len(rows)

    if context.dataset_name == "tushare_index_weight":
        response = fetch_raw_index_weight(
            token=token,
            index_code=request["index_code"],
            start_date=request["start_date"],
            end_date=request["end_date"],
            transport=transport,
        )
        rows = response.rows
        payload = {
            "provider": "tushare",
            "api": "index_weight",
            "request_mode": request.get("request_mode"),
            "index_code": request["index_code"],
            "start_date": request["start_date"],
            "end_date": request["end_date"],
            "fields": list(TUSHARE_INDEX_WEIGHT_FIELDS),
            "items": rows,
            "row_count": len(rows),
            "prepared_at": utc_stamp(),
        }
        raw_path = context.raw_root / f"{dataset_request_file_stem('tushare_index_weight', request)}.json"
        write_json(raw_path, payload)
        return raw_path, len(rows)

    if context.dataset_name == "tushare_stk_factor_pro":
        response = fetch_stk_factor_pro(
            token=token,
            ts_code=request.get("ts_code") or None,
            trade_date=request.get("trade_date"),
            start_date=request.get("start_date"),
            end_date=request.get("end_date"),
            transport=transport,
        )
        rows = response.rows
        payload = {
            "provider": "tushare",
            "api": "stk_factor_pro",
            "request_mode": request.get("request_mode"),
            "trade_date": request.get("trade_date"),
            "start_date": request.get("start_date"),
            "end_date": request.get("end_date"),
            "ts_code": request.get("ts_code", ""),
            "symbols": request_target_symbols(request),
            "fields": list(STK_FACTOR_PRO_FIELDS),
            "items": rows,
            "row_count": len(rows),
            "prepared_at": utc_stamp(),
        }
        raw_path = context.raw_root / f"{dataset_request_file_stem('tushare_stk_factor_pro', request)}.json"
        write_json(raw_path, payload)
        return raw_path, len(rows)

    if context.dataset_name == "tushare_moneyflow":
        response = fetch_moneyflow(
            token=token,
            ts_code=request.get("ts_code") or None,
            trade_date=request.get("trade_date"),
            start_date=request.get("start_date"),
            end_date=request.get("end_date"),
            transport=transport,
        )
        rows = response.rows
        payload = {
            "provider": "tushare",
            "api": "moneyflow",
            "request_mode": request.get("request_mode"),
            "trade_date": request.get("trade_date"),
            "start_date": request.get("start_date"),
            "end_date": request.get("end_date"),
            "ts_code": request.get("ts_code", ""),
            "symbols": request_target_symbols(request),
            "fields": list(MONEYFLOW_FIELDS),
            "items": rows,
            "row_count": len(rows),
            "prepared_at": utc_stamp(),
        }
        raw_path = context.raw_root / f"{dataset_request_file_stem('tushare_moneyflow', request)}.json"
        write_json(raw_path, payload)
        return raw_path, len(rows)

    if context.dataset_name == "tushare_daily_basic":
        response = fetch_daily_basic(
            token=token,
            ts_code=request.get("ts_code") or None,
            trade_date=request.get("trade_date"),
            start_date=request.get("start_date"),
            end_date=request.get("end_date"),
            transport=transport,
        )
        rows = response.rows
        payload = {
            "provider": "tushare",
            "api": "daily_basic",
            "request_mode": request.get("request_mode"),
            "trade_date": request.get("trade_date"),
            "start_date": request.get("start_date"),
            "end_date": request.get("end_date"),
            "ts_code": request.get("ts_code", ""),
            "symbols": request_target_symbols(request),
            "fields": list(DAILY_BASIC_FIELDS),
            "items": rows,
            "row_count": len(rows),
            "prepared_at": utc_stamp(),
        }
        raw_path = context.raw_root / f"{dataset_request_file_stem('tushare_daily_basic', request)}.json"
        write_json(raw_path, payload)
        return raw_path, len(rows)

    response = fetch_daily(
        token=token,
        ts_code=request.get("ts_code") or None,
        trade_date=request.get("trade_date"),
        start_date=request.get("start_date"),
        end_date=request.get("end_date"),
        transport=transport,
    )
    rows = response.rows
    payload = {
        "provider": "tushare",
        "api": "daily",
        "request_mode": request.get("request_mode"),
        "trade_date": request.get("trade_date"),
        "start_date": request.get("start_date"),
        "end_date": request.get("end_date"),
        "ts_code": request.get("ts_code", ""),
        "symbols": request_target_symbols(request),
        "fields": list(FIELDS),
        "items": rows,
        "row_count": len(rows),
        "prepared_at": utc_stamp(),
    }
    raw_path = context.raw_root / f"{dataset_request_file_stem('tushare_daily', request)}.json"
    write_json(raw_path, payload)
    return raw_path, len(rows)
