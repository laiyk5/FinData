"""Official Tushare daily basic dataset plugin for findata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyarrow as pa

from findata.sdk.contracts import DatasetSpec, provider_date
from findata_plugins.shared.engine import (
    _SECURITY,
    _index_code,
    _materialized,
    _setting_array,
)

if TYPE_CHECKING:
    from findata.sdk.plugins import DatasetPlugin


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


def _daily_basic_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("ts_code", pa.string(), nullable=False),
            pa.field("trade_date", pa.date32(), nullable=False),
            *(pa.field(name, pa.float64(), nullable=True) for name in DAILY_BASIC_FLOAT_FIELDS),
            pa.field("limit_status", pa.int8(), nullable=True),
        ]
    )


DAILY_BASIC_SPEC = DatasetSpec(
    name="findata-plugins/tushare_daily_basic",
    api_name="daily_basic",
    schema=_daily_basic_schema(),
    provider_fields=DAILY_BASIC_FIELDS,
    primary_key=("ts_code", "trade_date"),
    partition_key="ts_code",
    time_field="trade_date",
    missing_data_policy="accept-empty",
    capabilities={"symbol_set_cap": 1, "row_limit": 6000, "time_accumulating": True},
    normalize_rows=_normalize_daily_basic,
)


def _normalize_update_symbols(value: Any, workspace: Any) -> list[str]:
    values = _setting_array(value)
    if len(values) == 1 and values[0] == "all":
        return ["all"]
    for item in values:
        if _SECURITY.fullmatch(item):
            continue
        code = _index_code(item, allow_suffix=True)
        if not _materialized(workspace, code):
            raise ValueError(
                f"unknown index {item!r}; run findata-plugins/tushare_index_basic complete for it first"
            )
    return sorted(values)


def daily_basic_plugin() -> "DatasetPlugin":
    from findata.sdk.plugins import DatasetPlugin, SettingSpec

    from findata_plugins.plugins.datasets.tushare_daily_basic.operations import (
        DailyBasicDatasetRuntime,
    )

    return DatasetPlugin(
        name=DAILY_BASIC_SPEC.name,
        provider="tushare",
        spec=DAILY_BASIC_SPEC,
        runtime=DailyBasicDatasetRuntime(),
        operations=("update", "complete", "refresh"),
        dependencies=("tushare_trade_cal", "tushare_index_basic", "tushare_index_weight"),
        settings={
            "dataset.findata-plugins/tushare_daily_basic.update_symbols": SettingSpec(
                schema={"type": "array", "minItems": 1, "items": {"type": "string"}},
                normalize=_normalize_update_symbols,
                help="Direct securities and Tushare constituent selectors maintained by update.",
                required=True,
            )
        },
        schedule=("40 17 * * 1-5", "Asia/Shanghai"),
    )
