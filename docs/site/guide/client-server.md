# Client-server architecture

findata has a client-server model with a standalone read path.

## The server

The server (`findata-server`) owns all write operations and state management:

- **Task execution** — runs dataset operations in child processes
- **Configuration** — stores and serves workspace config keys
- **Cron scheduling** — triggers recurring updates
- **Event log** — records operational events
- **HTTP API** — serves the CLI and Web UI on `http://127.0.0.1:8765`

One server controls one workspace at a time. It acquires a file lock on the workspace
directory so a second server cannot accidentally share the same data.

## The clients

### CLI

The `findata` command is a thin HTTP client. Every command sends a request to the
server's API and renders the response. The CLI never opens DuckDB, never runs a task
directly, and never modifies workspace state without going through the server.

```bash
findata --workspace ~/market-data dataset ls     # GET /v1/datasets
findata --workspace ~/market-data task run ...    # POST /v1/tasks
```

### Web UI

The Web UI is another thin client served by the same server at the listening address.
It uses the same HTTP API as the CLI and covers the same surface — datasets, tasks,
providers, cron, events, configuration. It is the primary interface for exploration;
the CLI is designed for scripting and automation.

### Direct reads (DataLoader)

Reading committed data does **not** require the server. `DataLoader` opens each
dataset's DuckDB database directly through a cross-process read-write gate:

```python
from findata import DataLoader

table = DataLoader("~/market-data").dataset("findata-test/demo_random").query()
```

This is safe because writers and readers coordinate through the gate lock — a reader
waits briefly if a writer holds the lock, and a writer waits for all readers to finish
before committing. Different datasets are independent.

Importing `DataLoader` pulls in only DuckDB and Arrow — no CLI, server, or provider
modules — so it is safe to depend on in notebooks, research jobs, or services.

## How they fit together

```
┌──────────┐     HTTP      ┌────────────┐     fork      ┌──────────┐
│  CLI     │ ──────────▶   │  Server    │ ───────────▶  │  Task    │
│  Web UI  │ ◀──────────   │  (write)   │               │  (child) │
└──────────┘               └─────┬──────┘               └──────────┘
                                 │
                          ┌──────┴──────┐
                          │  Workspace  │
                          │  (gate)     │
                          └──────┬──────┘
                                 │
                    ┌────────────┴────────────┐
                    │  Dataset .duckdb files  │
                    └────────────▲────────────┘
                                 │
                          ┌──────┴──────┐
                          │  DataLoader │  (direct read, no server)
                          └─────────────┘
```

The workspace is the shared coordination point: the server writes through the gate,
DataLoader reads through the gate, and the gate ensures they never conflict.
