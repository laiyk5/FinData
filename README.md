# findata

**Local, plugin-oriented financial dataset maintenance and querying.**

findata keeps financial datasets on your own machine as transactional DuckDB files,
maintains them on a schedule through provider plugins, and lets you query the committed
data safely from Python or the command line — while the server is running.

- **Plugin-oriented**: datasets and providers are ordinary Python packages discovered
  through entry points; the framework installs no datasets by itself.
- **Transactional**: every update is one atomic commit with declared coverage; failed
  runs resume exactly where the data leaves off.
- **Safe concurrent reads**: the `DataLoader` API and `findata data` commands query
  committed data concurrently with writers, coordinated by a per-dataset gate.

## Install

```bash
pip install findata                     # the framework only
pip install findata-plugins-tushare     # the official Tushare plugin collection
```

## Quick start

```bash
findata-server init ~/market-data
findata-server start ~/market-data

findata task run findata/tushare/daily_basic complete \
  --param symbols=tushare:000300.SH \
  --param timerange=2026-06-29:2026-07-04 \
  --follow
```

```python
from findata import DataLoader

table = (
    DataLoader("~/market-data")
    .dataset("findata/tushare/daily_basic")
    .query(keys=["600000.SH"], time_range=("2026-06-29", "2026-07-04"))
)
```

## Documentation

Full documentation (installation, quick start, CLI reference, DataLoader, writing your
own plugins) lives at **<https://laiyk5.github.io/FinData/>**; sources are in
[`docs/site/`](docs/site/). Architecture and contributor guides are in
[`docs/design/`](docs/design/) and [`docs/DEV.md`](docs/DEV.md).

## License

[Apache-2.0](LICENSE)
