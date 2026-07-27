# Plugin architecture

findata discovers plugins through Python entry points. Installing a plugin distribution
is the only step — the next server start discovers, validates, registers, and serves it.
Uninstalling removes it. No framework changes are needed.

## Two plugin kinds

**Provider plugins** declare a data source: configuration schema, rate limits, and a
runtime that checks readiness. Example: `findata-test/demo` is a provider that is always
ready and returns mock data.

**Dataset plugins** define one logical dataset: its Arrow schema, primary key,
operations, settings, and a runtime that executes those operations. Example:
`findata-test/demo_random` is a dataset that generates random-walk price data.

A dataset always references exactly one provider. Multiple datasets can share the
same provider.

## Discovery and mounting

Every plugin package declares its entry points in `pyproject.toml`:

```toml
[project.entry-points."findata.providers"]
demo = "findata_test.plugins.providers.demo:provider_plugin"

[project.entry-points."findata.datasets"]
demo_random = "findata_test.plugins.datasets.demo_random:demo_random_plugin"
```

When the server starts, it reads all installed entry points, validates each plugin's
contract, and registers matching datasets in the workspace. A plugin whose full name
doesn't match its Python package namespace is rejected.

### Naming

A plugin's full name is `<package-namespace>/<local-name>` — for example,
`findata-test/demo_random`. The namespace is the top-level Python package name,
derived from the entry point's module path. This prevents impersonation: a plugin
physically cannot claim a namespace it doesn't live in.

### Resilient loading

If a plugin distribution has an import error or a broken entry point, it is skipped
and the error is recorded. The server starts with the remaining plugins. Run
`findata plugin check <name>` to diagnose why a specific plugin failed to load.

## Blocklist

A workspace can block plugins via the `plugins.blocked` configuration key:

```bash
findata plugin block findata-test/demo_random
findata plugin unblock findata-test/demo_random
findata plugin blocked
```

A blocked plugin is not registered. However, if an unblocked plugin depends on a
blocked one (as a data dependency or its provider), the block is **ineffective**
and the required plugin is mounted anyway.

The blocklist is read at server startup only — changes take effect on the next
restart.

## Plugin SDK

The `findata.sdk` module provides a single import surface for plugin authors:

```python
from findata.sdk import DatasetPlugin, DatasetRuntimeBase, ProviderPlugin, ...
```

- `DatasetRuntimeBase` reduces the seven-method `DatasetRuntime` protocol to one
  required override (`operation_worker`).
- `findata.testing` provides test helpers: `RecordingReporter`, `FakeDatasetRuntime`,
  `create_test_workspace`.
- `findata plugin scaffold <namespace> <name>` generates a complete plugin family.
