# Toolkit components

This file is the canonical catalog of reusable, opt-in components used by dataset plugins. [core.md](../core.md) owns the architectural boundaries; [DEV.md](../../DEV.md) explains how to add or promote a component; [dataset/index.md](../dataset/index.md) records which components each dataset uses.

A mechanism is promoted into the toolkit only when a second dataset needs it. Before that, it remains private to its first dataset plugin.

Every component is documented as:

- purpose;
- requirements and capabilities;
- provided interface;
- invariants and failure behavior;
- example.

Toolkit components are plugin-side helpers. DuckDB storage, transactions, SQL generation, and
DataLoader belong to core and are not toolkit components.

Toolkit implementations live under the `findata.toolkit` package. Core modules never import that
package. Dataset plugins opt into individual toolkit components and remain responsible for adapting
their public operands and settings to dataset-neutral toolkit inputs. Toolkit code may depend on
stable public core contracts, but never on a concrete dataset or provider implementation.

## Components

- [Checkpoint-batch planner](checkpoint_batch_planner.md)
- [Coverage tracker](coverage_tracker.md)
- [Publication-window pruning](publication_window_pruning.md)
- [Request optimizer](request_optimizer.md)
- [Constituent-set resolver](constituent_set_resolver.md)
- [Mock API](mock_api.md)
- [Task logging bridge](task_logging_bridge.md)
