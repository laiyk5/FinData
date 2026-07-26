# User documentation

findata's user documentation is published as a website:

**<https://laiyk5.github.io/FinData/>**

The sources live in [`docs/site/`](site/) and are built with MkDocs Material
(`nox -s docs`). The former contents of this file moved as follows:

| former section | current page |
| --- | --- |
| Installation | [get-started/installation.md](site/get-started/installation.md) |
| Quick start | [get-started/quickstart.md](site/get-started/quickstart.md) |
| Workspace selection, Web UI | [guide/workspace.md](site/guide/workspace.md) |
| CLI behavior, Command reference | [reference/cli.md](site/reference/cli.md) |
| Tasks, task lifecycle | [guide/tasks-and-events.md](site/guide/tasks-and-events.md) |
| Datasets, providers | [guide/providers-and-datasets.md](site/guide/providers-and-datasets.md) |
| Cron | [guide/scheduling.md](site/guide/scheduling.md) |
| Configuration | [guide/configuration.md](site/guide/configuration.md) |
| Discovering, previewing, and exporting committed data | [guide/reading-data.md](site/guide/reading-data.md) |
| DataLoader, snapshots | [guide/dataloader.md](site/guide/dataloader.md) |

Architecture and invariants live in [design/core.md](design/core.md); individual dataset
contracts live in [design/dataset/index.md](design/dataset/index.md). User-documentation
principles (single canonical statement, executed quick starts, no secrets or internal
paths in examples) are owned by [DEV.md](DEV.md).
