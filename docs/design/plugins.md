# Plugin architecture

This file owns the goals and decoupling invariants of the plugin-based provider/dataset
design. [core.md](core.md) owns the runtime architecture those plugins plug into;
[DEV.md](../DEV.md) owns the contributor workflow for adding one; the author-facing
walkthrough lives in the [custom-datasets guide](../site/guide/custom-datasets.md).

## Design goals

1. **Plugins are decoupled from each other at the code level.** A dataset plugin may
   depend on another dataset's *data* — never on its *code*. No plugin imports another
   dataset plugin's package.
2. **Plugins are decoupled from core.** Core knows plugins only through entry-point
   discovery and the typed contracts. No core module imports a plugin package or
   branches on a specific provider or dataset name.
3. **Adding a plugin never changes core code.** A new provider or dataset ships as its
   own Python distribution; installing it is the only step required.
4. **The framework installs no datasets.** `pip install findata` yields the framework
   only. Plugins are ordinary packages that depend on `findata`, installed singly or as
   a family collection, at a packaging granularity the publisher chooses.
5. **Third-party plugins mount identically.** Same entry points, same contracts, same
   naming rules, no privileged path.

## Naming: the package namespace

A plugin's full name is `<package-namespace>/<local-name>`. The namespace is a Python
namespace package ([PEP 420](https://packaging.python.org/guides/packaging-namespace-packages/))
shared by one publisher's plugin distributions; uniqueness is enforced mechanically by
the packaging ecosystem itself — one environment cannot hold two distributions with the
same name, so `findata-plugins` or `acme-finance` needs no registry and no
identity verification. The local name is unique per plugin kind within the namespace,
which the namespace owner controls and the framework validates at discovery.

- Full-name shape: `<namespace>/<local>` with `[a-z0-9_-]+` components.
- The namespace of a plugin is derived, not declared: an entry point's module path
  determines its top-level package (e.g. `findata_plugins.tushare.plugins.datasets.stock.foo` →
  `findata-plugins`), and validation requires the plugin's full name to match
  `<that namespace>/<leaf name>`. A plugin can never impersonate another namespace —
  it physically cannot live there.
- Official example: namespace `findata_plugins`; provider `findata-plugins/tushare`
  at `findata_plugins.tushare.plugins.providers.tushare`; stock datasets such as
  `findata-plugins/tushare_daily_basic` at
  `findata_plugins.tushare.plugins.datasets.stock.daily_basic`; shared machinery at
  `findata_plugins.tushare.shared`. Third parties choose their own repository and family segments.
- The framework's own top-level package (`findata`) is a regular package and is never
  a plugin namespace.

Names are structural everywhere they flow: storage nests by dataset name
(`datasets/findata-plugins/tushare_daily_basic/`, snapshots mirror it), configuration
keys are `dataset.<full-name>.<setting>` and `provider.<full-name>.<field>` (resolved
by registry longest-match where parsing is required), HTTP routes match registered
names greedily instead of counting path segments, and `_findata_metadata` stores the
full dataset name. Renames are breaking for existing workspaces (dataset directories,
configuration keys, task history); v1 is unreleased, so they ship as breaking changes
with manual migration notes rather than core-carried migration code.

A dataset plugin references its provider **by name**, resolved within its namespace
first: `findata-plugins/tushare_daily_basic` declaring `provider="tushare"` resolves to
`findata-plugins/tushare`; a provider from another namespace is written as a full name
(`acme-finance/tushare`). An unresolved reference fails registration with an
unknown-provider error. Data dependencies resolve the same way (namespace-relative
shorthand, cross-namespace by full name).

## Granularity: plugins, distributions, and the family repository

A **plugin** is a contract unit: one full name, one entry point, one isolated
subpackage. A **distribution** is an install and versioning unit. A **family
repository** is a maintenance unit only — a thin layer that hosts one namespace's
plugin distributions, their shared machinery, and their shared test/mock code in one
place. The three granularities are independent:

- plugins stay mutually independent: only *data* dependencies between them, never
  imports, whether they share a distribution or not;
- independent distributions contribute subpackages into one shared namespace package
  (PEP 420), so install granularity stays per plugin: a dependency-free dataset plugin
  installs and mounts alone, and a dependent one pulls exactly its own dependency
  chain (the package manager resolves the mirrored hard package dependencies) — never
  the whole namespace by force;
- the family repository adds an umbrella distribution (metadata only, no entry points)
  for users who *want* the whole namespace in one install.

Within a namespace, plugin code follows the layout `<ns>.<repository>.plugins.providers`
and `<ns>.<repository>.plugins.datasets.<family>`, with shared machinery at
`<ns>.<repository>.shared`. Repository and family segments are publisher-owned and may have any
depth; they classify packages without changing plugin IDs. The framework only requires the
namespace/full-name coherence described under *Naming*. Plugins expose the same family path as
metadata so clients can group them without parsing package imports.

## Dependency model: the relations, kept apart

| relation | expressed as | allowed directions | forbidden |
| --- | --- | --- | --- |
| **data dependency** (dataset → dataset) | `DatasetPlugin.dependencies` name strings | any dataset → any dataset, acyclic, cross-namespace allowed | imports or code calls |
| **provider reference** (dataset → provider) | `DatasetPlugin.provider` name string (namespace-relative allowed) | a dataset plugin → exactly one provider plugin | imports or code calls into another provider |
| **package dependency** (install-time) | distribution `dependencies` metadata | dataset distribution → its provider distribution (any namespace — reusing a provider is the point); dataset distribution → the distributions providing its declared data dependencies (any namespace) | dataset→dataset package edges unrelated to data dependencies |
| **code sharing** | the three tiers below | the most general honestly-true tier | copy-paste across plugins; dataset-package imports |

Data dependencies are name strings, resolved at validation with namespace-relative
shorthand (`"tushare_trade_cal"` inside the `findata-plugins` namespace
resolves to `findata-plugins/tushare_trade_cal`). They flow at runtime through exactly two channels: the
reporter's `fulfill(dataset, requirement)` (the framework executes the dependency
through the parent's `resolve_dependency`) and DataLoader reads of a committed revision
(settings normalization, planning). The dependent plugin never knows the provider
plugin's package name.

The provider reference works the same way at the registry level: the dataset plugin
names its provider (resolved namespace-relative like a data dependency), and the dataset
**distribution** declares hard package dependencies on the distributions it imports —
its namespace's provider distribution (the adapter: client, transport, selector syntax)
and shared distribution (engine, mock, publication timing) — so installing a dataset
plugin brings its provider along. Provider distributions never import dataset
packages.

Data dependencies that a plugin's `update` requires **and that live in another
distribution** are mirrored as hard package dependencies — same-namespace or
cross-namespace alike — so installing one dataset plugin lets the package manager
resolve and pull the packages that provide its dependency data. Within one
distribution the dependency ships together by construction and needs no metadata.
This is metadata only: the package manager keeps installations
complete while imports remain forbidden. A missing data dependency at runtime still
fails validation with a clear error.

## Plugin SDK

Plugin SDK services live in the `findata` distribution as public modules. There is no
separate `findata-sdk` distribution; extraction into one is deferred until a second
independent consumer outside this repository demonstrates the need.

Three public surfaces, all under `findata`:

### `findata.sdk` — unified import module

A single re-export module so plugin authors write one import instead of hunting across
`findata.contracts`, `findata.plugins`, and `findata.storage`:

```python
from findata.sdk import (
    Coverage, DataLoader, DataMutation, DatasetPlugin, DatasetRuntimeBase,
    DatasetSpec, DateRange, OperandError, OperationReporter, OperationRequest,
    OperationWorker, ProviderPlugin, ProviderRuntime, SettingSpec, Workspace,
    discover_provider_plugins, discover_dataset_plugins,
    validate_plugins, validate_provider_plugins, register_plugins,
    plugin_blocklist, plugin_load_errors,
)
```

No new logic — pure re-exports. The original import paths continue working. The toolkit
package (`findata.toolkit`) is opt-in and documented separately, not included here.

### `findata.testing` — test utilities

Plugin authors need shared test infrastructure without depending on Tushare internals:

- **`RecordingReporter`** — minimal `OperationReporter` that captures `log()`,
  `diagnostic()`, `progress()` calls for test assertions.
- **`FakeDatasetRuntime`** — `DatasetRuntimeBase` subclass with a configurable worker
  for testing registration and validation without a real operation engine.
- **`create_test_workspace`** — context manager that creates a temp workspace with
  registered datasets.

Framework-provided mock transports are excluded: each provider family has its own API
shape, so mocking is inherently domain-specific.

### `findata plugin scaffold` — code generator

The CLI command `findata plugin scaffold <namespace> <name>` generates a complete plugin
family directory tree in the current working directory:

- Validates `<namespace>` matches `[a-z][a-z0-9_-]*`
- Creates `./<namespace>/` with provider, dataset, and umbrella packages
- Templates use `DatasetRuntimeBase` and `findata.sdk` imports
- PEP 420 namespace directories are set up correctly (no `__init__.py`)
- Refuses to overwrite an existing directory
- Deleting generated files is the uninstall path

Generated code targets this tier model: a new plugin imports from `findata.sdk` (tier
1) and its own namespace's shared subpackage (tier 2). The scaffold is purely a
developer convenience — it does not register, publish, or depend on any remote service.

## Code sharing: three tiers by generality

When plugins genuinely share code, it lives at the most general tier it honestly fits:

1. **findata SDK** (in `findata`): framework-generic code — `findata.contracts`,
   `findata.plugins` (contract layer) and `findata.toolkit` (dataset-neutral helpers,
   promoted on second use, catalogued, boundary-tested). The only layer plugin authors
   may treat as stable long-term.
2. **Namespace shared subpackage**: code specific to one plugin namespace and shared by
   its dataset plugins (publication timing, the mock transport, the shared operation
   engine). It lives at `<ns>.shared`; provider adapters (client, transport, selector
   syntax) live in the provider plugin's own subpackage instead. Contributing
   distributions declare package dependencies on what they import. Both import the SDK;
   the framework knows nothing about either.
3. **findata-unrelated third-party library**: domain-generic code with no findata
   imports, versioned independently. (No v1 candidate; the rule is recorded ahead of
   need.)

## Contracts: two runtimes

Behavior is dispatched through two protocols in `findata.plugins`:

- `ProviderPlugin.provider_id` is the provider's full name (`findata-plugins/tushare`);
  `ProviderRuntime` — provider scope only: `ready`, `is_mock`, `probe`. Configuration,
  credentials, transports, and rate limiting are the provider plugin's internal
  business.
- `DatasetRuntime` — dataset scope, carried by every `DatasetPlugin`:
  `operation_worker`, `normalize_operation`, `plan_operation`,
  `dataset_description`, `operation_description`, `resolve_dependency`,
  `update_ready`.

The server resolves dataset-scoped calls through `plugin.runtime`; the task worker is a
`PluginWorkerDispatcher` that resolves the executing dataset's runtime inside the child
process, so a plugin installed after server start is picked up on the next dispatch.
`DatasetPlugin` also declares `schedule` (suggested cron expression + IANA timezone).

## Discovery, mounting, and the blocklist

Discovery validates each entry point against the naming rules under *Naming* — the
plugin's full name must match its module's namespace — for official and third-party
plugins alike; there is no distribution-name prefix convention beyond that coherence
check. Installed plugins **mount automatically**: discovery → namespace validation →
dependency validation → storage registration, with no configuration required, and
unmount on uninstall. A running server can reload discovery or remove and restore a mounted plugin
without a process restart. Reload and removal are rejected while affected datasets have active work;
every successful change atomically replaces the live registry and refreshes suggested schedules.

A workspace may block plugins via the `plugins.blocked` configuration key (dataset or
provider full names). A blocked plugin does not register and is invisible to routing
and dispatch. A block is **ineffective for anything an unblocked plugin requires** —
a declared data dependency or a mounted dataset's provider — and every repair (and every
unknown entry) logs a warning. Registration, server discovery, and the task-process
dispatcher apply the same filter.

The blocklist remains persistent workspace policy. Plugin removal adds the selected plugin and its
mounted dependents to that blocklist; restoration removes the requested block and rediscovery
repairs any required dependency closure before mounting. Hot changes never delete dataset data,
configuration, or task history.

## Official plugins

The official Tushare family is the reference implementation and lives in one family
repository (`plugins/` in this workspace **only while the contracts stabilize**, then
its own repository), all contributing to the `findata_plugins` namespace package:

- `findata-plugins-providers-tushare` → `findata_plugins.tushare.plugins.providers.tushare`:
  the provider plugin (`findata-plugins/tushare`) with its configuration schema and
  readiness probe; the client and transport adapter it uses live in shared so dataset
  engines never import the provider leaf;
- `findata-plugins-shared` → `findata_plugins.tushare.shared`: the client/transport adapter,
  publication timing, mock transport, shared operation engine;
- `findata-plugins-datasets-tushare-*` → `findata_plugins.tushare.plugins.datasets.<family>.<name>`:
  dataset plugins (`findata-plugins/tushare_<name>`), each an independent
  distribution pulling exactly its own data-dependency chain;
- `findata-plugins` → metadata-only umbrella for the whole namespace.

The official family paths are classification metadata as well as import structure:

- `stock`: trade calendar, stock basic, and daily basic datasets;
- `etf`: ETF basic, ETF index, and fund daily datasets;
- `fund`: fund basic and fund factor datasets; and
- `index`: index basic, index weight, and index daily-basic datasets.

The `findata_plugins.tushare` family is the umbrella for all of the preceding provider and dataset
plugins. These paths are publisher-owned labels: another repository can use a different hierarchy
without changing plugin IDs, storage names, or core discovery.

## Invariants enforced by tests

- namespace coherence (full name matches the plugin module's top-level namespace),
  local-name uniqueness per kind, dependency and provider-reference resolution and
  acyclicity;
- blocklist semantics: unrequired blocks stick, dependency repairs mount and warn, the
  dispatcher applies the same filter;
- import boundaries: core imports no plugin and no toolkit; toolkit imports no plugin;
  dataset plugin subpackages never import each other — each may import only the SDK,
  its namespace's shared subpackage, and third-party libraries; package-dependency
  metadata covers declared data dependencies and never reverses;
- the wheel gate builds and installs every distribution and runs the mocked quick start
  through entry-point discovery in a clean environment.
