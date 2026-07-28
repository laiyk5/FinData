from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping
from copy import deepcopy
from datetime import date, timedelta
import re
from typing import Any


MOCK_TOKEN = "findata-mock"
_MOCK_FAILURE = re.compile(r"^findata-mock:fail=([a-z_]+)@(\d+)$")

# The daily_basic provider fields between ts_code/trade_date and limit_status;
# kept local so the provider package never imports a dataset package.
_DAILY_BASIC_FLOAT_FIELDS = (
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
)


def is_mock_token(value: object) -> bool:
    return isinstance(value, str) and (
        value == MOCK_TOKEN or _MOCK_FAILURE.fullmatch(value) is not None
    )


def transport_from_mock_token(value: str, *, today: date) -> MockTushareTransport:
    if not is_mock_token(value):
        raise ValueError("invalid findata mock token")
    transport = MockTushareTransport(today=today)
    match = _MOCK_FAILURE.fullmatch(value)
    if match:
        transport.fail_on_api_call(
            match.group(1), int(match.group(2)), code=-1, message="injected mock API failure"
        )
    return transport


class MockTushareTransport:
    """Deterministic implementation of the official Tushare HTTP envelope."""

    def __init__(self, *, today: date) -> None:
        self.today = today
        self.requests: list[dict[str, Any]] = []
        self._next_error: tuple[int, str] | None = None
        self._next_drop_field: str | None = None
        self._call_errors: dict[int, tuple[int, str]] = {}
        self._api_call_errors: dict[tuple[str, int], tuple[int, str]] = {}
        self._api_calls: dict[str, int] = {}
        self._empty_apis: set[str] = set()
        self.checkpoint_request_limit: int | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(today={self.today.isoformat()!r})"

    def fail_next(self, *, code: int, message: str) -> None:
        self._next_error = (code, message)

    def drop_field_next(self, field: str) -> None:
        self._next_drop_field = field

    def fail_on_call(self, number: int, *, code: int, message: str) -> None:
        if number <= 0:
            raise ValueError("call number must be positive")
        self._call_errors[number] = (code, message)
        self.checkpoint_request_limit = 1

    def fail_on_api_call(self, api_name: str, number: int, *, code: int, message: str) -> None:
        if number <= 0:
            raise ValueError("call number must be positive")
        self._api_call_errors[(api_name, number)] = (code, message)
        self.checkpoint_request_limit = 1

    def empty_next(self, api_name: str) -> None:
        self._empty_apis.add(api_name)

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = deepcopy(dict(payload))
        self.requests.append(request)
        api_name = str(request.get("api_name"))
        api_call = self._api_calls.get(api_name, 0) + 1
        self._api_calls[api_name] = api_call
        api_error = self._api_call_errors.pop((api_name, api_call), None)
        if api_error is not None:
            code, message = api_error
            return {"code": code, "msg": message, "data": None}
        scheduled_error = self._call_errors.pop(len(self.requests), None)
        if scheduled_error is not None:
            code, message = scheduled_error
            return {"code": code, "msg": message, "data": None}
        if self._next_error is not None:
            code, message = self._next_error
            self._next_error = None
            return {"code": code, "msg": message, "data": None}

        fields = str(request.get("fields") or "").split(",")
        params = request.get("params")
        if not isinstance(params, Mapping):
            return {"code": -1, "msg": "params must be an object", "data": None}
        if api_name in self._empty_apis:
            self._empty_apis.remove(api_name)
            return {"code": 0, "msg": None, "data": {"fields": fields, "items": []}}
        rows = self._rows(api_name, params)

        if self._next_drop_field is not None and self._next_drop_field in fields:
            fields.remove(self._next_drop_field)
            self._next_drop_field = None
        items = [[row.get(field) for field in fields] for row in rows]
        return {"code": 0, "msg": None, "data": {"fields": fields, "items": items}}

    _FUND_DAILY_FLOAT_FIELDS = ("open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount")

    def _rows(self, api_name: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        generators = {
            "trade_cal": self._trade_cal,
            "stock_basic": self._stock_basic,
            "index_basic": self._index_basic,
            "index_weight": self._index_weight,
            "daily_basic": self._daily_basic,
            "fund_daily": self._fund_daily,
        }
        try:
            return generators[api_name](params)
        except KeyError:
            return []

    def _trade_cal(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        exchange = str(params.get("exchange") or "SSE")
        start = _provider_date(params.get("start_date"), fallback=self.today)
        end = _provider_date(params.get("end_date"), fallback=start)
        result: list[dict[str, Any]] = []
        cursor = start
        while cursor <= end:
            result.append(
                {
                    "exchange": exchange,
                    "cal_date": _format_date(cursor),
                    "is_open": "1" if cursor.weekday() < 5 else "0",
                    "pretrade_date": _format_date(_previous_weekday(cursor)),
                }
            )
            cursor += timedelta(days=1)
        return result

    def _stock_basic(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        fixtures = [
            _stock_row("600000.SH", "600000", "浦发银行", "SSE", "主板", "L", "19991110"),
            _stock_row("000001.SZ", "000001", "平安银行", "SZSE", "主板", "L", "19910403"),
            _stock_row("430047.BJ", "430047", "诺思兰德", "BSE", "北交所", "L", "20201124"),
            _stock_row("600001.SH", "600001", "示例退市", "SSE", None, "D", "19910101", "20200101"),
            _stock_row("920000.BJ", "920000", "示例待交易", "BSE", "北交所", "G", None),
        ]
        return [
            row
            for row in fixtures
            if (not params.get("list_status") or row["list_status"] == params["list_status"])
            and (not params.get("exchange") or row["exchange"] == params["exchange"])
        ]

    def _index_weight(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        index_code = str(params.get("index_code") or "000300.SH")
        start = _provider_date(params.get("start_date"), fallback=self.today.replace(day=1))
        end = _provider_date(
            params.get("end_date"),
            fallback=date(start.year, start.month, monthrange(start.year, start.month)[1]),
        )
        result: list[dict[str, Any]] = []
        for month_start in _months_intersecting(start, end):
            trade_date = _first_weekday(month_start)
            for con_code, weight in (
                ("000001.SZ", 40.0),
                ("600000.SH", 35.0),
                ("600519.SH", 25.0),
            ):
                result.append(
                    {
                        "index_code": index_code,
                        "con_code": con_code,
                        "trade_date": _format_date(trade_date),
                        "weight": weight,
                    }
                )
        return result

    def _index_basic(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        code = str(params.get("ts_code") or "")
        if not code:
            return []
        names = {"000300.SH": "沪深300", "000905.SH": "中证500"}
        return [
            {
                "ts_code": code,
                "name": names.get(code, f"Mock {code}"),
                "fullname": names.get(code, f"Mock index {code}"),
                "market": "CSI",
                "publisher": "中证指数有限公司",
                "index_type": "规模",
                "category": "规模指数",
                "base_date": "20041231",
                "base_point": 1000.0,
                "list_date": "20050408",
                "weight_rule": "派许加权",
                "desc": f"Mock metadata for {code}",
                "exp_date": None,
            }
        ]

    def _daily_basic(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        symbols = (
            [str(params["ts_code"])]
            if params.get("ts_code")
            else ["000001.SZ", "600000.SH", "600519.SH"]
        )
        if params.get("trade_date"):
            start = end = _provider_date(params["trade_date"], fallback=self.today)
        else:
            start = _provider_date(params.get("start_date"), fallback=self.today)
            end = _provider_date(params.get("end_date"), fallback=start)
        result: list[dict[str, Any]] = []
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                for symbol in symbols:
                    seed = sum(ord(char) for char in symbol) + cursor.toordinal()
                    row: dict[str, Any] = {
                        "ts_code": symbol,
                        "trade_date": _format_date(cursor),
                        "limit_status": seed % 7,
                    }
                    for index, field in enumerate(_DAILY_BASIC_FLOAT_FIELDS):
                        row[field] = round((seed % 1000 + index + 1) / 10.0, 4)
                    result.append(row)
            cursor += timedelta(days=1)
        return result

    def _fund_daily(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        symbols = (
            [str(params["ts_code"])]
            if params.get("ts_code")
            else ["159919.SZ", "510050.SH"]
        )
        if params.get("trade_date"):
            start = end = _provider_date(params["trade_date"], fallback=self.today)
        else:
            start = _provider_date(params.get("start_date"), fallback=self.today)
            end = _provider_date(params.get("end_date"), fallback=start)
        result: list[dict[str, Any]] = []
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                for symbol in symbols:
                    seed = sum(ord(char) for char in symbol) + cursor.toordinal()
                    row: dict[str, Any] = {
                        "ts_code": symbol,
                        "trade_date": _format_date(cursor),
                    }
                    for index, field in enumerate(self._FUND_DAILY_FLOAT_FIELDS):
                        row[field] = round((seed % 1000 + index + 1) / 10.0, 4)
                    result.append(row)
            cursor += timedelta(days=1)
        return result


def _provider_date(value: Any, *, fallback: date) -> date:
    if value in (None, ""):
        return fallback
    text = str(value)
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def _format_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _previous_weekday(value: date) -> date:
    cursor = value - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def _first_weekday(month_start: date) -> date:
    cursor = month_start
    while cursor.weekday() >= 5:
        cursor += timedelta(days=1)
    return cursor


def _months_intersecting(start: date, end: date) -> list[date]:
    cursor = start.replace(day=1)
    result: list[date] = []
    while cursor <= end:
        result.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return result


def _stock_row(
    ts_code: str,
    symbol: str,
    name: str,
    exchange: str,
    market: str | None,
    list_status: str,
    list_date: str | None,
    delist_date: str | None = None,
) -> dict[str, Any]:
    return {
        "ts_code": ts_code,
        "symbol": symbol,
        "name": name,
        "area": "北京" if exchange == "BSE" else "上海" if exchange == "SSE" else "深圳",
        "industry": "银行" if "银行" in name else None,
        "fullname": name,
        "enname": None,
        "cnspell": None,
        "market": market,
        "exchange": exchange,
        "curr_type": "CNY",
        "list_status": list_status,
        "list_date": list_date,
        "delist_date": delist_date,
        "is_hs": None,
        "act_name": None,
        "act_ent_type": None,
    }
