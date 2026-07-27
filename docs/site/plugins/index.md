# Official plugins

findata ships two plugin families in this repository:

| Family | Description | Credentials needed |
|---|---|---|
| [Demo plugins](demo.md) | Evaluation — mock data, works immediately | None |
| [Tushare plugins](tushare.md) | Chinese A-share market data | [Tushare API token](https://tushare.pro) with credits |

## Demo plugins

The `findata-test` family provides always-ready mock data with no API token. Install,
start with `--provider-mode mock`, and run tasks immediately. See the
[Demo plugins](demo.md) page.

## Tushare plugins

The `findata-plugins` family provides real financial data through the Tushare API.
It requires a [Tushare API token](https://tushare.pro) with sufficient credits.
See the [Tushare plugins](tushare.md) page.
