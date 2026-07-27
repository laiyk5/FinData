# Quick start

This walkthrough configures Tushare, backfills CSI 300 daily valuation data for a sample
week, and enables recurring updates. It takes about five minutes and uses only normal user
operations.

You need a Tushare API token from [tushare.pro](https://tushare.pro) before you start.

## 1. Create and start the workspace

```bash
# Terminal 1
findata-server init ~/market-data
findata-server start ~/market-data
```

`init` creates the workspace marker and the API credential (`~/market-data/token`).
`start` runs the server in the foreground and prints a readiness report with the version,
workspace, and listening address (default `http://127.0.0.1:8765`). Leave it running;
a service manager may supervise it later.

!!! tip
    While the server runs, open that address in a browser and paste the token to use the
    Web UI. Everything below can also be done there — see
    [Workspace and Web UI](../guide/workspace.md#web-ui).

## 2. Configure the Tushare token

```bash
# Terminal 2
cd ~/market-data
# Paste the token and press Enter; it is not placed in shell history.
findata config set provider.findata-plugins/tushare.token --stdin
findata provider check findata-plugins/tushare
```

`provider check` authenticates against Tushare through the provider's rate limiter.
Provider commands never display credentials; see
[Configuration](../guide/configuration.md#secrets) for safer ways to store them
(`--stdin`, `--env`).

## 3. Backfill the index universe

```bash
findata task run findata-plugins/tushare_index_basic complete \
  --param indexes=tushare:000300.SH \
  --wait
```

This materializes the CSI 300 index reference. `tushare:000300.SH` is the exact provider
index code; the plugins use it to resolve constituents without guessing.

## 4. Backfill daily valuation data

```bash
findata task run findata-plugins/tushare_daily_basic complete \
  --param symbols=tushare:000300.SH \
  --param timerange=2026-06-29:2026-07-04 \
  --follow
```

`--follow` streams progress until the task reaches a terminal state. The backfill uses the
historical union of CSI 300 constituents over the requested range: resolution starts with
the latest weight snapshot effective at the range start and includes later snapshots
inside the range; a month without a new snapshot continues the preceding membership.

!!! note "Half-open ranges"
    Date ranges are half-open `[start, end)`: `2026-06-29:2026-07-04` covers June 29
    through July 3. `today` resolves once, in the dataset timezone, to the current date —
    which excludes today when used as the end.

Rerunning a failed backfill skips resolved historical coverage, refreshes an intersecting
current month, and resumes its remaining intervals — you never restart from scratch.

## 5. Enable recurring updates

```bash
findata config set dataset.findata-plugins/tushare_daily_basic.update_symbols \
  --value-json '["tushare:000300.SH@latest"]'
findata cron enable findata-plugins/tushare_daily_basic
```

The `update_symbols` setting belongs to the `findata-plugins/tushare_daily_basic` plugin; it parses the
constituent selector and uses it only for later parameterless `update` operations.
`@latest` is a plugin-defined suffix meaning "the current constituents" for future
updates, so recurring updates resolve the constituent month containing each latest due
trading date. Automatic maintenance is opt-in — nothing runs until you enable it.
See [Scheduling](../guide/scheduling.md).

## 6. Read the data

```bash
findata data preview findata-plugins/tushare_daily_basic \
  --keys 600000.SH \
  --from 2026-06-29 --to 2026-07-04 \
  --columns ts_code,trade_date,close,pe,pb
```

or from Python, with or without the server running:

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

Continue with [Reading data](../guide/reading-data.md) and
[DataLoader](../guide/dataloader.md).

## Using another index

For another Tushare index, obtain its exact `ts_code`, materialize it with
`findata-plugins/tushare_index_basic complete`, and use the same plugin-owned `tushare:<ts_code>` form.
This tracks only the requested reference. Metadata presence identifies the provider object
but does not guarantee index-weight permission or historical coverage.
