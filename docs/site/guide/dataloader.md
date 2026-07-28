# DataLoader

DataLoader is the **only supported read protocol for external processes**. It reads each
dataset's DuckDB database directly and does not require the server process. It
coordinates with writers through the dataset gate, so a query may briefly wait for a
transaction on the same dataset; different datasets remain independent. Importing it
pulls in only DuckDB and Arrow — no CLI, server, or provider modules — so a client that
installs `findata` can use it without any server-side setup.

!!! warning "Never open dataset files directly"
    External processes MUST NOT open a dataset's `dataset.duckdb` directly with
    `duckdb.connect` — not even `read_only=True`. A direct connection bypasses the
    dataset gate, which corrupts read/write ordering against concurrent writers, and it
    crashes the next server startup: opening each database in
    `Workspace.recover_storage` then fails with a conflicting-lock error. DataLoader
    itself never retries such a conflicting-lock failure, because that error is the
    detector for the protocol violation.

## Querying

```python
from pathlib import Path

from findata import DataLoader

loader = DataLoader(Path("~/market-data").expanduser())
dataset = loader.dataset("findata-test/demo_random")

table = dataset.query(
    keys=["AAPL", "GOOGL"],
    time_range=("2026-07-01", "2026-07-10"),
    columns=["ticker", "trade_date", "close", "daily_return"],
    filters=[("close", ">", 100)],
    order_by=["trade_date", "ticker"],
    require_coverage=True,
)
```

For interactive exploration, `query_sql` runs one guarded read-only `SELECT`
against the dataset's public `data` relation through the same DataLoader lock and
ready-state checks:

```python
table = dataset.query_sql(
    "SELECT ticker, trade_date, close FROM data "
    "WHERE close > 100 ORDER BY trade_date DESC",
    limit=100,
)
```

The SQL surface permits one query over `data`; joins, subqueries, external files,
and multiple statements are rejected.

The WebUI's dataset **Data** tab uses this same guarded server-side query path. It shows a bounded
preview and exports only the current query result as CSV or Parquet; it does not download an
implicit whole-dataset copy.

`query` returns a `pyarrow.Table`. `keys` addresses the dataset's declared partition key,
and `time_range` is half-open `[start, end)` over its declared time field.

Filters are `(column, operator, value)` tuples combined with AND. Supported operators are
`=`, `!=`, `<`, `<=`, `>`, `>=`, `in`, and `not in`. Ordering accepts column names or
`(column, "asc"|"desc")` pairs.

## Streaming

For low-memory reads:

```python
with dataset.iter_batches(batch_size=65536, keys=["AAPL"]) as batches:
    for batch in batches:
        ...  # pyarrow.RecordBatch
```

The iterator holds its shared gate, read-only connection, and committed database view
until the context manager closes, so one batch reader observes exactly one committed
publication — and a writer waits for it to finish.

## Metadata and coverage

- `dataset.describe()` returns storage-neutral schema and key metadata for one committed
  revision (fields, nullability, primary/partition/time keys, publication ID, coverage
  support).
- `dataset.publication_id` names the current committed publication.
- `dataset.coverage(keys=None)` returns the coverage table when available.

## Errors

- An uninitialized dataset raises `DatasetNotReadyError`.
- An unsupported storage-adapter, DuckDB-storage, or data-layout version raises
  `IncompatibleDatasetError` without modifying the workspace.
- With `require_coverage=True`, a coverage-tracked dataset verifies explicit `keys` and
  `time_range` and raises `CoverageError(dataset, missing_intervals)` when due
  observations are unresolved. Non-observation dates such as a daily dataset's closed
  market days do not appear as gaps. Best-effort and non-coverage-tracked datasets do not
  support this option.

All errors derive from `findata.loader.DataLoaderError`.

## Snapshots for readers that cannot use DataLoader

Readers that cannot use DataLoader — non-Python, third-party, or offline consumers —
receive a snapshot copy instead of touching live files:

```bash
findata data snapshot findata-test/demo_random            # writes <workspace>/snapshots/findata-test/demo_random.duckdb
findata data snapshot findata-test/demo_random --output /path/to/copy.duckdb
```

The snapshot is checkpointed and copied while holding the dataset's exclusive gate, so it
is a WAL-free single file containing exactly one committed state, safe to open directly
with any DuckDB client. An existing snapshot at the destination is replaced atomically.
