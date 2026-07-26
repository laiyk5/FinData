# Installation

## Requirements

- Python **3.11 or newer**
- Linux or macOS
- A local **POSIX filesystem** for the workspace (findata relies on `flock` semantics;
  network filesystems are not supported)

## Install

From a source checkout:

```bash
python -m pip install . ./plugins/tushare/umbrella
```

This installs the framework and the official Tushare plugin collection, and two
commands:

- `findata` — the client CLI (configuration, datasets, tasks, data reads)
- `findata-server` — the local API server that performs maintenance work

**The framework installs no datasets.** Plugins are ordinary separate distributions that
depend on `findata` — install what you need and it mounts automatically at the next
server start:

```bash
# the whole official Tushare family via its umbrella package
python -m pip install findata-plugins-tushare

# or just one dataset (its dependencies resolve automatically)
python -m pip install findata-dataset-tushare-daily-basic
```

Third-party plugins install the same way (`pip install <their-package>`); a workspace
can block individual plugins via the `plugins.blocked` configuration key. See
[Custom datasets and providers](../guide/custom-datasets.md).

!!! tip
    Use a virtual environment when isolation from other Python packages is desired.

Verify the installation:

```bash
findata --version
findata-server --help
```

## Reading data from your own Python code

Clients that only need to query committed datasets install the same package and use the
`DataLoader` API. Importing it pulls in only DuckDB and Arrow — no CLI, server, or
provider modules — so it is safe to depend on in a notebook, research job, or service.
See [DataLoader](../guide/dataloader.md).

## Next step

[Quick start](quickstart.md){ .md-button .md-button--primary }
