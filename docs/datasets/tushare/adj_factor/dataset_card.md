# Dataset Card: adj_factor

**Provider**: [Tushare Pro](https://tushare.pro)
**API**: `adj_factor` ([docs](https://tushare.pro/document/2?doc_id=28))

Daily adjustment factor (复权因子) for A-share securities. Returns the forward-adjustment factor used to adjust raw OHLCV prices for corporate actions (splits, dividends).

## Metadata

- **Grain**: one security × one trading date
- **Primary key**: `ts_code`, `trade_date`
- **Date field**: `trade_date`
- **Calendar**: CN_A_SHARE

## Notes

Uses the dedicated `adj_factor` API endpoint. Only the 3 canonical fields (`ts_code`, `trade_date`, `adj_factor`) are stored. Multiply unadjusted prices by `adj_factor` to obtain forward-adjusted prices.
