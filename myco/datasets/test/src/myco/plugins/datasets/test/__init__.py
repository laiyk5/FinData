"""Dataset plugin for myco/test."""

import pyarrow as pa
from findata.sdk import DatasetPlugin, DatasetRuntimeBase, DatasetSpec

FIELDS = ('key', 'value')

def _schema() -> pa.Schema:
    return pa.schema([
        pa.field("key", pa.string(), nullable=False),
        pa.field("value", pa.float64(), nullable=True),
    ])

SPEC = DatasetSpec(
    name="myco/test",
    api_name="test",
    schema=_schema(),
    provider_fields=FIELDS,
    primary_key=("key",),
)


def test_plugin():
    from myco.plugins.datasets.test.operations import TestRuntime
    return DatasetPlugin(
        name=SPEC.name,
        provider="test",
        spec=SPEC,
        runtime=TestRuntime(),
        operations=("update", "complete", "refresh"),
    )
