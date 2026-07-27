# Official plugins

The **findata-plugins** family adds Tushare financial data support. Install it from a
checkout of this repository:

```bash
pip install -e ./plugins/tushare/provider \
             ./plugins/tushare/trade-cal \
             ./plugins/tushare/stock-basic \
             ./plugins/tushare/index-basic \
             ./plugins/tushare/index-weight \
             ./plugins/tushare/daily-basic
```

See [Installation](../get-started/installation.md).

## Datasets

| Dataset | Contents | Operations | Settings |
|---|---|---|---|
| `findata-plugins/tushare_trade_cal` | Exchange trade calendars (SSE, SZSE) | `update`, `complete` | — |
| `findata-plugins/tushare_stock_basic` | Listed-stock master data | `update` | — |
| `findata-plugins/tushare_index_basic` | Index metadata | `update`, `complete` | — |
| `findata-plugins/tushare_index_weight` | Monthly index constituent weights | `update`, `complete` | `update_indexes` |
| `findata-plugins/tushare_daily_basic` | Daily valuation metrics (PE, PB, turnover, …) | `update`, `complete`, `refresh` | `update_symbols` |

## Provider

| Provider | Description | Configuration |
|---|---|---|
| `findata-plugins/tushare` | Tushare API client with rate-limited transport | `token` (required), `rate_limit` |

The canonical per-dataset contracts (schemas, publication windows, missing-data policy)
live in the repository under `docs/design/dataset/`.

### Usage

Once installed, the plugins mount automatically on the next server start:

```bash
findata-server init ~/market-data
findata-server start ~/market-data
```

Configure your [Tushare API token](https://tushare.pro) and you're ready:

```bash
findata config set provider.findata-plugins/tushare.token --stdin
findata task run findata-plugins/tushare_daily_basic complete \
  --param symbols=tushare:000300.SH \
  --param timerange=2026-06-29:2026-07-04 \
  --wait
```

See the [Quick start](../get-started/quickstart.md) for a full walkthrough.
