# findata

**Local, plugin-oriented financial dataset maintenance and querying.**

findata keeps financial datasets on your own machine as transactional DuckDB files,
maintains them on a schedule through provider plugins, and lets you query the committed
data safely from Python or the command line — while the server is running.

## What it does

- **Plugin-oriented.** Datasets and providers are ordinary Python packages discovered
  through entry points; the framework installs no datasets by itself. The
  [demo plugins](plugins/demo.md) let you evaluate the system without any API token.
  The [official plugins](plugins/index.md) include the Tushare family for real
  Chinese A-share market data. Build your own with the
  [Custom datasets](guide/custom-datasets.md) guide.
- **Transactional.** Every update is one atomic commit with declared coverage; a failed
  run resumes exactly where the data leaves off, never halfway.
- **Runs on your schedule.** Opt-in cron jobs keep datasets current in exchange timezones.
- **Reads safely.** The `DataLoader` API and the `findata data` commands query committed
  data concurrently with writers, coordinated by a per-dataset gate — no server round-trip
  required.

## Where to go next

<div class="grid cards" markdown>

- :rocket: **[Installation](get-started/installation.md)** — requirements and install
- :zap: **[Quick start](get-started/quickstart.md)** — start the server and explore
  through the Web UI with the demo plugins
- :gear: **[Server](guide/workspace.md)** — workspaces, configuration, scheduling
- :package: **[Data](guide/providers-and-datasets.md)** — datasets, tasks, reading data, DataLoader API
- :wrench: **[Custom datasets](guide/custom-datasets.md)** — bring your own data with a plugin
- :computer: **[CLI reference](reference/cli.md)** — every command, output format, and exit code

</div>

## A taste

```bash
pip install -e ./plugins/demo/provider ./plugins/demo/datasets/demo-random
findata-server init ~/market-data
findata-server start ~/market-data --provider-mode mock
findata task run findata-test/demo_random complete \
  --param tickers=AAPL \
  --param timerange=2026-07-01:2026-07-10 \
  --wait
```

```python
from pathlib import Path
from findata import DataLoader

table = (
    DataLoader(Path("~/market-data").expanduser())
    .dataset("findata-test/demo_random")
    .query(keys=["AAPL"], time_range=("2026-07-01", "2026-07-10"))
)
```

The DataLoader API reads committed data directly from DuckDB — no server round-trip
required.
