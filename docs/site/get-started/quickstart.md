# Quick start

This walkthrough starts findata with the included demo plugins and explores the server
through the Web UI — no external credentials required.

## 1. Install

```bash
pip install <path-to-findata>
```

See [Installation](installation.md) for system requirements and alternative methods.

## 2. Install the demo plugins

From a checkout of this repository, install the demo plugin family:

```bash
pip install -e ./plugins/demo/provider \
             ./plugins/demo/datasets/demo-hello \
             ./plugins/demo/datasets/demo-random
```

These plugins require no API token and work immediately with mock data.

## 3. Start the server and open the Web UI

```bash
findata-server init ~/market-data
findata-server start ~/market-data --provider-mode mock
```

`init` creates the workspace directory and an API credential. `start` runs the server
in the foreground with mock responses — no token needed.

Open your browser to **http://127.0.0.1:8765** and paste the token from
`~/market-data/token`. The Web UI shows the server status, the demo provider
(`findata-test/demo`), and two demo datasets (`demo_hello` and `demo_random`).

Leave the server running; the Web UI polls for live updates.

!!! tip
    The server also serves a [REST API](../reference/cli.md) that the CLI and Web UI
    share. The Web UI is the primary interface for exploration; the CLI is designed for
    scripting and automation.

## 4. Run a task

From the Web UI, navigate to **demo_random** and click **Complete**, or use the CLI:

```bash
findata task run findata-test/demo_random complete \
  --param tickers=AAPL \
  --param timerange=2026-07-01:2026-07-10 \
  --wait
```

The task generates deterministic random-walk price data, commits it transactionally,
and reports the result. The Web UI shows live progress.

## 5. Read the data

Data is readable whether the server is running or not:

```bash
findata data preview findata-test/demo_random \
  --keys AAPL \
  --from 2026-07-01 --to 2026-07-10 \
  --columns ticker,trade_date,close
```

or from Python:

```python
from pathlib import Path
from findata import DataLoader

table = (
    DataLoader(Path("~/market-data").expanduser())
    .dataset("findata-test/demo_random")
    .query(
        keys=["AAPL"],
        time_range=("2026-07-01", "2026-07-10"),
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
