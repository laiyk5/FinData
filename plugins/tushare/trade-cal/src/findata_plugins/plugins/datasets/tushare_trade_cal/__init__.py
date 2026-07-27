"""Official Tushare trade calendar dataset plugin for findata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyarrow as pa

from findata.sdk.contracts import DatasetSpec, provider_date

if TYPE_CHECKING:
    from findata.sdk.plugins import DatasetPlugin


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


TRADE_CAL_FIELDS = ("exchange", "cal_date", "is_open", "pretrade_date")

TRADE_CAL_SPEC = DatasetSpec(
    name="findata-plugins/tushare_trade_cal",
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
)


def trade_cal_plugin() -> "DatasetPlugin":
    from findata.sdk.plugins import DatasetPlugin

    from findata_plugins.plugins.datasets.tushare_trade_cal.operations import TradeCalDatasetRuntime

    return DatasetPlugin(
        name=TRADE_CAL_SPEC.name,
        provider="tushare",
        spec=TRADE_CAL_SPEC,
        runtime=TradeCalDatasetRuntime(),
        operations=("update", "complete"),
        dependencies=(),
        settings={},
        schedule=("0 9 * * 1", "Asia/Shanghai"),
    )
