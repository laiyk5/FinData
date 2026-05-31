# Dataset Card: adj_factor

**Provider**: [Tushare Pro](https://tushare.pro)
**API**: `stk_factor_pro` ([docs](https://tushare.pro/document/2?doc_id=28))

Daily adjustment factor (复权因子) for A-share securities. Used to adjust raw OHLCV prices for corporate actions (splits, dividends).

## Metadata

- **Grain**: one security × one trading date
- **Primary key**: `ts_code`, `trade_date`
- **Date field**: `trade_date`
- **Calendar**: CN_A_SHARE

## Notes

The `adj_factor` is extracted from the 68-field `stk_factor_pro` API response. Only the 3 canonical fields (`ts_code`, `trade_date`, `adj_factor`) are stored. Multiply unadjusted prices by `adj_factor` to obtain forward-adjusted prices.
