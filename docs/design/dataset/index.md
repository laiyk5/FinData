# Dataset plugins

This file is the canonical catalog of dataset-plugin contracts. Architectural rules shared by every dataset live in [core.md](../core.md); reusable plugin-side components live in [toolkit/index.md](../toolkit/index.md).

Each dataset entry defines its provider, logical Arrow schema, keys, observation domain, plugin
settings, publication timing, missing-data policy, dependencies, operations and operand semantics,
storage, coverage, and status fields. Machine-readable schemas created during implementation must
express these contracts without changing them.

Datasets:

- [`findata/tushare/trade_cal`](findata/tushare/trade_cal.md)
- [`findata/tushare/stock_basic`](findata/tushare/stock_basic.md)
- [`findata/tushare/index_basic`](findata/tushare/index_basic.md)
- [`findata/tushare/index_weight`](findata/tushare/index_weight.md)
- [`findata/tushare/daily_basic`](findata/tushare/daily_basic.md)

## Common conventions

- Logical dates are Arrow `date32[day]`, strings are `utf8`, and floating-point provider values are `float64`. Provider `YYYYMMDD` strings are normalized before validation and never exposed as the logical date type.
- A non-null primary-key field is required on every row, and primary-key tuples must be unique within
  each committed dataset revision. A missing declared provider field is an error; undeclared extra
  provider fields are ignored until intentionally added by a data-layout version.
- Operation `timerange` values are nonempty half-open `[start, end)` civil-date ranges. The CLI spelling is `YYYY-MM-DD:YYYY-MM-DD`; `today` is resolved once, in the dataset timezone, to the current date used as an exclusive endpoint. Inclusive provider endpoints are an adapter detail.
- `symbols`, `indexes`, and `exchanges` are nonempty arrays of strings, deduplicated after canonicalization. A single CLI scalar is coerced to a one-element array.
- `update` is always parameterless. Its dataset plugin alone interprets any settings needed to
  select work. A one-time `complete` or `refresh` never changes plugin settings.
- Each declared plugin setting is classified as `required` or optional. A required setting gates
  update readiness: `update` is not ready while a required setting is unconfigured, and clients
  (CLI, WebUI) must warn only about unconfigured required settings. Optional settings never
  produce warnings. The classification is part of the plugin's declared setting specification and
  is exposed through dataset descriptions and the configuration-keys API.
- Built-in `complete` and `refresh` operations declare their fully normalized operands as a stable coalescing identity. `update` never coalesces because its target depends on submission time, committed dataset state, and the plugin-settings revision.
