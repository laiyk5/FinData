# Plugin architecture

This file owns the goals and decoupling invariants of the plugin-based provider/dataset
design. [core.md](core.md) owns the runtime architecture those plugins plug into;
[DEV.md](../DEV.md) owns the contributor workflow for adding one; the author-facing
walkthrough lives in the [custom-datasets guide](../site/guide/custom-datasets.md).

## Design goals

The plugin architecture exists so that:

1. **Plugins are decoupled from each other.** One dataset plugin never imports, links
   against, or names another plugin's code. A dataset that needs another dataset's data
   reads a committed revision of a declared dependency through the public DataLoader.
2. **Plugins are decoupled from core.** Core knows plugins only through entry-point
   discovery and the typed contracts (`ProviderPlugin`, `DatasetPlugin`,
   `ProviderRuntime`, `OperationRequest`, `OperationReporter`). No core module imports a
   concrete plugin package or branches on a specific provider or dataset name.
3. **Adding a plugin never changes core code.** A new provider or dataset ships as its
   own Python distribution; installing it is the only step required for the next server
   start to discover, validate, register, and serve it.
4. **Official plugins are mountable on demand.** The plugins findata ships are ordinary
   plugins through the same mechanism; users install only the provider families they
   need. The Tushare family ships as the separate `findata-tushare` distribution, which
   findata depends on by default so a plain install works out of the box; uninstalling
   it yields a lean core.
5. **Third-party plugins mount identically.** An external author uses the same entry
   points and the same contracts as the official plugins, with no source changes to
   findata and no privileged registration path.

## Invariants

These rules are what make the goals true; tests and review enforce them.

- **Core never names a plugin.** Core dispatch resolves behavior per dataset through the
  plugin contracts. A `switch` on a provider or dataset name in `findata` core modules
  (server, cron, task runner, CLI, storage) is an architecture violation, not a style
  issue. The only tolerated exception is time-bounded migration code for legacy
  workspace formats.
- **Discovery is the only registration path.** Core loads provider contracts from the
  `findata.providers` entry-point group, validates them, then loads dataset contracts
  from `findata.datasets` and validates provider references, dependencies, and cycles.
  Core never imports the module behind an entry point directly.
- **Plugins never import each other.** A dataset plugin may import public core
  contracts, its own provider adapter, and selected toolkit components — never another
  dataset plugin or another provider. Cross-dataset data flows through declared
  dependencies and DataLoader reads of committed revisions.
- **Shared behavior is promoted, not copied.** Behavior needed by a second dataset moves
  into `findata.toolkit` as a dataset-neutral component; provider-specific values stay
  as parameters inside the owning plugin.
- **Plugin policy lives in the plugin.** Operation semantics, settings schemas and
  normalization, update readiness, publication timing, and suggested schedules are
  declared by the plugin through its contract. Core transports and executes them but
  never encodes them.

## Lifecycle

1. **Discover** — the server reads both entry-point groups at startup.
2. **Validate** — duplicate IDs, malformed contracts, unknown provider references,
   missing `update`, incomplete runtimes, and dependency cycles are rejected before any
   state changes.
3. **Register** — each dataset plugin's storage is created or validated against its
   declared spec in the workspace.
4. **Dispatch** — every dataset-scoped call (normalize, plan, describe, execute) resolves
   through the plugin's provider runtime; every provider-scoped call (readiness, probe)
   resolves through the named provider. Neither path may assume a particular plugin.
5. **Execute** — the worker runs in a task subprocess against the typed
   `OperationRequest`/`OperationReporter` contracts and commits through the core
   transactional writer.

## v1 status

The goals above are implemented and enforced:

- **Dispatch is fully generic.** The TaskRunner worker is a `PluginWorkerDispatcher`
  that resolves the executing dataset's provider runtime inside the task child process,
  so a plugin installed after server start is picked up on the next dispatch. Update
  readiness is reported by each plugin through `ProviderRuntime.update_ready`, provider
  checks dispatch generically to the named provider's runtime, and suggested cron
  schedules are declared by each `DatasetPlugin` and injected into the cron manager.
  No core module branches on a provider or dataset name; the only tolerated exception
  is the time-bounded legacy configuration migration in `storage.py`, removed with the
  legacy format.
- **Boundaries are test-enforced.** Core modules may not import a plugin distribution
  or the toolkit; toolkit components may not import a plugin; plugin distributions may
  not import another plugin or retired core plugin paths.
- **Official plugins are a separate distribution.** The Tushare provider and datasets,
  including their mock transport, live under `plugins/tushare/` as the
  `findata-tushare` uv workspace member — the reference implementation of every rule
  on this page. The five Tushare datasets remain one plugin package per provider
  family; splitting a family further requires no mechanism change.

The typed contracts, entry-point spelling, and a worked example live in the
[custom-datasets guide](../site/guide/custom-datasets.md).
