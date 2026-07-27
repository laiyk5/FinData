# Plugin developer experience audit

> Audit date: 2026-07-27
> Branch: `feature/plugin-api-contracts`
> Method: Acted as a first-time plugin developer building a mock plugin family
> (`findata-test/demo` provider + `findata-test/demo_hello` and `findata-test/demo_random`
> datasets) and as a user configuring and running them.

## Summary

The plugin architecture is well-designed at the contract layer — clean protocols, strong
validation, and excellent test coverage for registration invariants. However, the
**developer onboarding experience** has significant friction: high ceremony per plugin,
no scaffold tooling, and a reference implementation (the Tushare family) that is too
complex to serve as a minimal example. The **user experience** lacks a dedicated plugin
management CLI and makes diagnostics (blocked plugins, load failures) hard to surface.

## Findings

### F1 — No scaffold tool (Developer, High)

A new plugin distribution requires **6–10 files** created from scratch:

1. `pyproject.toml` — build config, entry points, dependencies
2. `src/<namespace>/__init__.py` — factory function + `DatasetSpec`
3. `src/<namespace>/operations.py` — `DatasetRuntime` with 8 protocol methods
4. `src/<namespace>/__init__.py` (provider) — factory for `ProviderPlugin`
5. `src/<namespace>/provider.py` — `ProviderRuntime` implementation
6. Plus namespace directories with no `__init__.py` (PEP 420 rule)

There is no `findata plugin scaffold <name>` command. Every file is manual.

**How it felt:** Creating the first plugin took ~40 minutes of writing boilerplate, and
I made two mistakes (context-manager publisher pattern, missing coverage for
time-tracked datasets) that I only caught at runtime.

### F2 — DatasetRuntime protocol has 8 mandatory methods (Developer, High)

For even the simplest dataset (`demo_hello` — a hardcoded 3-column table), the
`DatasetRuntime` protocol requires:

- `operation_worker()` — returns pickle-safe callable
- `normalize_operation()` — validates + canonicalizes operands
- `plan_operation()` — dry-run with no side effects
- `dataset_description()` — status/metadata payload
- `operation_description()` — operand schema
- `resolve_dependency()` — map requirement to operation
- `update_ready()` — whether settings allow parameterless update

Most are trivial delegation (`resolve_dependency` raises ValueError, `update_ready`
returns True, `plan_operation` wraps `normalize_operation`), but they must all be
present. Roughly **60 lines of boilerplate per dataset** just to satisfy the protocol.

A common base class (`DatasetRuntimeBase`) with sensible defaults could reduce this
to **2–3 overrides** for a simple dataset.

### F3 — No minimal walkthrough example (Developer, Medium)

The [DEV.md](./DEV.md) "Adding a dataset plugin" section lists 9 things to define, but
has no concrete code example. The only reference implementation is the Tushare family,
which is necessarily complex (real API client, shared engine, publication windows,
constituent resolution). A developer looking for "what's the simplest thing that works"
has to reverse-engineer from complex code.

The `findata-test/demo_hello` plugin created in this audit could serve as that minimal
example (~90 lines total across 3 files).

### F4 — PEP 420 namespace package gotchas (Developer, Medium)

- `findata_test/` must NOT have `__init__.py` (PEP 420). Having one silently breaks
  the namespace package.
- `pyproject.toml` needs `only-include` + `sources` set correctly, or the wheel ships
  the wrong directory tree.
- Editable installs vs wheel installs behave differently for namespace packages.
- If any of these are wrong, entry points silently don't appear — no error, you just
  don't see your plugin.

A scaffold tool would eliminate all of these.

### F5 — Publisher API is not a context manager (Developer, Medium)

The `Publisher` class uses `publish(table)` / `commit(mutations)` directly. It is not
a context manager. I initially assumed it was — since it manages an exclusive gate —
and got `TypeError: 'Publisher' object does not support the context manager protocol`.

The existing Tushare plugins use `_publisher(spec.name).commit(...)` which is a
one-liner. This pattern should be documented in DEV.md.

### F6 — Coverage-tracked datasets must provide coverage on every publish (Developer, Low)

If `DatasetSpec.time_field` is set, every `publish()` call **must** include coverage
data (a list of `Coverage` objects). The error message is clear:
`StorageError: coverage-tracked dataset commit requires coverage`. But for a new
developer who doesn't know about the coverage mechanism, the fix isn't obvious.

This is actually correct behavior — the coverage mechanism is one of findata's core
features. But it should be called out in the "adding a dataset" walkthrough.

### F7 — No `findata plugin` CLI command (User, High)

There is no dedicated plugin management command. To see what's installed:

- `findata dataset ls` — shows registered datasets (but not failed ones)
- `findata provider ls` — shows registered providers
- `pip list | grep findata` — shows installed packages
- Server logs — show load errors (if you know where to look)

Missing features:
- `findata plugin ls` — list all installed plugin packages with versions
- `findata plugin check <name>` — diagnose why a specific plugin failed to load
- `findata plugin blocked` — show blocklist with effective/ineffective status

### F8 — Blocklist diagnostics require server logs (User, Medium)

When a block is ineffective (dependency repair mounts a blocked plugin anyway), the
warning goes to `logger.warning("plugins.blocked entry ... is ineffective: ...")`.
A user has to know to check server logs to understand why a plugin they blocked is
still active. This should be queryable via the API.

### F9 — Plugin load failures are not queryable (User, Medium)

If an entry point has a syntax error, the wrong return type, or a namespace mismatch,
`PluginRegistrationError` is raised during discovery. This bubbles up as an unhandled
exception during server start or workspace init. There's no endpoint to replay or
inspect the last load attempt.

### F10 — Long configuration key names (User, Low)

Keys like `dataset.findata-test/demo_random.update_symbols` are verbose (58 characters).
Shell completion helps but the cognitive load of remembering the exact path is
noticeable. A shorter alias system — e.g. `demo_random.update_symbols` resolving to
the full key — would help.

## Recommendations (priority order)

### R1 — Scaffold command (`findata plugin scaffold`)
Generate the directory structure, `pyproject.toml`, and template `__init__.py` /
`operations.py` for a new plugin. Eliminates F1, F4 in one feature.

### R2 — `DatasetRuntimeBase` base class
Provide `findata.plugins.DatasetRuntimeBase` with do-nothing defaults for all protocol
methods. Eliminates F2: a simple dataset overrides 2–3 methods instead of 8.

### R3 — `findata plugin` CLI group
Subcommands:
- `ls` — list installed plugins with package version, entry point status
- `check <name>` — diagnose why a plugin didn't load
- `blocked` — show blocklist with effective/ineffective annotation

Eliminates F7, F8, F9.

### R4 — Minimal example in user docs (`docs/site/guide/custom-datasets.md`)
Add a "Minimal walkthrough" section using `demo_hello` as a complete end-to-end example
(~90 lines of plugin code total). This section should be in the published user
documentation, not in the internal DEV.md. Eliminates F3.

### R5 — Publisher usage note in user docs
Document the `publisher.publish(table)` pattern and the coverage requirement in the
"Operation worker" section of `custom-datasets.md`. Eliminates F5, F6.

### R6 — Export plugin load errors via API
Add a `/v1/plugins/diagnostics` endpoint that stores and serves the last discovery
errors, so users can query them without server log access.

---

## What was tested

| Test | Result |
|------|--------|
| Entry-point discovery (`findata.providers`, `findata.datasets`) | ✅ |
| Workspace initialization with discovery | ✅ |
| Provider runtime protocol conformance | ✅ |
| Dataset runtime protocol conformance | ✅ |
| Task execution (complete, update, refresh) | ✅ |
| DataLoader read after committed task | ✅ |
| DataLoader query with keys, columns, ordering | ✅ |
| Coverage recording and querying | ✅ |
| Blocklist — unrequired plugin block | ✅ |
| Blocklist — ineffective block (dependency repair) | ✅ |
| Blocklist — clear and restore | ✅ |
| Config set/get/unset for plugin settings | ✅ |
| CLI human output format | ✅ |

## Demo plugin stats

| Plugin | Lines | Files | Operations |
|--------|-------|-------|------------|
| `findata-test-providers-demo` | ~40 | 3 | N/A (provider) |
| `findata-test-datasets-demo-hello` | ~195 | 3 | update, complete, refresh |
| `findata-test-datasets-demo-random` | ~220 | 3 | update, complete, refresh |
