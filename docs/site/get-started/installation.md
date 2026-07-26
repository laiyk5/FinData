# Installation

## Requirements

- Python **3.11 or newer**
- Linux or macOS
- A local **POSIX filesystem** for the workspace (findata relies on `flock` semantics;
  network filesystems are not supported)

## Install

From a source checkout:

```bash
python -m pip install .
```

This installs two commands:

- `findata` — the client CLI (configuration, datasets, tasks, data reads)
- `findata-server` — the local API server that performs maintenance work

It also installs `findata-plugins-tushare`, the official Tushare provider and dataset
plugins, as a default dependency — the quick start works out of the box. The plugins are
ordinary separate distributions: `pip uninstall findata-plugins-tushare` gives you a
lean core,
and third-party plugins install the same way (`pip install <their-package>`; see
[Custom datasets and providers](../guide/custom-datasets.md)).

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
