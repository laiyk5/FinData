# Workspace and Web UI

A **workspace** is one directory that owns everything findata manages: dataset storage,
configuration, task history, events, schedules, and the API credential.

## Server lifecycle

```bash
findata-server init <workspace>    # create the workspace marker and API credential
findata-server start <workspace>   # run in the foreground; a service manager may supervise it
findata-server token <workspace>   # print the API token (also the <workspace>/token file)
```

`start` prints a concise readiness report — version, resolved workspace, listening
address, and a credential-free provider summary — only after startup recovery has
succeeded. Redirected or service-managed output uses one plain log record rather than an
interactive banner. Server output never reveals API credentials or provider secrets.

## How the client finds the workspace

The `findata` client resolves its workspace in this order:

1. global `--workspace <path>`;
2. the `FINDATA_WORKSPACE` environment variable;
3. the nearest directory, starting at the current directory and walking through its
   parents, that contains a workspace marker.

If no workspace is found, the client exits with an error suggesting
`findata-server init <path>`.

## Internal files are implementation state

Each registered dataset owns one internal DuckDB file under `<workspace>/datasets/`.
These files are implementation state: query them through [DataLoader](dataloader.md) and
never open them directly, remove WAL files, or copy a live database as a backup. findata
retains only the current committed dataset revision; routine updates do not create
historical storage copies. Dataset initialization is local and does not contact a
provider.

## Web UI

While `findata-server start` is running, the server serves a browser UI at its listening
address (default `http://127.0.0.1:8765/`). Open it in a browser and paste the workspace
token when prompted. The token is held only in the browser session and is sent as an
`Authorization` header, never in a URL.

The Web UI is a thin client over the same HTTP API as the CLI and covers the same
operational surface:

- a home dashboard (attention queue, live work, dataset health);
- datasets (describe, status, schema-driven operation forms with dry-run and submit,
  typed settings editors, confirmed reset);
- tasks (list, live status and logs, cancel, retry, explain);
- providers (detail pages with configuration state, related datasets, and an
  authenticated check);
- cron (guided schedule editing, enable, disable, reset);
- events (filtering, contextual actions, acknowledgement);
- configuration (grouped, filterable, fully generated from declared keys — secrets can be
  entered but are never displayed);
- a server page (identity, uptime, workspace disk usage, task activity).

It follows live work by polling; no work is submitted implicitly, and every mutation
corresponds to a CLI command documented in the [CLI reference](../reference/cli.md).

!!! info
    Reading committed data (`data schema`, `data preview`, `data coverage`,
    `data export`, `data snapshot`) remains a CLI and DataLoader workflow and is
    intentionally not part of the Web UI.
