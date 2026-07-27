# Installation

## Requirements

- Python **3.11 or newer**
- Linux or macOS
- A local **POSIX filesystem** for the workspace (findata relies on `flock` semantics;
  network filesystems are not supported)

## Install

Install the framework from a source checkout:

```bash
pip install <path-to-findata>
```

or, if you are working from this repository:

```bash
pip install -e .
```

This installs two commands:

- `findata` — the client CLI (configuration, datasets, tasks, data reads)
- `findata-server` — the local API server and Web UI

Verify the installation:

```bash
findata --version
findata-server --help
```

**The framework installs no datasets.** Plugins are separate distributions that depend on
`findata` — install what you need and it mounts automatically at the next server start.

### Demo plugins (no credentials required)

This repository includes a demo plugin family for evaluation. It needs no API token:

```bash
pip install -e ./plugins/demo/provider \
             ./plugins/demo/datasets/demo-hello \
             ./plugins/demo/datasets/demo-random
```

Other plugin families — including the [official Tushare plugins](../plugins/index.md) —
install the same way (`pip install -e ./path/to/plugin`); a
workspace can block individual plugins via the `plugins.blocked` configuration key.
See [Custom datasets and providers](../guide/custom-datasets/).

!!! tip
    Use a virtual environment when isolation from other Python packages is desired.

## Reading data from your own Python code

Clients that only need to query committed datasets install the same package and use the
`DataLoader` API. Importing it pulls in only DuckDB and Arrow — no CLI, server, or
provider modules — so it is safe to depend on in a notebook, research job, or service.
See [DataLoader](../guide/dataloader.md).

## Next step

[Quick start](quickstart.md){ .md-button .md-button--primary }
