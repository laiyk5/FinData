# CLI reference

Every command below is a `findata` subcommand. Operational commands support
`--format human|json|jsonl`; `jsonl` is used for streams. Stdout contains command
results and stderr contains diagnostics.

## Output and exit codes

Human output is the default. Collection commands use compact tables, detail commands use
labeled fields, and an empty result says what was not found rather than printing an empty
JSON value. `--color auto|always|never` controls human styling; automatic styling applies
only to interactive terminals and is disabled when `NO_COLOR` is set or `TERM=dumb`.

JSON emits exactly one JSON document. JSONL emits one complete object per event or record
with a stable `type` field. Neither format includes spinners, banners, or other human
decoration. Because following is a stream, `--follow --format json` is rejected with
exit code `2`; use `--format jsonl` instead.

| exit code | meaning |
| --- | --- |
| `0` | success |
| `1` | operational failure, or a failed/canceled task when waiting |
| `2` | invalid CLI usage |
| `130` | interrupted wait or follow; the accepted server task remains running |

Human value formatting uses the declared meaning of a field: timestamps are ISO 8601 in
the configured display timezone with UTC offset, durations use adaptive units
(`240 ms`, `3.2 s`, `2 min 5 s`), counts use ASCII thousands grouping, and exact
decimals, identifiers, and dates are never passed through generic numeric formatting.

## Tasks

- `task run <dataset> [operation] [--param key=value ... | --params JSON|@file|-] [--wait|--follow]`
  — `operation` defaults to `update`. Repeated `--param` pairs, inline JSON, JSON from
  `@file`, and JSON from stdin (`-`) are mutually exclusive input forms.
- `task ls [--all] [--dataset NAME] [--status STATUS]`
- `task status <id>` / `task watch <id>` / `task explain <id>`
- `task logs <id> [--follow]` — `-f` aliases `--follow`. Human output renders one line
  per record as `HH:MM:SS message` in the display timezone; JSONL emits one typed record
  per line.
- `task cancel <id>` — cancel this request; reports whether shared execution continued
  for another requester.
- `task retry <id> [--wait|--follow]`

See [Tasks and events](../guide/tasks-and-events.md) for lifecycle states, coalescing,
and identifier prefixes.

## Datasets

- `dataset ls`
- `dataset describe <name>` — provider readiness, capabilities, dependencies, declared
  settings, storage, and status metadata.
- `dataset operations <name>` / `dataset operation <name> <operation>` — operand schema
  and per-operand help.
- `dataset status <name>` / `dataset status --all` — committed maintenance state.
- `dataset reset <name> [--yes]` — replace one dataset with a new uninitialized database;
  settings and task history are preserved.
- `dataset update|complete|refresh <name> [operands] [--wait|--follow] [--dry-run]` —
  ergonomic operation commands generated from the plugin's operation schema. Array
  operands use repeatable plural flags such as `--symbols`; half-open date ranges use
  `--timerange START:END` or `--from` plus `--to`.

See [Providers and datasets](../guide/providers-and-datasets.md) for operand conventions
and dry-run semantics.

## Providers

- `provider ls` / `provider status <name>` / `provider check <name>`

Provider commands never display credentials.

## Cron

- `cron ls` / `cron enable <dataset>` / `cron disable <dataset>` / `cron reset <dataset>`
- `cron set <dataset> --expression CRON --timezone IANA_ZONE`

## Events and system

- `system status`
- `events ls [--unread] [--since DURATION] [--severity LEVEL]` — `LEVEL` is `info`,
  `warning`, or `error`; `DURATION` looks like `30m`, `12h`, or `7d`.
- `events ack <id>` / `events ack --all`

## Web and server lifecycle

- `web open` — open the workspace WebUI and sign in through a one-time local browser session.
- `findata-server status <workspace>` — verify and describe that workspace's running server.
- `findata-server stop <workspace>` — request authenticated graceful shutdown.
- `findata-server restart <workspace> [--host HOST] [--port PORT] [--provider-mode real|mock]` —
  stop the verified server, then run a foreground replacement.

## Configuration

- `config ls` / `config get [key]` — secret values are always redacted.
- `config set <key> <value>` / `config set <key> --value-json JSON|@file|-`
- `config set <key> --stdin` / `config set <key> --env <variable>`
- `config unset <key>`

## Data

- `data schema <dataset>`
- `data preview <dataset> [--keys K ...] [--from D --to D] [--columns C,...] [--limit N]`
- `data coverage <dataset> [--keys K ...] [--from D --to D]`
- `data export <dataset> --output PATH|- --output-format csv|parquet|arrow|jsonl [--batch-size N] [--force]`
- `data snapshot <dataset> [--output PATH]`

See [Reading data](../guide/reading-data.md).

## Completion

`completion <bash|zsh|fish>` generates a shell-completion script. Generating it does not
activate completion; the current shell must source it:

```bash
# zsh: add to ~/.zshrc
eval "$(findata completion zsh)"

# bash: add to ~/.bashrc
eval "$(findata completion bash)"

# fish: add to ~/.config/fish/config.fish
findata completion fish | source
```

Completion suggests command families, dataset and provider names, operations,
configuration keys, retained task IDs, and schema-declared operand flags. It uses a
credentialed hidden CLI query rather than putting a workspace token in the shell script,
and falls back to static command completion when no workspace or server is available.

## Live diagnostics

While waiting or following in human mode, progress remains transient. The first ten
distinct warning or error diagnostics remain visible as ordinary lines; exact repeats may
be combined with an occurrence count; further distinct diagnostics are suppressed with a
counted notice. A terminal failure is always printed. The final summary names
`findata task logs <id>` or `findata events ls` when retained details are available.
JSONL represents every logical occurrence; JSON and JSONL do not apply the human
visibility limit.
