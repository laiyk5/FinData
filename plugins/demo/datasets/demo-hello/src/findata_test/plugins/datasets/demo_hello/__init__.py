"""Minimal example dataset plugin for findata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyarrow as pa

from findata.sdk.contracts import DatasetSpec

if TYPE_CHECKING:
    from findata.sdk.plugins import DatasetPlugin


HELLO_FIELDS = ("name", "greeting", "count")


def _hello_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("name", pa.string(), nullable=False),
            pa.field("greeting", pa.string(), nullable=False),
            pa.field("count", pa.int64(), nullable=False),
        ]
    )


HELLO_SPEC = DatasetSpec(
    name="findata-test/demo_hello",
    api_name="demo_hello",
    schema=_hello_schema(),
    provider_fields=HELLO_FIELDS,
    primary_key=("name",),
    partition_key="name",
)


def _hello_rows(rows: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for i in range(rows):
        result.append(
            {
                "name": f"user_{i:04d}",
                "greeting": f"Hello, user {i}!",
                "count": i * 100,
            }
        )
    return result


def demo_hello_plugin() -> "DatasetPlugin":
    from findata.sdk.plugins import DatasetPlugin

    from findata_test.plugins.datasets.demo_hello.operations import (
        HelloDatasetRuntime,
    )

    return DatasetPlugin(
        name=HELLO_SPEC.name,
        provider="demo",
        spec=HELLO_SPEC,
        runtime=HelloDatasetRuntime(),
        operations=("update", "complete", "refresh"),
    )
