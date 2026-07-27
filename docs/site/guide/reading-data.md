# Reading data

After maintaining a dataset, you can inspect and export its committed revision without
starting the server and without learning the internal DuckDB layout:

```bash
findata data schema findata-test/demo_random

findata data preview findata-test/demo_random \
  --keys AAPL \
  --from 2026-07-01 --to 2026-07-10 \
  --columns ticker,trade_date,close,volume

findata data coverage findata-test/demo_random \
  --keys AAPL \
  --from 2026-07-01 --to 2026-07-10

findata data export findata-test/demo_random \
  --keys AAPL \
  --from 2026-07-01 --to 2026-07-10 \
  --columns ticker,trade_date,close,volume \
  --output-format parquet \
  --output demo-random.parquet
```

This is a read-only workflow. It creates no task, performs no provider request, changes
no dataset or configuration, and never opens an internal database for writing. A
concurrent writer may delay the read briefly; the result always observes one committed
publication.

## Commands

- **`data schema <dataset>`** reports the Arrow fields, nullability, primary key,
  partition key, time field, publication ID, and whether coverage is supported.
- **`data preview <dataset>`** prints at most 20 rows by default; `--limit` changes the
  bound. It accepts repeatable `--keys`, `--from/--to`, and comma-separated or repeatable
  `--columns`.
- **`data coverage <dataset> [--keys KEY ...]`** reports committed half-open coverage
  intervals. With `--from/--to`, it compares that requested interval with each selected
  key and reports whether it is complete, the committed interval, and every missing
  half-open interval. Both boundaries must be supplied together.
- **`data export <dataset> --output PATH|- --output-format csv|parquet|arrow|jsonl`**
  streams committed batches instead of materializing the full result. `--batch-size`
  controls the batch bound.
- **`data snapshot <dataset> [--output PATH]`** copies a consistent, WAL-free snapshot of
  the whole database to `<workspace>/snapshots/<dataset>.duckdb` (default) or `PATH`, for
  readers that cannot use DataLoader. See
  [Snapshots](dataloader.md#snapshots-for-readers-that-cannot-use-dataloader).

## Coverage enforcement

When both keys and a time range are supplied, preview and export require complete
coverage by default. `--allow-partial` explicitly opts into a partial result and reports
that policy in the export summary. `--require-coverage` may be used for clarity but
cannot be combined with `--allow-partial`. Coverage validation errors name every missing
interval and suggest the matching `dataset complete ...` command; they never trigger that
command automatically.

## Export details

CSV and JSONL may be written to `--output -` (stdout). Parquet and Arrow IPC require a
file or a binary stdout stream. A filesystem export refuses to replace an existing file
unless `--force` is supplied and publishes the completed export with an atomic rename, so
a failed export does not leave a file that looks complete. Diagnostics and a file-export
summary go to stderr; stdout contains only exported records when `--output -` is used.

## Paging

Long human-readable results shown in an interactive terminal are automatically sent
through the pager selected by `$PAGER` (normally `less`) instead of flooding the
terminal. Paging is never used for redirected output, `--format json`, `--format jsonl`,
or export data written to stdout. Set `PAGER=cat` when interactive paging is not wanted.
