"""Official Tushare stock basic dataset plugin for findata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyarrow as pa

from findata.contracts import DatasetSpec, provider_date

if TYPE_CHECKING:
    from findata.plugins import DatasetPlugin


def _normalize_stock_basic(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        normalized["list_date"] = provider_date(row.get("list_date"), nullable=True)
        normalized["delist_date"] = provider_date(row.get("delist_date"), nullable=True)
        result.append(normalized)
    return result


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


def _stock_basic_schema() -> pa.Schema:
    required = {"ts_code", "symbol", "name", "exchange", "list_status"}
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


STOCK_BASIC_SPEC = DatasetSpec(
    name="findata/tushare/stock_basic",
    api_name="stock_basic",
    schema=_stock_basic_schema(),
    provider_fields=STOCK_BASIC_FIELDS,
    primary_key=("ts_code",),
    normalize_rows=_normalize_stock_basic,
)


def stock_basic_plugin() -> "DatasetPlugin":
    from findata.plugins import DatasetPlugin

    from findata_tushare_stock_basic.operations import StockBasicDatasetRuntime

    return DatasetPlugin(
        name=STOCK_BASIC_SPEC.name,
        provider="tushare",
        spec=STOCK_BASIC_SPEC,
        runtime=StockBasicDatasetRuntime(),
        operations=("update",),
        dependencies=(),
        settings={},
        schedule=("0 8 * * 1", "Asia/Shanghai"),
    )
