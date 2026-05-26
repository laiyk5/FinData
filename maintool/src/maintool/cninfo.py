from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STOCK_LIST_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_STATIC_BASE = "https://static.cninfo.com.cn/"
CNINFO_DETAIL_BASE = "https://www.cninfo.com.cn/new/disclosure/detail"
CNINFO_REFERER = "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"
CNINFO_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)

REPORT_TYPE_PATTERNS = {
    "semiannual": ("半年度报告", "半年报"),
    "q1": ("第一季度报告", "一季度报告", "一季报"),
    "q3": ("第三季度报告", "三季度报告", "三季报"),
    "annual": ("年度报告", "年报"),
}
SKIP_TITLE_KEYWORDS = ("摘要", "英文", "取消", "提示性公告")
CORRECTION_TITLE_KEYWORDS = ("更正", "修订", "更新")


class CninfoProviderError(RuntimeError):
    def __init__(self, message: str, error_type: str, retryable: bool) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable


@dataclass(frozen=True)
class CninfoResponse:
    raw_response: dict[str, Any]

    @property
    def announcements(self) -> list[dict[str, Any]]:
        items = self.raw_response.get("announcements") or []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]


Transport = Callable[[Request, float], bytes]


def default_transport(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_cninfo_announcements(
    request_params: dict[str, Any],
    transport: Transport | None = None,
    timeout: float = 30.0,
) -> CninfoResponse:
    body = urlencode(cninfo_form_params(request_params)).encode("utf-8")
    request = Request(
        CNINFO_QUERY_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.cninfo.com.cn",
            "Referer": CNINFO_REFERER,
            "User-Agent": CNINFO_USER_AGENT,
        },
        method="POST",
    )

    try:
        response_body = (transport or default_transport)(request, timeout)
    except HTTPError as exc:
        error_type = "blocked" if exc.code in {403, 429} else ("server" if exc.code >= 500 else "unknown")
        raise CninfoProviderError(f"HTTP {exc.code}: {exc.reason}", error_type, retryable=error_type == "server") from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise CninfoProviderError(str(exc), "network", retryable=True) from exc

    text = response_body.decode("utf-8", errors="replace").strip()
    if not text.startswith("{"):
        error_type = "blocked" if looks_like_blocked_html(text) else "server"
        raise CninfoProviderError("Cninfo returned non-JSON response.", error_type, retryable=False)

    try:
        response = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CninfoProviderError("Cninfo returned invalid JSON.", "server", retryable=True) from exc

    if isinstance(response, dict) and response.get("classifiedAnnouncements") is not None:
        # The website can return grouped announcements for broad searches. Keep v1 strict.
        raise CninfoProviderError("Cninfo returned grouped announcements; narrow the request.", "unexpected_shape", False)
    if not isinstance(response, dict):
        raise CninfoProviderError("Cninfo returned a non-object JSON response.", "server", True)
    return CninfoResponse(raw_response=response)


def fetch_cninfo_org_id_map(transport: Transport | None = None, timeout: float = 30.0) -> dict[str, str]:
    request = Request(
        CNINFO_STOCK_LIST_URL,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": CNINFO_REFERER,
            "User-Agent": CNINFO_USER_AGENT,
        },
        method="GET",
    )
    try:
        response_body = (transport or default_transport)(request, timeout)
    except HTTPError as exc:
        error_type = "blocked" if exc.code in {403, 429} else ("server" if exc.code >= 500 else "unknown")
        raise CninfoProviderError(f"HTTP {exc.code}: {exc.reason}", error_type, retryable=error_type == "server") from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise CninfoProviderError(str(exc), "network", retryable=True) from exc

    try:
        payload = json.loads(response_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CninfoProviderError("Cninfo stock list returned invalid JSON.", "server", retryable=True) from exc

    rows = payload.get("stockList") or []
    if not isinstance(rows, list):
        raise CninfoProviderError("Cninfo stock list has unexpected shape.", "server", retryable=True)
    mapping: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "")
        org_id = str(row.get("orgId") or "")
        if code and org_id:
            mapping[f"{code}.{exchange_suffix_for_code(code)}"] = org_id
    return mapping


def exchange_suffix_for_code(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return "SH"
    if code.startswith(("8", "4")):
        return "BJ"
    return "SZ"


def cninfo_form_params(request: dict[str, Any]) -> dict[str, str]:
    ts_code = str(request.get("ts_code", ""))
    stock_code = str(request.get("stock_code") or ts_code.split(".", 1)[0])
    org_id = str(request.get("org_id") or "")
    stock = f"{stock_code},{org_id}" if org_id else stock_code
    year = str(request["report_year"])
    return {
        "pageNum": str(request.get("page_num") or 1),
        "pageSize": str(request.get("page_size") or 30),
        "column": str(request.get("stock_exchange") or ""),
        "tabName": "fulltext",
        "plate": "",
        "stock": stock,
        "searchkey": "",
        "secid": "",
        "category": cninfo_category_filter(str(request.get("report_types") or "")),
        "trade": "",
        "seDate": f"{year}-01-01~{year}-12-31",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }


def cninfo_category_filter(report_types: str) -> str:
    selected = {item.strip() for item in report_types.split(",") if item.strip()}
    category_map = {
        "annual": "category_ndbg_szsh",
        "semiannual": "category_bndbg_szsh",
        "q1": "category_yjdbg_szsh",
        "q3": "category_sjdbg_szsh",
    }
    return ";".join(category_map[item] for item in ("annual", "semiannual", "q1", "q3") if item in selected)


def normalize_cninfo_announcement(
    request: dict[str, Any],
    announcement: dict[str, Any],
    seen_at: str | None = None,
) -> dict[str, str] | None:
    title = clean_title(str(announcement.get("announcementTitle") or announcement.get("title") or ""))
    report_type = classify_report_type(title)
    if not report_type:
        return None

    seen_at = seen_at or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    announcement_id = str(announcement.get("announcementId") or announcement.get("id") or "")
    adjunct_url = str(announcement.get("adjunctUrl") or "")
    publish_time = announcement.get("announcementTime") or announcement.get("publishTime") or ""
    announcement_date = normalize_publish_date(publish_time)
    ts_code = str(request.get("ts_code", ""))
    stock_code = str(announcement.get("secCode") or request.get("stock_code") or ts_code.split(".", 1)[0])

    return {
        "universe_id": str(request.get("universe_id", "")),
        "symbol_selector": str(request.get("symbol_selector", "")),
        "symbol_selector_resolved_at": str(request.get("symbol_selector_resolved_at", "")),
        "source": "cninfo",
        "announcement_id": announcement_id,
        "stock_code": stock_code,
        "stock_exchange": str(request.get("stock_exchange", "")),
        "ts_code": ts_code,
        "sec_name": str(announcement.get("secName") or ""),
        "org_id": str(request.get("org_id") or announcement.get("orgId") or ""),
        "report_type": report_type,
        "report_year": infer_report_year(title, announcement_date, str(request.get("report_year", ""))),
        "period_end": period_end_for(report_type, infer_report_year(title, announcement_date, str(request.get("report_year", "")))),
        "announcement_title": title,
        "announcement_date": announcement_date,
        "pdf_url": normalize_pdf_url(adjunct_url),
        "source_detail_url": source_detail_url(announcement_id, stock_code),
        "source_category": str(announcement.get("category") or request.get("report_types") or ""),
        "is_correction": bool_string(contains_any(title, CORRECTION_TITLE_KEYWORDS)),
        "is_summary": bool_string("摘要" in title),
        "is_english": bool_string("英文" in title),
        "is_cancelled": bool_string("取消" in title or "作废" in title),
        "version_no": "",
        "latest_version": "",
        "raw_adjunct_url": adjunct_url,
        "first_seen_at": seen_at,
        "last_seen_at": seen_at,
    }


def should_keep_report_row(row: dict[str, str]) -> bool:
    title = row.get("announcement_title", "")
    if contains_any(title, SKIP_TITLE_KEYWORDS):
        return False
    return row.get("report_type") in REPORT_TYPE_PATTERNS


def finalize_report_versions(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault((row.get("ts_code", ""), row.get("report_year", ""), row.get("report_type", "")), []).append(row)

    for group_rows in groups.values():
        group_rows.sort(key=lambda row: (row.get("announcement_date", ""), row.get("announcement_id", "")))
        for index, row in enumerate(group_rows, start=1):
            row["version_no"] = str(index)
            row["latest_version"] = "true" if index == len(group_rows) else "false"
    return rows


def classify_report_type(title: str) -> str:
    for report_type, patterns in REPORT_TYPE_PATTERNS.items():
        if contains_any(title, patterns):
            return report_type
    return ""


def clean_title(title: str) -> str:
    return title.replace("<em>", "").replace("</em>", "").strip()


def contains_any(value: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in value for keyword in keywords)


def normalize_pdf_url(adjunct_url: str) -> str:
    if not adjunct_url:
        return ""
    if adjunct_url.startswith("http://") or adjunct_url.startswith("https://"):
        return adjunct_url.replace("http://www.cninfo.com.cn/", CNINFO_STATIC_BASE)
    return CNINFO_STATIC_BASE + adjunct_url.lstrip("/")


def source_detail_url(announcement_id: str, stock_code: str) -> str:
    if not announcement_id:
        return ""
    query = urlencode({"announcementId": announcement_id, "stockCode": stock_code})
    return f"{CNINFO_DETAIL_BASE}?{query}"


def normalize_publish_date(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, timezone.utc).strftime("%Y%m%d")
    text = str(value or "")
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    return ""


def infer_report_year(title: str, announcement_date: str, fallback: str) -> str:
    for index, character in enumerate(title):
        if character == "年" and index >= 4:
            candidate = title[index - 4 : index]
            if candidate.isdigit():
                return candidate
    if fallback:
        return fallback
    if announcement_date:
        return str(int(announcement_date[:4]) - 1)
    return ""


def period_end_for(report_type: str, report_year: str) -> str:
    suffix = {
        "annual": "1231",
        "semiannual": "0630",
        "q1": "0331",
        "q3": "0930",
    }.get(report_type, "")
    return f"{report_year}{suffix}" if report_year and suffix else ""


def bool_string(value: bool) -> str:
    return "true" if value else "false"


def looks_like_blocked_html(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("captcha", "verify", "forbidden", "访问", "验证"))
