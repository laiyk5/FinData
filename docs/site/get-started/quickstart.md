# Quick start

This walkthrough starts findata and explores the server through the Web UI — no
external credentials required.

## 1. Install

```bash
pip install <path-to-findata>
```

See [Installation](installation.md) for system requirements and alternative methods.

## 2. Start the server and open the Web UI

```bash
findata-server init ~/market-data
findata-server start ~/market-data --provider-mode mock
```

`init` creates the workspace directory and an API credential. `start` runs the server
in the foreground with mock responses so you can explore without any API token.

Open your browser to `http://127.0.0.1:8765` and paste the token from
`~/market-data/token`. The Web UI shows the server status, registered plugins, and
available datasets — everything is empty at first because no plugins are installed yet.

Leave the server running; the Web UI polls for live updates.

!!! tip
    The server also serves a [REST API](../reference/cli.md) that the CLI and Web UI
    share. The Web UI is the primary interface for exploration; the CLI is designed for
    scripting and automation.

## 3. Install plugins

Datasets and data sources are added by installing plugin distributions. findata ships
with no datasets by default — plugins bring their own.

From a checkout of this repository, install the official Tushare family:

```bash
pip install -e ./plugins/tushare/provider \
             ./plugins/tushare/trade-cal \
             ./plugins/tushare/stock-basic \
             ./plugins/tushare/index-basic \
             ./plugins/tushare/index-weight \
             ./plugins/tushare/daily-basic
```

Stop the server (`Ctrl-C`) and restart it:

```bash
findata-server start ~/market-data --provider-mode mock
```

Refresh the Web UI — the **Datasets** and **Providers** pages now list the installed
plugins.

!!! tip
    Plugins don't have to come from a financial API. Run
    ``findata plugin scaffold mycompany hello`` to generate your own — see
    [Custom datasets](../guide/custom-datasets.md).

## 4. Run a task

From the Web UI, navigate to a dataset and click **Complete** to backfill data, or use
the CLI:

```bash
findata task run findata-plugins/tushare_daily_basic complete \
  --param symbols=tushare:000300.SH \
  --param timerange=2026-06-29:2026-07-04 \
  --wait
```

With `--provider-mode mock`, the server returns deterministic fake data — no API token
needed. Tasks run in a child process, publish data transactionally, and report their
result. The Web UI shows live progress.

## 5. Read the data

Data is readable whether the server is running or not:

```bash
findata data preview findata-plugins/tushare_daily_basic \
  --keys 600000.SH \
  --from 2026-06-29 --to 2026-07-04 \
  --columns ts_code,trade_date,close
```

or from Python:

```python
from pathlib import Path
from findata import DataLoader

table = (
    DataLoader(Path("~/market-data").expanduser())
    .dataset("findata-plugins/tushare_daily_basic")
    .query(
        keys=["600000.SH"],
        time_range=("2026-06-29", "2026-07-04"),
    )
)
```

DataLoader is a standalone reader that opens the committed DuckDB databases directly
through a cross-process read-write gate — no server round-trip needed.

## Next steps

- [Server](../guide/workspace.md) — workspace management, configuration, scheduling
- [Data](../guide/providers-and-datasets.md) — dataset operations, tasks, reading data
- [Custom datasets](../guide/custom-datasets.md) — write your own plugin from scratch
- [Official plugins](../plugins/index.md) — the Tushare plugin family reference
