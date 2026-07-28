"""Official Tushare index weight dataset plugin for findata."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import pyarrow as pa

from findata.sdk.contracts import DatasetSpec, provider_date
from findata_plugins.tushare.shared.engine import _index_code, _materialized, _setting_array

if TYPE_CHECKING:
    from findata.sdk.plugins import DatasetPlugin


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


INDEX_WEIGHT_FIELDS = ("index_code", "con_code", "trade_date", "weight")

INDEX_WEIGHT_SPEC = DatasetSpec(
    name="findata-plugins/tushare_index_weight",
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
    missing_data_policy="accept-empty",
    capabilities={"time_accumulating": True},
    normalize_rows=_normalize_index_weight,
)


def _normalize_update_indexes(value: Any, workspace: Any) -> list[str]:
    values = _setting_array(value)
    for item in values:
        if item == "stored":
            continue
        code = _index_code(item, allow_suffix=False)
        if not _materialized(workspace, code):
            raise ValueError(
                f"unknown index {item!r}; run findata-plugins/tushare_index_basic complete for it first"
            )
    return sorted(values)


def index_weight_plugin() -> "DatasetPlugin":
    from findata.sdk.plugins import DatasetPlugin, SettingSpec

    from findata_plugins.tushare.plugins.datasets.index.index_weight.operations import (
        IndexWeightDatasetRuntime,
    )

    return DatasetPlugin(
        name=INDEX_WEIGHT_SPEC.name,
        provider="tushare",
        spec=INDEX_WEIGHT_SPEC,
        runtime=IndexWeightDatasetRuntime(),
        operations=("update", "complete"),
        dependencies=("tushare_index_basic",),
        settings={
            "dataset.findata-plugins/tushare_index_weight.update_indexes": SettingSpec(
                schema={"type": "array", "minItems": 1, "items": {"type": "string"}},
                normalize=_normalize_update_indexes,
                help="Exact Tushare index references maintained by update"
                " (defaults to the indexes already covered by this dataset).",
                required=False,
                default=["stored"],
            )
        },
        schedule=("0 18 * * 1", "Asia/Shanghai"),
        family=("tushare", "index"),
    )
