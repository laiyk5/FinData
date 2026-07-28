"""Official Tushare index daily dataset plugin for findata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyarrow as pa

from findata.sdk.contracts import DatasetSpec, OperandError, provider_date
from findata_plugins.tushare.shared.engine import _normalize_index_reference

if TYPE_CHECKING:
    from findata.sdk.plugins import DatasetPlugin


INDEX_DAILY_FIELDS = (
    "ts_code", "trade_date", "close", "open", "high", "low", "pre_close", "change", "pct_chg", "vol", "amount",
)


def _normalize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        normalized["trade_date"] = provider_date(row["trade_date"])
        for field in INDEX_DAILY_FIELDS[2:]:
            value = row.get(field)
            normalized[field] = None if value in (None, "") else float(value)
        result.append(normalized)
    return result


INDEX_DAILY_SPEC = DatasetSpec(
    name="findata-plugins/tushare_index_daily",
    api_name="index_daily",
    schema=pa.schema([
        pa.field("ts_code", pa.string(), nullable=False), pa.field("trade_date", pa.date32(), nullable=False),
        *[pa.field(field, pa.float64(), nullable=True) for field in INDEX_DAILY_FIELDS[2:]],
    ]),
    provider_fields=INDEX_DAILY_FIELDS,
    primary_key=("ts_code", "trade_date"),
    partition_key="ts_code",
    time_field="trade_date",
    normalize_rows=_normalize,
    capabilities={"time_accumulating": True},
)


def _normalize_update_indexes(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise OperandError("update_indexes must be a nonempty array")
    return sorted({_normalize_index_reference(str(item)) for item in value})


def index_daily_plugin() -> "DatasetPlugin":
    from findata.sdk.plugins import DatasetPlugin, SettingSpec
    from findata_plugins.tushare.plugins.datasets.index.index_daily.operations import IndexDailyDatasetRuntime

    return DatasetPlugin(
        name=INDEX_DAILY_SPEC.name, provider="tushare", spec=INDEX_DAILY_SPEC,
        runtime=IndexDailyDatasetRuntime(), operations=("update", "complete", "refresh"),
        dependencies=("tushare_trade_cal", "tushare_index_basic"),
        settings={f"dataset.{INDEX_DAILY_SPEC.name}.update_indexes": SettingSpec(schema={"type": "array", "items": {"type": "string"}, "minItems": 1}, normalize=_normalize_update_indexes, help="Indexes to maintain; defaults to locally covered indexes.", required=False, default=["stored"])},
        schedule=("40 17 * * 1-5", "Asia/Shanghai"), family=("tushare", "index"),
    )
