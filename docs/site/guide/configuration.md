# Configuration

Workspace configuration is a flat key/value store managed through the CLI (or the Web UI).

```bash
findata config ls                          # list all values
findata config get [key]                   # one value, or all when omitted
findata config set <key> <value>           # set a non-secret value
findata config set <key> --value-json JSON # typed JSON value (also @file or - for stdin)
findata config unset <key>
```

## Secrets

Secret values are always redacted in `config ls` and `config get`. v1 intentionally has
no command for revealing a stored secret, and literal secrets are rejected on the command
line — use one of:

```bash
findata config set provider.tushare.token --stdin          # paste; not in shell history
findata config set provider.tushare.token --env TUSHARE_TOKEN   # environment reference
```

`--env` stores a reference to an environment variable and is the recommended form for
provider tokens.

## Dataset-owned settings

Keys under `dataset.<dataset-name>.*` are owned by that dataset's plugin. Core findata
transports and stores the value but does not interpret it; the plugin declares the setting
names and schemas, normalizes values, reports update readiness, and provides
setting-specific help through `dataset describe`. Unknown dataset settings and invalid
values are rejected before configuration is changed; the error for a registered dataset
lists its declared setting keys.

Each declared setting is classified as **required** or optional. A required setting gates
update readiness: `update` is not ready while it is unconfigured, and clients warn only
about unconfigured required settings.

```bash
findata config set dataset.findata/tushare/daily_basic.update_symbols \
  --value-json '["tushare:000300.SH@latest"]'
```

## Discovering keys

Declared keys can be discovered without knowing them in advance:

- shell completion for `config set|get|unset` suggests declared keys alongside
  already-set ones;
- `findata dataset describe <dataset>` shows that dataset's declared settings with help
  and whether each is configured;
- the HTTP API exposes every declared core, provider, and dataset key at
  `GET /v1/config/keys` (with `key`, `help`, `schema`, `configured`, and `secret`).

## Display settings

`display.timezone` controls the timezone used for human-readable timestamps
(`HH:MM:SS` log lines, event times). Structured JSON/JSONL output always keeps the
original values.
