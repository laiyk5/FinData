from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .tushare_daily import FIELDS as DAILY_FIELDS
from .tushare_daily_basic import FIELDS as DAILY_BASIC_FIELDS


DAILY_MAX_ROWS_PER_REQUEST = 6000
DAILY_REQUEST_STRATEGY_AUTO = "auto"
DAILY_REQUEST_STRATEGY_SYMBOL_RANGE = "symbol_range"
DAILY_REQUEST_STRATEGY_TRADE_DATE_ALL = "trade_date_all"
TRADE_CALENDAR_FIELDS = ("exchange", "cal_date", "is_open", "pretrade_date")
INSTRUMENT_UNIVERSE_FIELDS = (
    "universe_id",
    "provider",
    "source_id",
    "member_code",
    "member_name",
    "valid_from",
    "valid_to",
    "as_of_date",
    "weight",
    "rank",
    "member_type",
)
REPORT_CATALOG_FIELDS = (
    "universe_id",
    "symbol_selector",
    "symbol_selector_resolved_at",
    "source",
    "announcement_id",
    "stock_code",
    "stock_exchange",
    "ts_code",
    "sec_name",
    "org_id",
    "report_type",
    "report_year",
    "period_end",
    "announcement_title",
    "announcement_date",
    "pdf_url",
    "source_detail_url",
    "source_category",
    "is_correction",
    "is_summary",
    "is_english",
    "is_cancelled",
    "version_no",
    "latest_version",
    "raw_adjunct_url",
    "first_seen_at",
    "last_seen_at",
)


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    api_name: str
    fields: tuple[str, ...]
    primary_key: tuple[str, ...]
    date_field: str
    partition_field: str
    published_filename: str
    staged_prefix: str
    publish_partition_field: str | None = None

    def row_key(self, row: dict[str, str]) -> tuple[str, ...]:
        return tuple(row[field] for field in self.primary_key)

    def sort_key(self, row: dict[str, str]) -> tuple[str, ...]:
        return tuple(row.get(field, "") for field in (*self.primary_key,))

    def staged_filename(self, partition_value: str) -> str:
        return f"{self.staged_prefix}_{partition_value}.csv"


SPECS = {
    "tushare_daily": DatasetSpec(
        name="tushare_daily",
        api_name="daily",
        fields=tuple(DAILY_FIELDS),
        primary_key=("ts_code", "trade_date"),
        date_field="trade_date",
        partition_field="trade_date",
        published_filename="daily.csv",
        staged_prefix="daily",
    ),
    "trade_calendar": DatasetSpec(
        name="trade_calendar",
        api_name="trade_cal",
        fields=TRADE_CALENDAR_FIELDS,
        primary_key=("exchange", "cal_date"),
        date_field="cal_date",
        partition_field="exchange",
        published_filename="trade_calendar.csv",
        staged_prefix="trade_calendar",
        publish_partition_field="exchange",
    ),
    "tushare_daily_basic": DatasetSpec(
        name="tushare_daily_basic",
        api_name="daily_basic",
        fields=DAILY_BASIC_FIELDS,
        primary_key=("ts_code", "trade_date"),
        date_field="trade_date",
        partition_field="trade_date",
        published_filename="daily_basic.csv",
        staged_prefix="daily_basic",
    ),
    "instrument_universe": DatasetSpec(
        name="instrument_universe",
        api_name="index_weight",
        fields=INSTRUMENT_UNIVERSE_FIELDS,
        primary_key=("universe_id", "member_code", "as_of_date"),
        date_field="as_of_date",
        partition_field="universe_id",
        published_filename="instrument_universe.csv",
        staged_prefix="instrument_universe",
        publish_partition_field="universe_id",
    ),
    "report_catalog": DatasetSpec(
        name="report_catalog",
        api_name="hisAnnouncement/query",
        fields=REPORT_CATALOG_FIELDS,
        primary_key=("source", "announcement_id"),
        date_field="announcement_date",
        partition_field="universe_id",
        published_filename="report_catalog.csv",
        staged_prefix="report_catalog",
        publish_partition_field="universe_id",
    ),
}


def get_spec(dataset_name: str) -> DatasetSpec:
    try:
        return SPECS[dataset_name]
    except KeyError as exc:
        raise ValueError(f"Unsupported dataset: {dataset_name}") from exc


def request_key(dataset_name: str, request: dict[str, Any]) -> str:
    if dataset_name in {"tushare_daily", "tushare_daily_basic"}:
        prefix = "daily_basic" if dataset_name == "tushare_daily_basic" else "daily"
        if request.get("request_mode") == "trade_date_all":
            return f"{prefix}:{request['trade_date']}:ALL"
        if request.get("start_date") and request.get("end_date"):
            return f"{prefix}:{request['start_date']}:{request['end_date']}:{request['ts_code']}"
        return f"{prefix}:{request['trade_date']}:{request['ts_code']}"
    if dataset_name == "trade_calendar":
        return f"trade_cal:{request['exchange']}:{request['start_date']}:{request['end_date']}"
    if dataset_name == "instrument_universe":
        return f"index_weight:{request['universe_id']}:{request['source_id']}:{request['start_date']}:{request['end_date']}"
    if dataset_name == "report_catalog":
        return (
            f"cninfo_reports:{request['universe_id']}:{request['ts_code']}:"
            f"{request['report_year']}:{request['report_types']}:{request['page_num']}"
        )
    raise ValueError(f"Unsupported dataset: {dataset_name}")


def request_file_stem(dataset_name: str, request: dict[str, Any]) -> str:
    if dataset_name in {"tushare_daily", "tushare_daily_basic"}:
        prefix = "daily_basic" if dataset_name == "tushare_daily_basic" else "daily"
        if request.get("request_mode") == "trade_date_all":
            return f"{prefix}_{request['trade_date']}_ALL"
        symbol_token = request_symbols_token(request)
        if request.get("start_date") and request.get("end_date"):
            return f"{prefix}_{request['start_date']}_{request['end_date']}_{symbol_token}"
        return f"{prefix}_{request['trade_date']}_{symbol_token}"
    if dataset_name == "trade_calendar":
        return f"trade_cal_{request['exchange']}_{request['start_date']}_{request['end_date']}"
    if dataset_name == "instrument_universe":
        return (
            f"index_weight_{safe_path_token(request['universe_id'])}_"
            f"{request['source_id'].replace('.', '_')}_{request['start_date']}_{request['end_date']}"
        )
    if dataset_name == "report_catalog":
        return (
            f"cninfo_reports_{safe_path_token(request['universe_id'])}_"
            f"{request['ts_code'].replace('.', '_')}_{request['report_year']}_p{request['page_num']}"
        )
    raise ValueError(f"Unsupported dataset: {dataset_name}")


def plan_requests(dataset_name: str, symbols: list[str], trade_dates: list[str], extras: dict[str, Any]) -> list[dict[str, Any]]:
    if dataset_name == "tushare_daily":
        return plan_daily_requests(symbols, trade_dates, extras)
    if dataset_name == "tushare_daily_basic":
        return plan_daily_basic_requests(symbols, trade_dates, extras)
    if dataset_name == "trade_calendar":
        exchange = extras["exchange"]
        return [
            {
                "api": "trade_cal",
                "exchange": exchange,
                "start_date": start_date,
                "end_date": end_date,
                "is_open": extras.get("is_open"),
            }
            for start_date, end_date in chunk_date_range_by_year(extras["start_date"], extras["end_date"])
        ]
    if dataset_name == "instrument_universe":
        return [
            {
                "api": "index_weight",
                "universe_id": extras["universe_id"],
                "source_id": extras["source_id"],
                "start_date": extras["start_date"],
                "end_date": extras["end_date"],
            }
        ]
    if dataset_name == "report_catalog":
        requests = []
        start_year = int(extras["start_year"])
        end_year = int(extras["end_year"])
        report_types = ",".join(extras.get("report_types") or ["annual", "semiannual", "q1", "q3"])
        max_pages = int(extras.get("max_pages_per_request") or 1)
        for symbol in symbols:
            for year in range(start_year, end_year + 1):
                for page_num in range(1, max_pages + 1):
                    requests.append(
                        {
                            "api": "hisAnnouncement/query",
                            "universe_id": extras["universe_id"],
                            "symbol_selector": extras.get("symbol_selector", ""),
                            "symbol_selector_resolved_at": extras.get("symbol_selector_resolved_at", ""),
                            "ts_code": symbol,
                            "stock_code": symbol.split(".", 1)[0],
                            "stock_exchange": exchange_suffix_to_cninfo_column(symbol),
                            "report_year": str(year),
                            "report_types": report_types,
                            "page_num": str(page_num),
                            "page_size": str(extras.get("page_size") or 30),
                            "org_id": extras.get("org_id_map", {}).get(symbol, infer_cninfo_org_id(symbol)),
                        }
                    )
        return requests
    raise ValueError(f"Unsupported dataset: {dataset_name}")


def plan_daily_requests(
    symbols: list[str],
    trade_dates: list[str],
    extras: dict[str, Any],
    *,
    api_name: str = "daily",
) -> list[dict[str, Any]]:
    strategy = extras.get("daily_request_strategy") or DAILY_REQUEST_STRATEGY_AUTO
    if strategy not in {
        DAILY_REQUEST_STRATEGY_AUTO,
        DAILY_REQUEST_STRATEGY_SYMBOL_RANGE,
        DAILY_REQUEST_STRATEGY_TRADE_DATE_ALL,
    }:
        raise ValueError(f"Unsupported daily request strategy: {strategy}")

    if extras.get("start_date") and extras.get("end_date"):
        expected_trade_dates = list(extras.get("expected_trade_dates") or date_range(extras["start_date"], extras["end_date"]))
        symbol_range_plan = plan_daily_symbol_range_requests(symbols, expected_trade_dates)
        trade_date_plan = plan_daily_trade_date_all_requests(symbols, expected_trade_dates)
    else:
        expected_trade_dates = list(trade_dates)
        symbol_range_plan = plan_daily_trade_date_symbol_batches(symbols, expected_trade_dates)
        trade_date_plan = plan_daily_trade_date_all_requests(symbols, expected_trade_dates)

    if strategy == DAILY_REQUEST_STRATEGY_SYMBOL_RANGE:
        return apply_api_name(symbol_range_plan, api_name)
    if strategy == DAILY_REQUEST_STRATEGY_TRADE_DATE_ALL:
        return apply_api_name(trade_date_plan, api_name)
    if len(trade_date_plan) < len(symbol_range_plan):
        return apply_api_name(trade_date_plan, api_name)
    return apply_api_name(symbol_range_plan, api_name)


def apply_api_name(requests: list[dict[str, Any]], api_name: str) -> list[dict[str, Any]]:
    return [{**request, "api": api_name} for request in requests]


def plan_daily_basic_requests(symbols: list[str], trade_dates: list[str], extras: dict[str, Any]) -> list[dict[str, Any]]:
    expected_trade_dates = list(extras.get("expected_trade_dates") or trade_dates)
    if not symbols or not expected_trade_dates:
        return []

    if extras.get("daily_request_strategy") == DAILY_REQUEST_STRATEGY_TRADE_DATE_ALL:
        return apply_api_name(plan_daily_trade_date_all_requests(symbols, expected_trade_dates), "daily_basic")

    return [
        {
            "api": "daily_basic",
            "request_mode": "symbol_range",
            "ts_code": symbol,
            "symbols": [symbol],
            "start_date": expected_trade_dates[0],
            "end_date": expected_trade_dates[-1],
            "expected_trade_dates": list(expected_trade_dates),
            "estimated_max_rows": len(expected_trade_dates),
            "row_limit": DAILY_MAX_ROWS_PER_REQUEST,
        }
        for symbol in symbols
    ]


def plan_daily_symbol_range_requests(symbols: list[str], expected_trade_dates: list[str]) -> list[dict[str, Any]]:
    if not symbols or not expected_trade_dates:
        return []

    date_chunk_size, symbol_chunk_size = choose_daily_range_chunk_shape(len(symbols), len(expected_trade_dates))
    requests: list[dict[str, Any]] = []
    for date_chunk in chunk_sequence(expected_trade_dates, date_chunk_size):
        for symbol_chunk in chunk_sequence(symbols, symbol_chunk_size):
            requests.append(
                {
                    "api": "daily",
                    "request_mode": "symbol_range_batch",
                    "ts_code": ",".join(symbol_chunk),
                    "symbols": list(symbol_chunk),
                    "start_date": date_chunk[0],
                    "end_date": date_chunk[-1],
                    "expected_trade_dates": list(date_chunk),
                    "estimated_max_rows": len(symbol_chunk) * len(date_chunk),
                    "row_limit": DAILY_MAX_ROWS_PER_REQUEST,
                }
            )
    return requests


def plan_daily_trade_date_symbol_batches(symbols: list[str], trade_dates: list[str]) -> list[dict[str, Any]]:
    if not symbols or not trade_dates:
        return []
    symbol_chunk_size = DAILY_MAX_ROWS_PER_REQUEST
    requests: list[dict[str, Any]] = []
    for trade_date in trade_dates:
        for symbol_chunk in chunk_sequence(symbols, symbol_chunk_size):
            requests.append(
                {
                    "api": "daily",
                    "request_mode": "symbol_date_batch",
                    "ts_code": ",".join(symbol_chunk),
                    "symbols": list(symbol_chunk),
                    "trade_date": trade_date,
                    "expected_trade_dates": [trade_date],
                    "estimated_max_rows": len(symbol_chunk),
                    "row_limit": DAILY_MAX_ROWS_PER_REQUEST,
                }
            )
    return requests


def plan_daily_trade_date_all_requests(symbols: list[str], trade_dates: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "api": "daily",
            "request_mode": "trade_date_all",
            "ts_code": "",
            "symbols": list(symbols),
            "trade_date": trade_date,
            "expected_trade_dates": [trade_date],
            "estimated_max_rows": DAILY_MAX_ROWS_PER_REQUEST,
            "row_limit": DAILY_MAX_ROWS_PER_REQUEST,
        }
        for trade_date in trade_dates
    ]


def choose_daily_range_chunk_shape(symbol_count: int, trade_date_count: int) -> tuple[int, int]:
    best_date_chunk_size = 1
    best_symbol_chunk_size = DAILY_MAX_ROWS_PER_REQUEST
    best_request_count: int | None = None

    max_date_chunk_size = min(trade_date_count, DAILY_MAX_ROWS_PER_REQUEST)
    for date_chunk_size in range(1, max_date_chunk_size + 1):
        symbol_chunk_size = max(1, DAILY_MAX_ROWS_PER_REQUEST // date_chunk_size)
        request_count = ceil_div(trade_date_count, date_chunk_size) * ceil_div(symbol_count, symbol_chunk_size)
        if best_request_count is None or request_count < best_request_count:
            best_request_count = request_count
            best_date_chunk_size = date_chunk_size
            best_symbol_chunk_size = symbol_chunk_size
        elif request_count == best_request_count and date_chunk_size > best_date_chunk_size:
            best_date_chunk_size = date_chunk_size
            best_symbol_chunk_size = symbol_chunk_size

    return best_date_chunk_size, best_symbol_chunk_size


def summarize_request_plan(dataset_name: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
    modes: dict[str, int] = {}
    for request in requests:
        mode = request.get("request_mode") or request.get("api") or "unknown"
        modes[mode] = modes.get(mode, 0) + 1
    summary: dict[str, Any] = {
        "request_count": len(requests),
        "modes": modes,
    }
    if dataset_name in {"tushare_daily", "tushare_daily_basic"}:
        estimates = [int(request.get("estimated_max_rows") or 0) for request in requests]
        summary.update(
            {
                "row_limit": DAILY_MAX_ROWS_PER_REQUEST,
                "max_estimated_rows_per_request": max(estimates) if estimates else 0,
                "total_estimated_target_rows": sum(estimates),
            }
        )
    return summary


def chunk_sequence(values: list[Any], chunk_size: int) -> list[list[Any]]:
    return [values[index : index + chunk_size] for index in range(0, len(values), chunk_size)]


def ceil_div(left: int, right: int) -> int:
    return -(-left // right)


def request_symbols_token(request: dict[str, Any]) -> str:
    symbols = request.get("symbols") or parse_symbols(request.get("ts_code", ""))
    if not symbols:
        return "ALL"
    safe_symbols = [symbol.replace(".", "_") for symbol in symbols]
    if len(safe_symbols) <= 3:
        return "_".join(safe_symbols)
    digest = hashlib.sha1(",".join(symbols).encode("utf-8")).hexdigest()[:10]
    return f"{safe_symbols[0]}_{len(safe_symbols)}symbols_{digest}"


def parse_symbols(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def expected_keys(dataset_name: str, manifest: dict[str, Any]) -> set[tuple[str, str]]:
    if dataset_name in {"tushare_daily", "tushare_daily_basic"}:
        daily_request = manifest.get("daily_request") or {}
        if daily_request.get("start_date") and daily_request.get("end_date"):
            return {
                (symbol, trade_date)
                for symbol in manifest["symbols"]
                for trade_date in daily_request.get("expected_trade_dates", [])
            }
        return {
            (symbol, trade_date)
            for symbol in manifest["symbols"]
            for trade_date in manifest["trade_dates"]
        }
    if dataset_name == "trade_calendar":
        exchange = manifest["calendar_request"]["exchange"]
        return {
            (exchange, date_value)
            for date_value in date_range(manifest["calendar_request"]["start_date"], manifest["calendar_request"]["end_date"])
        }
    if dataset_name == "instrument_universe":
        return set()
    if dataset_name == "report_catalog":
        return set()
    raise ValueError(f"Unsupported dataset: {dataset_name}")


def coverage_from_rows(dataset_name: str, rows: list[dict[str, str]]) -> dict[str, Any]:
    if dataset_name in {"tushare_daily", "tushare_daily_basic"}:
        symbols = sorted({row["ts_code"] for row in rows if row.get("ts_code")})
        dates = sorted({row["trade_date"] for row in rows if row.get("trade_date")})
        symbol_ranges = {}
        for symbol in symbols:
            symbol_dates = sorted(row["trade_date"] for row in rows if row.get("ts_code") == symbol and row.get("trade_date"))
            symbol_ranges[symbol] = {
                "start_date": symbol_dates[0] if symbol_dates else None,
                "end_date": symbol_dates[-1] if symbol_dates else None,
            }
        return {
            "symbols": symbols,
            "start_date": dates[0] if dates else None,
            "end_date": dates[-1] if dates else None,
            "calendar": "CN_A_SHARE",
            "symbol_ranges": symbol_ranges,
        }
    if dataset_name == "trade_calendar":
        exchanges = sorted({row["exchange"] for row in rows if row.get("exchange")})
        dates = sorted({row["cal_date"] for row in rows if row.get("cal_date")})
        exchange_ranges = {}
        for exchange in exchanges:
            exchange_dates = sorted(row["cal_date"] for row in rows if row.get("exchange") == exchange and row.get("cal_date"))
            exchange_ranges[exchange] = {
                "start_date": exchange_dates[0] if exchange_dates else None,
                "end_date": exchange_dates[-1] if exchange_dates else None,
            }
        return {
            "exchanges": exchanges,
            "start_date": dates[0] if dates else None,
            "end_date": dates[-1] if dates else None,
            "exchange_ranges": exchange_ranges,
        }
    if dataset_name == "instrument_universe":
        universes = sorted({row["universe_id"] for row in rows if row.get("universe_id")})
        dates = sorted({row["as_of_date"] for row in rows if row.get("as_of_date")})
        universe_ranges = {}
        member_counts = {}
        for universe_id in universes:
            universe_rows = [row for row in rows if row.get("universe_id") == universe_id]
            universe_dates = sorted({row["as_of_date"] for row in universe_rows if row.get("as_of_date")})
            latest_date = universe_dates[-1] if universe_dates else None
            latest_members = {
                row["member_code"]
                for row in universe_rows
                if row.get("member_code") and row.get("as_of_date") == latest_date
            }
            universe_ranges[universe_id] = {
                "start_date": universe_dates[0] if universe_dates else None,
                "end_date": universe_dates[-1] if universe_dates else None,
            }
            member_counts[universe_id] = len(latest_members)
        return {
            "universes": universes,
            "start_date": dates[0] if dates else None,
            "end_date": dates[-1] if dates else None,
            "universe_ranges": universe_ranges,
            "latest_member_counts": member_counts,
        }
    if dataset_name == "report_catalog":
        universes = sorted({row["universe_id"] for row in rows if row.get("universe_id")})
        years = sorted({row["report_year"] for row in rows if row.get("report_year")})
        symbols = sorted({row["ts_code"] for row in rows if row.get("ts_code")})
        return {
            "universes": universes,
            "symbols": symbols,
            "start_year": years[0] if years else None,
            "end_year": years[-1] if years else None,
            "report_count": len(rows),
        }
    raise ValueError(f"Unsupported dataset: {dataset_name}")


def date_range(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    dates = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates


def chunk_date_range_by_year(start_date: str, end_date: str) -> list[tuple[str, str]]:
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    chunks = []
    current = start
    while current <= end:
        year_end = current.replace(month=12, day=31)
        chunk_end = min(year_end, end)
        chunks.append((current.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")))
        current = chunk_end + timedelta(days=1)
    return chunks


def safe_path_token(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in value)


def exchange_suffix_to_cninfo_column(ts_code: str) -> str:
    if ts_code.endswith(".SH"):
        return "sse"
    if ts_code.endswith(".SZ"):
        return "szse"
    if ts_code.endswith(".BJ"):
        return "bj"
    return ""


def infer_cninfo_org_id(ts_code: str) -> str:
    stock_code = ts_code.split(".", 1)[0]
    if ts_code.endswith(".SH"):
        return f"gssh0{stock_code}"
    if ts_code.endswith(".SZ"):
        return f"gssz0{stock_code}"
    if ts_code.endswith(".BJ"):
        return f"bj{stock_code}"
    return ""
