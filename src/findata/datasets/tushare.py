from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import TYPE_CHECKING, Any

import pyarrow as pa

from findata.contracts import DatasetSpec, provider_date

if TYPE_CHECKING:
    from findata.plugins import DatasetPlugin


def _normalize_trade_cal(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "exchange": row["exchange"],
            "cal_date": provider_date(row["cal_date"]),
            "is_open": str(row["is_open"]) == "1",
            "pretrade_date": provider_date(row.get("pretrade_date"), nullable=True),
        }
        for row in rows
    ]


def _normalize_stock_basic(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        normalized["list_date"] = provider_date(row.get("list_date"), nullable=True)
        normalized["delist_date"] = provider_date(row.get("delist_date"), nullable=True)
        result.append(normalized)
    return result


def _normalize_index_weight(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dated: list[dict[str, Any]] = []
    latest: dict[tuple[str, date], date] = {}
    for row in rows:
        trade_date = provider_date(row["trade_date"])
        assert trade_date is not None
        effective_month = trade_date.replace(day=1)
        normalized = {
            "index_code": row["index_code"],
            "effective_month": effective_month,
            "con_code": row["con_code"],
            "trade_date": trade_date,
            "weight": float(row["weight"]),
        }
        dated.append(normalized)
        key = (normalized["index_code"], effective_month)
        latest[key] = max(latest.get(key, trade_date), trade_date)
    return [
        row
        for row in dated
        if row["trade_date"] == latest[(row["index_code"], row["effective_month"])]
    ]


def _normalize_daily_basic(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        normalized["trade_date"] = provider_date(row["trade_date"])
        for name in DAILY_BASIC_FLOAT_FIELDS:
            value = row.get(name)
            normalized[name] = None if value in (None, "") else float(value)
        value = row.get("limit_status")
        normalized["limit_status"] = None if value in (None, "") else int(value)
        result.append(normalized)
    return result


TRADE_CAL_FIELDS = ("exchange", "cal_date", "is_open", "pretrade_date")
STOCK_BASIC_FIELDS = (
    "ts_code",
    "symbol",
    "name",
    "area",
    "industry",
    "fullname",
    "enname",
    "cnspell",
    "market",
    "exchange",
    "curr_type",
    "list_status",
    "list_date",
    "delist_date",
    "is_hs",
    "act_name",
    "act_ent_type",
)
INDEX_WEIGHT_FIELDS = ("index_code", "con_code", "trade_date", "weight")
DAILY_BASIC_FLOAT_FIELDS = (
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
DAILY_BASIC_FIELDS = ("ts_code", "trade_date", *DAILY_BASIC_FLOAT_FIELDS, "limit_status")


def _stock_basic_schema() -> pa.Schema:
    required = {"ts_code", "symbol", "name", "market", "exchange", "list_status"}
    date_fields = {"list_date", "delist_date"}
    return pa.schema(
        [
            pa.field(
                name,
                pa.date32() if name in date_fields else pa.string(),
                nullable=name not in required,
            )
            for name in STOCK_BASIC_FIELDS
        ]
    )


def _daily_basic_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("ts_code", pa.string(), nullable=False),
            pa.field("trade_date", pa.date32(), nullable=False),
            *(pa.field(name, pa.float64(), nullable=True) for name in DAILY_BASIC_FLOAT_FIELDS),
            pa.field("limit_status", pa.int8(), nullable=True),
        ]
    )


TUSHARE_DATASETS: Mapping[str, DatasetSpec] = {
    "tushare_trade_cal": DatasetSpec(
        name="tushare_trade_cal",
        api_name="trade_cal",
        schema=pa.schema(
            [
                pa.field("exchange", pa.string(), nullable=False),
                pa.field("cal_date", pa.date32(), nullable=False),
                pa.field("is_open", pa.bool_(), nullable=False),
                pa.field("pretrade_date", pa.date32(), nullable=True),
            ]
        ),
        provider_fields=TRADE_CAL_FIELDS,
        primary_key=("exchange", "cal_date"),
        partition_key="exchange",
        time_field="cal_date",
        capabilities={"time_accumulating": True},
        normalize_rows=_normalize_trade_cal,
    ),
    "tushare_stock_basic": DatasetSpec(
        name="tushare_stock_basic",
        api_name="stock_basic",
        schema=_stock_basic_schema(),
        provider_fields=STOCK_BASIC_FIELDS,
        primary_key=("ts_code",),
        normalize_rows=_normalize_stock_basic,
    ),
    "tushare_index_weight": DatasetSpec(
        name="tushare_index_weight",
        api_name="index_weight",
        schema=pa.schema(
            [
                pa.field("index_code", pa.string(), nullable=False),
                pa.field("effective_month", pa.date32(), nullable=False),
                pa.field("con_code", pa.string(), nullable=False),
                pa.field("trade_date", pa.date32(), nullable=False),
                pa.field("weight", pa.float64(), nullable=False),
            ]
        ),
        provider_fields=INDEX_WEIGHT_FIELDS,
        primary_key=("index_code", "effective_month", "con_code"),
        partition_key="index_code",
        secondary_key="con_code",
        time_field="effective_month",
        capabilities={"time_accumulating": True},
        aliases={"CSI300": "000300.SH"},
        normalize_rows=_normalize_index_weight,
    ),
    "tushare_daily_basic": DatasetSpec(
        name="tushare_daily_basic",
        api_name="daily_basic",
        schema=_daily_basic_schema(),
        provider_fields=DAILY_BASIC_FIELDS,
        primary_key=("ts_code", "trade_date"),
        partition_key="ts_code",
        time_field="trade_date",
        capabilities={"symbol_set_cap": 1, "row_limit": 6000, "time_accumulating": True},
        normalize_rows=_normalize_daily_basic,
    ),
}


def builtin_plugins() -> list["DatasetPlugin"]:
    from findata.plugins import DatasetPlugin

    definitions = {
        "tushare_trade_cal": ("single-file-csv", ("update", "complete"), ()),
        "tushare_stock_basic": ("single-file-csv", ("update",), ()),
        "tushare_index_weight": ("partitioned-parquet", ("update", "complete"), ()),
        "tushare_daily_basic": (
            "partitioned-parquet",
            ("update", "complete", "refresh"),
            ("tushare_trade_cal", "tushare_index_weight"),
        ),
    }
    return [
        DatasetPlugin(
            name=name,
            provider="tushare",
            spec=TUSHARE_DATASETS[name],
            storage_strategy=strategy,
            operations=operations,
            dependencies=dependencies,
        )
        for name, (strategy, operations, dependencies) in definitions.items()
    ]


def trade_cal_plugin() -> "DatasetPlugin":
    return builtin_plugins()[0]


def stock_basic_plugin() -> "DatasetPlugin":
    return builtin_plugins()[1]


def index_weight_plugin() -> "DatasetPlugin":
    return builtin_plugins()[2]


def daily_basic_plugin() -> "DatasetPlugin":
    return builtin_plugins()[3]
