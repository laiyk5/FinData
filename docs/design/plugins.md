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
   need. (v1 ships the Tushare family as built-in entry points inside the findata wheel;
   splitting it into a separate distribution is the intended end state, not a new
   mechanism.)
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

## v1 status and known couplings

The contracts and discovery path are in place. The following v1 remnants still couple
core to the built-in Tushare family and are tracked for removal; each item names its
target shape:

- the server constructs the task worker from one hardcoded provider instead of
  dispatching per execution dataset (`server.py`); target: worker resolution by the
  execution's dataset at dispatch time;
- update readiness for three built-in datasets is a name switch in the server
  (`server.py`); target: readiness reported by the plugin through its contract;
- the provider readiness probe is invoked through a provider-specific helper
  (`server.py`); target: generic dispatch to the named provider's runtime;
- default cron schedules for built-in datasets are a core table (`cron.py`); target:
  suggested schedules declared by each dataset plugin;
- legacy workspace configuration migration names two built-in datasets (`storage.py`);
  tolerated as time-bounded migration code and removed with the legacy format.

Likewise, the five Tushare datasets currently share one plugin package; per-dataset
packaging is the direction as the family grows, and it requires no mechanism change.

## Authoring

The typed contracts, entry-point spelling, and a worked example live in the
[custom-datasets guide](../site/guide/custom-datasets.md). The built-in Tushare provider
and dataset plugins are the reference implementation of every rule on this page.
