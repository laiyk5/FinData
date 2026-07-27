# Plan: Fix plugin development and loading support gaps

## Context

findata's plugin architecture is well-designed at the contract layer (protocols, validation,
entry-point discovery), but it has several gaps that make plugin development painful and
plugin management invisible. These surfaced during the audit in
`docs/design/plugin-developer-experience.md` and are confirmed by code inspection.

The core problems:

1. **Plugin loading is fragile.** `discover_provider_plugins()` and `discover_dataset_plugins()`
   have no exception handling. A single plugin with an import error crashes server startup.
   There's no graceful degradation, no partial loading, and no way to query what failed.

2. **No plugin lifecycle management.** There's no `findata plugin` CLI. Users can't list
   installed plugins, check versions, see what failed to load, or diagnose why a block
   was ineffective — all of this requires server log spelunking.

3. **Developer boilerplate is high.** `DatasetRuntime` requires 8 methods even for the
   simplest dataset. No base class with defaults exists.

4. **No scaffold tool.** Creating a new plugin means 6+ manual files with correct PEP 420
   namespace packaging, entry points, and hatchling config — easy to get wrong.

5. **No test SDK.** Mock infrastructure is coupled to the Tushare provider. Plugin authors
   have to build their own.

## Design — 5 workstreams

### Workstream A: Resilient plugin loading

**Problem:** `discover_provider_plugins()` and `discover_dataset_plugins()` in
`src/findata/plugins.py` don't catch exceptions. An import error in any entry point
propagates up through `initialize_workspace()` and `FindataServer.__init__()`, preventing
the server from starting.

**Solution:**
- Add `discover_providers_safe()` and `discover_datasets_safe()` wrappers that catch
  `PluginRegistrationError` and `Exception` per entry point.
- Store load errors in a new `_plugin_load_errors: dict[str, str]` module-level dict.
- Add `plugin_load_errors() -> dict[str, str]` accessor.
- Failed plugins are skipped; the server starts with the rest.
- Every call site (`initialize_workspace`, `FindataServer.__init__`,
  `PluginWorkerDispatcher.__call__`) uses the safe wrappers.

**Files:**
- `src/findata/plugins.py` — add `discover_providers_safe()`, `discover_datasets_safe()`,
  `plugin_load_errors()`, and `PluginLoadError` dataclass (stores entry point name, group,
  error type, error message)
- `src/findata/server.py` — log load errors at startup

### Workstream B: `findata plugin` CLI

**Problem:** No CLI for plugin introspection. Users can't answer "what plugins are
installed?", "what version?", "why didn't this plugin load?", or "what's blocked?".

**Solution:** Add a `findata plugin` command group to the CLI.

```
findata plugin ls           — list installed packages with versions, entry point status
findata plugin check <name> — diagnose why a specific plugin didn't load
findata plugin blocked      — show blocklist with effective/ineffective annotation
```

- `plugin ls` reads from `importlib.metadata` directly (no server needed) — shows
  distribution name, version, and which `findata.*` entry points it exposes.
- `plugin check` looks up the plugin name and reports: entry point found/not found,
  load error if any, registration status.
- `plugin blocked` reads the workspace's `plugins.blocked` config and the effective
  blocklist from `apply_plugin_blocklist()` output.

**Files:**
- `src/findata/click_parser.py` — add `plugin` command group
- `src/findata/cli.py` — add plugin command handlers
- `src/findata/plugins.py` — add `plugin_load_errors()` accessor, `format_plugin_diagnostic()`

### Workstream C: `DatasetRuntimeBase` base class

**Problem:** `DatasetRuntime` protocol requires 8 methods. For a simple dataset, 5 are
trivial delegation. Tushare plugins repeat identical patterns across 5 datasets.

**Solution:** Add `DatasetRuntimeBase` in `findata.plugins` with default implementations
that cover the common cases.

```python
class DatasetRuntimeBase:
    """Base class for DatasetRuntime with sensible defaults."""

    spec: DatasetSpec | None = None  # override in subclass

    def operation_worker(self, workspace, *, mode, today, now): ...

    def normalize_operation(self, operation, operands, *, today): ...

    def plan_operation(self, workspace, operation, operands, *, today): ...

    def dataset_description(self, workspace, *, provider_ready): ...

    def operation_description(self, operation): ...

    def resolve_dependency(self, target, requirement): ...
        raise ValueError("no declared dependencies")

    def update_ready(self, workspace): ...
        return True
```

The base class delegates `spec` to a class attribute, implements `dataset_description`
with DataLoader, and provides safe defaults for `resolve_dependency`, `update_ready`,
and `plan_operation`. A minimal dataset overrides `operation_worker` and optionally
`normalize_operation`.

**Files:**
- `src/findata/plugins.py` — add `DatasetRuntimeBase` class (at module level, after
  `DatasetRuntime` protocol)
- Optionally update the `validate_plugins` function to accept both `DatasetRuntime`
  protocol and `DatasetRuntimeBase` instances.

### Workstream D: `findata plugin scaffold` generator

**Problem:** No scaffold tool. New plugin authors must create directories, `pyproject.toml`,
`__init__.py`, `operations.py` with correct PEP 420 namespace packaging and entry points.

**Solution:** Add `findata plugin scaffold <namespace> <name>` that generates a complete
plugin family from templates.

```
findata plugin scaffold mycompany hello
```

Creates:
```
mycompany/
├── provider/
│   ├── pyproject.toml
│   └── src/
│       └── mycompany/
│           └── plugins/providers/hello/
│               ├── __init__.py
│               └── provider.py
├── datasets/
│   └── hello/
│       ├── pyproject.toml
│       └── src/
│           └── mycompany/
│               └── plugins/datasets/hello/
│                   ├── __init__.py
│                   └── operations.py
└── umbrella/
    └── pyproject.toml
```

Template files use the minimal walkthrough from `custom-datasets.md` as their content.
The scaffold validates the namespace name, handles PEP 420 directory setup, and
generates correct `pyproject.toml` with entry points and hatchling config.

**Files:**
- `src/findata/click_parser.py` — add `plugin scaffold` subcommand
- `src/findata/cli.py` — add scaffold handler
- `src/findata/plugins.py` or new `src/findata/scaffold.py` — template generation logic

### Workstream E: Plugin test SDK

**Problem:** No shared test utilities for plugin authors. `MockTushareTransport` is in
`findata_plugins.shared.testing` and is Tushare-specific. Plugin authors have to build
their own mock infrastructure.

**Solution:** Add `findata.testing` module with:
- `MockWorkspace` — creates a temporary workspace with proper directory structure
- `RecordingReporter` — captures reporter calls for assertions (already in test code,
  promote to public API)
- `FakeDatasetRuntime` — a minimal runtime for testing registration/validation
- `plugin_registration_fixture` — helper to set up plugins in a temp workspace

**Files:**
- `src/findata/testing/__init__.py` — public test utilities

## Implementation order

1. **Workstream A** (resilient loading) — highest impact, smallest change. Server shouldn't
   crash on a bad plugin. Also unlocks workstream B's `plugin check`.

2. **Workstream B** (plugin CLI) — makes plugin management visible. Depends on A for
   load error surfacing.

3. **Workstream C** (DatasetRuntimeBase) — reduces boilerplate by ~60% for new plugins.
   Independent of A and B.

4. **Workstream D** (scaffold) — best developer experience win. Builds on C (templates
   use DatasetRuntimeBase).

5. **Workstream E** (test SDK) — nice-to-have. Independent of others.

## Verification

After each workstream:

- **A:** Install a plugin with a syntax error → server starts, `plugin_load_errors()` shows
  the error, other plugins load fine.
- **B:** `findata plugin ls` shows installed packages. `findata plugin check nonexistent`
  shows clear diagnostic. `findata plugin blocked` shows blocklist state.
- **C:** A new dataset overriding 2-3 methods instead of 8 works and passes validation.
  Existing tushare plugins still pass all tests.
- **D:** `findata plugin scaffold myco demo` creates loadable packages. `uv sync` and
  `findata-server init` discover them.
- **E:** Test utilities can be imported from `findata.testing` and work without Tushare.

Existing test suite must pass: `uv run pytest -q -x`
