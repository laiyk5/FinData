# `findata-plugins/tushare_stock_basic`

API: <https://tushare.pro/document/2?doc_id=25>

The complete A-share security table across provider statuses `L`, `D`, `P`, and `G`.

| field | Arrow type | nullable | field | Arrow type | nullable |
| --- | --- | --- | --- | --- | --- |
| `ts_code` | `utf8` | no | `symbol` | `utf8` | no |
| `name` | `utf8` | no | `area` | `utf8` | yes |
| `industry` | `utf8` | yes | `fullname` | `utf8` | yes |
| `enname` | `utf8` | yes | `cnspell` | `utf8` | yes |
| `market` | `utf8` | yes | `exchange` | `utf8` | no |
| `curr_type` | `utf8` | yes | `list_status` | `utf8` | no |
| `list_date` | `date32[day]` | yes | `delist_date` | `date32[day]` | yes |
| `is_hs` | `utf8` | yes | `act_name` | `utf8` | yes |
| `act_ent_type` | `utf8` | yes |  |  |  |

- **provider**: `tushare`
- **keys**: primary key `ts_code`; no partition or time field
- **settings**: none; every `update` fetches the complete provider table
- **publication timing**: no publication window; the provider maintains a complete current view
- **suggested schedule**: cron `0 8 * * 1`, `Asia/Shanghai`
- **missing-data policy**: `strict`; the merged table is never legitimately empty
- **request plan**: request each of `L`, `D`, `P`, and `G` separately for `SSE`, `SZSE`, and `BSE`, merge by `ts_code`, and fail on conflicting duplicates or any response reaching the provider's 6000-row limit because completeness would be uncertain
- **dependencies**: none
- **operations**:
  - `update()` — fetch all four statuses and replace the committed table
- **toolkit**: mock API
- **storage mutation**: complete table replacement in one DuckDB transaction; the validated result
  of all requests is one indivisible work item
- **status fields**: number of symbols grouped by `list_status`
