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
   only. Plugins are ordinary packages that depend on `findata`, installed singly, as a
   family umbrella, or as dependency chains resolved by the package manager.
5. **Third-party plugins mount identically.** Same entry points, same contracts, same
   naming rules, no privileged path.

## Naming: the author namespace

A plugin's full name is `<author>/<free/path/...>`. The first component is the
publisher namespace; everything below it is author-chosen classification (any depth)
that core treats as an opaque path, validating only component shape
(`[a-z0-9_-]+`, no `.`/`..`). Official plugins are named like
`findata/tushare/daily_basic`: `findata` is the publisher, `tushare/daily_basic` is the
publisher's own classification. Each dataset plugin registers exactly one dataset, so
the plugin's full name is also the dataset's registered name wherever a dataset is
addressed (storage, configuration, routing, DataLoader).

An author can keep their own namespace consistent but cannot control other publishers,
so the author level is the only level the framework polices:

- one distribution may register datasets under exactly one author namespace
  (entry points are traced to their distribution at discovery);
- full names must be unique across the environment (duplicate names fail validation).

The author namespace is a **convention, not a verified identity** — the framework cannot
know that `laiyk5` belongs to you, because plugins may arrive from PyPI, GitHub, another
host, or a colleague. Choose an author name you control somewhere public (your PyPI or
GitHub name, or a company domain) so casual collisions stay unlikely; if two installed
distributions still produce the same full dataset name, registration fails loudly with a
duplicate-name error and the user decides which package to keep.

Provider plugin IDs stay flat (`tushare`), uniqueness-validated; providers are adapters,
not data assets, and their IDs appear in configuration keys.

Names are structural everywhere they flow: storage nests by name
(`datasets/findata/tushare/daily_basic/`, snapshots mirror it), configuration keys are
`dataset.<full-name>.<setting>` resolved by registry longest-match, HTTP routes match
registered names greedily instead of counting path segments, and `_findata_metadata`
stores the full name.

## Dependency model: three relations, kept apart

| relation | expressed as | allowed directions | forbidden |
| --- | --- | --- | --- |
| **data dependency** (dataset → dataset) | `DatasetPlugin.dependencies` name strings | any dataset → any dataset, acyclic, cross-author allowed | imports or code calls |
| **package dependency** (install-time) | distribution `dependencies` metadata | upward (dataset package → family provider package → `findata`); between dataset packages only when mirroring a declared data dependency | cross-author hard requirements; unrelated dataset-package edges |
| **code sharing** | the three tiers below | the most general honestly-true tier | copy-paste across plugins; dataset-package imports |

Data dependencies are name strings, resolved at validation with author-relative
shorthand (`"tushare/trade_cal"` inside a `findata/*` plugin resolves to
`findata/tushare/trade_cal`). They flow at runtime through exactly two channels: the
reporter's `fulfill(dataset, requirement)` (the framework executes the dependency
through the parent's `resolve_dependency`) and DataLoader reads of a committed revision
(settings normalization, planning). The dependent plugin never knows the provider
plugin's package name.

Same-author data dependencies that a plugin's `update` requires are mirrored as hard
package dependencies, so installing one dataset plugin pulls the packages that provide
its dependency data (`findata-dataset-tushare-daily-basic` pulls trade-cal, index-basic,
and index-weight). This is metadata only: the package manager keeps installations
complete while imports remain forbidden. Cross-author data dependencies are documented
requirements, not hard package dependencies; a missing one fails validation with a clear
error.

## Code sharing: three tiers by generality

When plugins genuinely share code, it lives at the most general tier it honestly fits:

1. **findata SDK** (in `findata`): framework-generic code — `findata.contracts`,
   `findata.plugins` (contract layer) and `findata.toolkit` (dataset-neutral helpers,
   promoted on second use, catalogued, boundary-tested). The only layer plugin authors
   may treat as stable long-term.
2. **Family shared package**: code specific to one provider family (the Tushare client
   and selector syntax, publication timing, the mock transport, the shared operation
   engine). It lives in the family's provider package; family dataset packages use it
   through an ordinary package dependency. It imports the SDK; the framework knows
   nothing about it.
3. **findata-unrelated third-party library**: domain-generic code with no findata
   imports, versioned independently. (No v1 candidate; the rule is recorded ahead of
   need.)

## Contracts: two runtimes

Behavior is dispatched through two protocols in `findata.plugins`:

- `ProviderRuntime` — provider scope only: `ready`, `is_mock`, `probe`. Configuration,
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

Distribution names follow the prefix convention `findata-provider-*` /
`findata-dataset-*` (family umbrellas are `findata-plugins-*` and carry no entry
points); discovery validates it. Installed plugins **mount automatically**: discovery →
prefix validation → author/dependency validation → storage registration, with no
configuration required, and unmount on uninstall.

A workspace may block plugins via the `plugins.blocked` configuration key (dataset full
names or provider IDs). A blocked plugin does not register and is invisible to routing
and dispatch. A block is **ineffective for anything an unblocked plugin requires** —
a declared data dependency or a mounted dataset's provider — and every repair (and every
unknown entry) logs a warning. Registration, server discovery, and the task-process
dispatcher apply the same filter.

## Official plugins

The Tushare family is the reference implementation: `findata-provider-tushare` (client,
transport, mock, publication timing, shared operation engine) plus five
`findata-dataset-tushare-*` packages and the `findata-plugins-tushare` umbrella. They
live in this repository under `plugins/` as uv workspace members **only while the
contracts stabilize**; they graduate to their own repository afterwards, with no
mechanism change.

## Invariants enforced by tests

- author rule (one author per distribution), name shape and uniqueness, dependency
  resolution and acyclicity;
- prefix convention for plugin distributions;
- blocklist semantics: unrequired blocks stick, dependency repairs mount and warn, the
  dispatcher applies the same filter;
- import boundaries: core imports no plugin and no toolkit; toolkit imports no plugin;
  dataset packages import only the SDK, their family provider package, and third-party
  libraries; package-dependency metadata covers same-author data dependencies and never
  reverses;
- the wheel gate builds and installs every distribution and runs the mocked quick start
  through entry-point discovery in a clean environment.
