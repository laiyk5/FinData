"""Dataset plugin for mycompany/hello."""

import pyarrow as pa
from findata.sdk import DatasetPlugin, DatasetRuntimeBase, DatasetSpec

FIELDS = ('key', 'value')

def _schema() -> pa.Schema:
    return pa.schema([
        pa.field("key", pa.string(), nullable=False),
        pa.field("value", pa.float64(), nullable=True),
    ])

SPEC = DatasetSpec(
    name="mycompany/hello",
    api_name="hello",
    schema=_schema(),
    provider_fields=FIELDS,
    primary_key=("key",),
)


def hello_plugin():
    from mycompany.plugins.datasets.hello.operations import HelloRuntime
    return DatasetPlugin(
        name=SPEC.name,
        provider="hello",
        spec=SPEC,
        runtime=HelloRuntime(),
        operations=("update", "complete", "refresh"),
    )
