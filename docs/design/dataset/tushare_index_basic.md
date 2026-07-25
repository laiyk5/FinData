# `tushare_index_basic`

API: <https://tushare.pro/document/2?doc_id=94>

Locally materialized Tushare index-reference metadata used for validation. It contains only indexes
explicitly requested by a user or dependency and preserves each exact provider `ts_code`. It never
enumerates Tushare markets implicitly.

| field | Arrow type | nullable | meaning |
| --- | --- | --- | --- |
| `ts_code` | `utf8` | no | exact Tushare provider ID |
| `name` | `utf8` | no | provider short name |
| `fullname` | `utf8` | yes | provider full name |
| `market` | `utf8` | no | Tushare market or service-provider category |
| `publisher` | `utf8` | yes | reported publisher |
| `index_type` | `utf8` | yes | reported index style |
| `category` | `utf8` | yes | reported index category |
| `base_date` | `date32[day]` | yes | reported base date |
| `base_point` | `float64` | yes | reported base point |
| `list_date` | `date32[day]` | yes | reported publication date |
| `weight_rule` | `utf8` | yes | reported weighting method |
| `desc` | `utf8` | yes | provider description |
| `exp_date` | `date32[day]` | yes | reported termination date |

- **provider**: `tushare`
- **keys**: primary key `ts_code`; no partition or time field
- **settings**: none; the tracked set is the set of rows already committed, not separate configuration
- **missing-data policy**: `strict`; an empty or mismatched response for an explicitly requested
  `ts_code` is a failure
- **request plan**: one `index_basic(ts_code=...)` request per exact requested index; the plugin
  never uses the provider's `market` query
- **dependencies**: none
- **dependency fulfillment**: `{indexes}` requires a nonempty array of unsuffixed
  `tushare:<ts_code>` references and maps absent references to `complete(indexes=...)`
- **operations**:
  - `update()` — refresh exactly the indexes in the committed table; an uninitialized or empty
    dataset is unready and directs the user to `complete`
  - `complete(indexes)` — fetch the explicitly requested references, merge them with existing rows,
    and replace the committed table; it never discovers or adds another index
- **toolkit**: checkpoint-batch planner, mock API
- **storage mutation**: exact-`ts_code` replacement; the operation's merged logical result commits
  transactionally
- **status fields**: refresh time and number of tracked indexes

Reference presence establishes only that Tushare returned its metadata. It does not promise
`index_weight` coverage, historical depth, or account permission. Core findata has no
index-specific command.

The built-in Tushare plugins spell an index reference as `tushare:<ts_code>`, for example
`tushare:000300.SH`. This is plugin syntax, not a core findata identifier. The prefix distinguishes
an index selector from a direct security code, while the remainder is copied byte-for-byte into the
provider's `index_basic.ts_code` request. An unknown reference is rejected locally. A consuming
plugin may additionally define `@YYYYMM`, `@latest`, or bare range-union selection, but suffixes are
not part of the provider identity.
