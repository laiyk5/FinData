# Quick start

This walkthrough starts findata, explores a running server, runs a dataset task, and reads
the committed data — no external credentials required.

## 1. Install

```bash
pip install findata
```

See [Installation](installation.md) for system requirements and alternative methods.

## 2. Create and start the workspace

```bash
# Terminal 1
findata-server init ~/market-data
findata-server start ~/market-data
```

`init` creates the workspace directory and an API credential. `start` runs the server
in the foreground and prints a readiness report with the version, workspace, and
listening address (default `http://127.0.0.1:8765`). Leave it running.

!!! tip
    While the server runs, open that address in a browser and paste the token from
    `~/market-data/token` to use the Web UI.

## 3. Explore the server

In another terminal:

```bash
# Terminal 2
cd ~/market-data
findata provider ls
findata dataset ls
```

These commands show the plugins currently installed and registered. A fresh `pip install
findata` includes no datasets by default — the lists may be sparse until you install
plugins.

```bash
# Check the server status
findata dataset status --all
```

## 4. Install plugins

Datasets are added by installing plugin distributions. The
[Official plugins](../plugins/index.md) page covers the Tushare family —
the primary data source for Chinese A-share markets:

```bash
pip install findata-plugins
```

Stop the server (`Ctrl-C`) and restart it so the new entry points are discovered:

```bash
findata-server start ~/market-data
```

Now `findata dataset ls` shows the installed datasets, and
`findata provider ls` shows the Tushare provider.

!!! tip
    Datasets don't have to come from a financial API. See
    [Custom datasets](../guide/custom-datasets.md) to build a plugin that generates or
    ingests data on your own terms.

## 5. Configure and run

If you have a [Tushare API token](https://tushare.pro), configure it and run a
backfill:

```bash
findata config set provider.findata-plugins/tushare.token --stdin
findata task run findata-plugins/tushare_daily_basic complete \
  --param symbols=tushare:000300.SH \
  --param timerange=2026-06-29:2026-07-04 \
  --wait
```

Tasks run in a child process, publish data transactionally, and report their result.
`--wait` blocks until the task reaches a terminal state.

Without a token, the server's `--provider-mode mock` flag enables deterministic mock
responses for evaluation:

```bash
findata-server start ~/market-data --provider-mode mock
```

## 6. Read the data

Data is readable whether the server is running or not:

```bash
findata data preview findata-plugins/tushare_daily_basic \
  --keys 600000.SH \
  --from 2026-06-29 --to 2026-07-04 \
  --columns ts_code,trade_date,close,pe,pb
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
        require_coverage=True,
    )
)
```

DataLoader is a standalone reader that opens the committed DuckDB databases directly
through a cross-process read-write gate — no server round-trip needed.

## Next steps

- [Data](../guide/providers-and-datasets.md) — dataset operations, tasks, reading data
- [Custom datasets](../guide/custom-datasets.md) — write your own plugin from scratch
- [Official plugins](../plugins/index.md) — the Tushare plugin family reference
