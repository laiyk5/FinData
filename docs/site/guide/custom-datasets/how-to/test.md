# How to test your plugin

The `findata.testing` module provides helpers for writing plugin tests.

## RecordingReporter

Captures `log()`, `diagnostic()`, and `progress()` calls for assertions without
running a real operation:

```python
from findata.testing import RecordingReporter

reporter = RecordingReporter()
reporter.log("hello")
reporter.diagnostic("warning", "MY_CODE", "something happened")
assert "hello" in reporter.logs
assert reporter.diagnostics[0]["code"] == "MY_CODE"
```

## FakeDatasetRuntime

A configurable `DatasetRuntimeBase` for testing registration and validation without
a real operation engine:

```python
from findata.testing import FakeDatasetRuntime, make_dataset_plugin

runtime = FakeDatasetRuntime(spec=MY_SPEC)
plugin = make_dataset_plugin(spec=MY_SPEC, runtime=runtime)
# validate_plugins accepts it just like a real runtime
```

## Quick factories

Build minimal plugin instances for tests:

```python
from findata.testing import make_provider_plugin, make_dataset_plugin

provider = make_provider_plugin("mycompany/myprovider")
dataset = make_dataset_plugin(MY_SPEC, provider="mycompany/myprovider")
```

## create_test_workspace

Context manager that creates a temporary workspace with registered plugins:

```python
from findata.testing import create_test_workspace

with create_test_workspace(plugins=[my_plugin]) as ws:
    assert my_plugin.name in [p.name for p in ws.datasets_root.rglob("dataset.duckdb")]
```
