"""Official Tushare index basic dataset plugin for findata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyarrow as pa

from findata.sdk.contracts import DatasetSpec, provider_date

if TYPE_CHECKING:
    from findata.sdk.plugins import DatasetPlugin


def _normalize_index_basic(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        normalized["base_date"] = provider_date(row.get("base_date"), nullable=True)
        normalized["list_date"] = provider_date(row.get("list_date"), nullable=True)
        normalized["exp_date"] = provider_date(row.get("exp_date"), nullable=True)
        value = row.get("base_point")
        normalized["base_point"] = None if value in (None, "") else float(value)
        result.append(normalized)
    return result


INDEX_BASIC_FIELDS = (
    "ts_code",
    "name",
    "fullname",
    "market",
    "publisher",
    "index_type",
    "category",
    "base_date",
    "base_point",
    "list_date",
    "weight_rule",
    "desc",
    "exp_date",
)

INDEX_BASIC_SPEC = DatasetSpec(
    name="findata-plugins/tushare_index_basic",
    api_name="index_basic",
    schema=pa.schema(
        [
            pa.field("ts_code", pa.string(), nullable=False),
            pa.field("name", pa.string(), nullable=False),
            pa.field("fullname", pa.string(), nullable=True),
            pa.field("market", pa.string(), nullable=False),
            pa.field("publisher", pa.string(), nullable=True),
            pa.field("index_type", pa.string(), nullable=True),
            pa.field("category", pa.string(), nullable=True),
            pa.field("base_date", pa.date32(), nullable=True),
            pa.field("base_point", pa.float64(), nullable=True),
            pa.field("list_date", pa.date32(), nullable=True),
            pa.field("weight_rule", pa.string(), nullable=True),
            pa.field("desc", pa.string(), nullable=True),
            pa.field("exp_date", pa.date32(), nullable=True),
        ]
    ),
    provider_fields=INDEX_BASIC_FIELDS,
    primary_key=("ts_code",),
    normalize_rows=_normalize_index_basic,
)


def index_basic_plugin() -> "DatasetPlugin":
    from findata.sdk.plugins import DatasetPlugin

    from findata_plugins.plugins.datasets.tushare_index_basic.operations import (
        IndexBasicDatasetRuntime,
    )

    return DatasetPlugin(
        name=INDEX_BASIC_SPEC.name,
        provider="tushare",
        spec=INDEX_BASIC_SPEC,
        runtime=IndexBasicDatasetRuntime(),
        operations=("update", "complete"),
        dependencies=(),
        settings={},
        schedule=None,
    )
