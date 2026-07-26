# findata

**Local, plugin-oriented financial dataset maintenance and querying.**

findata keeps financial datasets on your own machine as transactional DuckDB files,
maintains them on a schedule through provider plugins, and lets you query the committed
data safely from Python or the command line — while the server is running.

## What it does

- **Maintains datasets locally.** Built-in Tushare plugins backfill and refresh trade
  calendars, stock and index metadata, index constituents, and daily valuation metrics
  into one DuckDB file per dataset.
- **Commits transactionally.** Every update is one atomic commit with declared coverage;
  a failed run resumes exactly where the data leaves off, never halfway.
- **Runs on your schedule.** Opt-in cron jobs keep datasets current in exchange timezones.
- **Reads safely.** The `DataLoader` API and the `findata data` commands query committed
  data concurrently with writers, coordinated by a per-dataset gate — no server round-trip
  required.

## Where to go next

<div class="grid cards" markdown>

- :rocket: **[Installation](get-started/installation.md)** — requirements and install
- :zap: **[Quick start](get-started/quickstart.md)** — configure Tushare and backfill your
  first dataset in five minutes
- :book: **[Guide](guide/workspace.md)** — workspaces, datasets, tasks, scheduling, reading data
- :computer: **[CLI reference](reference/cli.md)** — every command, output format, and exit code

</div>

## A taste

```bash
findata-server init ~/market-data
findata-server start ~/market-data

findata task run tushare_daily_basic complete \
  --param symbols=tushare:000300.SH \
  --param timerange=2026-06-29:2026-07-04 \
  --follow
```

```python
from pathlib import Path

from findata import DataLoader

table = (
    DataLoader(Path("~/market-data").expanduser())
    .dataset("tushare_daily_basic")
    .query(keys=["600000.SH"], time_range=("2026-06-29", "2026-07-04"))
)
```
