"""Official Tushare fund daily (ETF) dataset plugin for findata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyarrow as pa

from findata.sdk.contracts import DatasetSpec, provider_date

if TYPE_CHECKING:
    from findata.sdk.plugins import DatasetPlugin


FUND_DAILY_FLOAT_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)
FUND_DAILY_FIELDS = ("ts_code", "trade_date", *FUND_DAILY_FLOAT_FIELDS)


def _normalize_fund_daily(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        normalized["trade_date"] = provider_date(row["trade_date"])
        for name in FUND_DAILY_FLOAT_FIELDS:
            value = row.get(name)
            normalized[name] = None if value in (None, "") else float(value)
        result.append(normalized)
    return result


def _fund_daily_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("ts_code", pa.string(), nullable=False),
            pa.field("trade_date", pa.date32(), nullable=False),
            *(pa.field(name, pa.float64(), nullable=True) for name in FUND_DAILY_FLOAT_FIELDS),
        ]
    )


FUND_DAILY_SPEC = DatasetSpec(
    name="findata-plugins/tushare_fund_daily",
    api_name="fund_daily",
    schema=_fund_daily_schema(),
    provider_fields=FUND_DAILY_FIELDS,
    primary_key=("ts_code", "trade_date"),
    partition_key="ts_code",
    time_field="trade_date",
    missing_data_policy="accept-empty",
    capabilities={"row_limit": 5000, "time_accumulating": True},
    normalize_rows=_normalize_fund_daily,
)


def _normalize_update_symbols(value: Any, workspace: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [str(item) for item in value]
    else:
        raise ValueError(
            f"expected a string or array of fund codes, got {type(value).__name__}"
        )
    return sorted(set(values))


def fund_daily_plugin() -> "DatasetPlugin":
    from findata.sdk.plugins import DatasetPlugin, SettingSpec

    from findata_plugins.plugins.datasets.tushare_fund_daily.operations import (
        FundDailyDatasetRuntime,
    )

    return DatasetPlugin(
        name=FUND_DAILY_SPEC.name,
        provider="tushare",
        spec=FUND_DAILY_SPEC,
        runtime=FundDailyDatasetRuntime(),
        operations=("update", "complete", "refresh"),
        dependencies=(),
        settings={
            f"dataset.{FUND_DAILY_SPEC.name}.update_symbols": SettingSpec(
                schema={"type": "array", "minItems": 1, "items": {"type": "string"}},
                normalize=_normalize_update_symbols,
                help="Fund codes maintained by update (e.g. ['159919.SZ', '510050.SH']).",
                required=True,
            ),
        },
        schedule=("40 17 * * 1-5", "Asia/Shanghai"),
    )
