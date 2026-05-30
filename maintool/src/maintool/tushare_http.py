from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .dataset_specs import TRADE_CALENDAR_FIELDS, TUSHARE_INDEX_WEIGHT_FIELDS
from .moneyflow import FIELDS as MONEYFLOW_FIELDS
from .tushare_daily import FIELDS
from .tushare_daily_basic import FIELDS as DAILY_BASIC_FIELDS
from .stk_factor_pro import FIELDS as STK_FACTOR_PRO_FIELDS


TUSHARE_HTTP_URL = "http://api.tushare.pro"
TUSHARE_DAILY_FIELDS = ",".join(FIELDS)
TUSHARE_DAILY_BASIC_FIELDS = ",".join(DAILY_BASIC_FIELDS)
TUSHARE_STK_FACTOR_PRO_FIELDS = ",".join(STK_FACTOR_PRO_FIELDS)
TUSHARE_MONEYFLOW_FIELDS = ",".join(MONEYFLOW_FIELDS)
TUSHARE_TRADE_CAL_FIELDS = "exchange,cal_date,is_open,pretrade_date"
TUSHARE_INDEX_WEIGHT_FIELD_LIST = ",".join(TUSHARE_INDEX_WEIGHT_FIELDS)


class TushareProviderError(RuntimeError):
    def __init__(self, message: str, error_type: str, retryable: bool) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable


@dataclass(frozen=True)
class TushareDailyResponse:
    fields: list[str]
    items: list[list[Any]]
    raw_response: dict[str, Any]
    canonical_fields: tuple[str, ...] = FIELDS

    @property
    def rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self.items:
            row = dict(zip(self.fields, item))
            rows.append({field: row.get(field, "") for field in self.canonical_fields})
        return rows


Transport = Callable[[Request, float], bytes]


def default_transport(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_daily(
    token: str,
    ts_code: str | None,
    trade_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    transport: Transport | None = None,
    timeout: float = 30.0,
) -> TushareDailyResponse:
    params = {}
    if ts_code:
        params["ts_code"] = ts_code
    if trade_date is not None:
        params["trade_date"] = trade_date
    if start_date is not None:
        params["start_date"] = start_date
    if end_date is not None:
        params["end_date"] = end_date
    return fetch_api(
        token=token,
        api_name="daily",
        params=params,
        fields=TUSHARE_DAILY_FIELDS,
        canonical_fields=FIELDS,
        transport=transport,
        timeout=timeout,
    )


def fetch_daily_basic(
    token: str,
    ts_code: str | None,
    trade_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    transport: Transport | None = None,
    timeout: float = 30.0,
) -> TushareDailyResponse:
    params = {}
    if ts_code:
        params["ts_code"] = ts_code
    if trade_date is not None:
        params["trade_date"] = trade_date
    if start_date is not None:
        params["start_date"] = start_date
    if end_date is not None:
        params["end_date"] = end_date
    return fetch_api(
        token=token,
        api_name="daily_basic",
        params=params,
        fields=TUSHARE_DAILY_BASIC_FIELDS,
        canonical_fields=DAILY_BASIC_FIELDS,
        transport=transport,
        timeout=timeout,
    )


def fetch_stk_factor_pro(
    token: str,
    ts_code: str | None,
    trade_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    transport: Transport | None = None,
    timeout: float = 30.0,
) -> TushareDailyResponse:
    params = {}
    if ts_code:
        params["ts_code"] = ts_code
    if trade_date is not None:
        params["trade_date"] = trade_date
    if start_date is not None:
        params["start_date"] = start_date
    if end_date is not None:
        params["end_date"] = end_date
    return fetch_api(
        token=token,
        api_name="stk_factor_pro",
        params=params,
        fields=TUSHARE_STK_FACTOR_PRO_FIELDS,
        canonical_fields=STK_FACTOR_PRO_FIELDS,
        transport=transport,
        timeout=timeout,
    )


def fetch_moneyflow(
    token: str,
    ts_code: str | None,
    trade_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    transport: Transport | None = None,
    timeout: float = 30.0,
) -> TushareDailyResponse:
    params = {}
    if ts_code:
        params["ts_code"] = ts_code
    if trade_date is not None:
        params["trade_date"] = trade_date
    if start_date is not None:
        params["start_date"] = start_date
    if end_date is not None:
        params["end_date"] = end_date
    return fetch_api(
        token=token,
        api_name="moneyflow",
        params=params,
        fields=TUSHARE_MONEYFLOW_FIELDS,
        canonical_fields=MONEYFLOW_FIELDS,
        transport=transport,
        timeout=timeout,
    )


def fetch_trade_cal(
    token: str,
    exchange: str,
    start_date: str,
    end_date: str,
    is_open: str | None = None,
    transport: Transport | None = None,
    timeout: float = 30.0,
) -> TushareDailyResponse:
    params = {
        "exchange": exchange,
        "start_date": start_date,
        "end_date": end_date,
    }
    if is_open is not None:
        params["is_open"] = is_open
    return fetch_api(
        token=token,
        api_name="trade_cal",
        params=params,
        fields=TUSHARE_TRADE_CAL_FIELDS,
        canonical_fields=TRADE_CALENDAR_FIELDS,
        transport=transport,
        timeout=timeout,
    )


def fetch_raw_index_weight(
    token: str,
    index_code: str,
    start_date: str,
    end_date: str,
    transport: Transport | None = None,
    timeout: float = 30.0,
) -> TushareDailyResponse:
    return fetch_api(
        token=token,
        api_name="index_weight",
        params={
            "index_code": index_code,
            "start_date": start_date,
            "end_date": end_date,
        },
        fields=TUSHARE_INDEX_WEIGHT_FIELD_LIST,
        canonical_fields=TUSHARE_INDEX_WEIGHT_FIELDS,
        transport=transport,
        timeout=timeout,
    )


def fetch_api(
    token: str,
    api_name: str,
    params: dict[str, Any],
    fields: str,
    canonical_fields: tuple[str, ...],
    transport: Transport | None = None,
    timeout: float = 30.0,
) -> TushareDailyResponse:
    if not token:
        raise TushareProviderError("Tushare token is missing.", "permission", retryable=False)

    payload = {
        "api_name": api_name,
        "token": token,
        "params": params,
        "fields": fields,
    }
    return post_tushare_payload(payload, canonical_fields=canonical_fields, transport=transport, timeout=timeout)


def post_tushare_payload(
    payload: dict[str, Any],
    canonical_fields: tuple[str, ...],
    transport: Transport | None = None,
    timeout: float = 30.0,
) -> TushareDailyResponse:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        TUSHARE_HTTP_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        response_body = (transport or default_transport)(request, timeout)
    except HTTPError as exc:
        if exc.code == 429:
            error_type = "rate_limit"
        else:
            error_type = "server" if exc.code >= 500 else "unknown"
        raise TushareProviderError(
            f"HTTP {exc.code}: {exc.reason}",
            error_type,
            retryable=error_type in {"rate_limit", "server"},
        ) from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise TushareProviderError(str(exc), "network", retryable=True) from exc

    try:
        response = json.loads(response_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise TushareProviderError("Tushare returned invalid JSON.", "server", retryable=True) from exc

    code = response.get("code")
    msg = response.get("msg")
    if code not in (0, None):
        error_type = classify_tushare_error(code, msg)
        raise TushareProviderError(
            f"Tushare error code {code}: {msg}",
            error_type,
            retryable=error_type in {"rate_limit", "server"},
        )

    data = response.get("data") or {}
    fields = [str(field) for field in data.get("fields", [])]
    items = data.get("items", [])
    if not isinstance(items, list):
        raise TushareProviderError("Tushare response data.items is not a list.", "server", retryable=True)
    return TushareDailyResponse(fields=fields, items=items, raw_response=response, canonical_fields=canonical_fields)


def classify_tushare_error(code: Any, msg: Any) -> str:
    message = str(msg or "").lower()
    if code == 2002 or "permission" in message or "权限" in message or "token" in message:
        return "permission"
    if "rate" in message or "limit" in message or "频" in message or "每分钟" in message or "访问" in message:
        return "rate_limit"
    if "server" in message or "timeout" in message or "超时" in message:
        return "server"
    return "unknown"
