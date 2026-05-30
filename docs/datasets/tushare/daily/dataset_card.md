# Dataset Card: daily

**Provider**: [Tushare Pro](https://tushare.pro)
**API**: `daily` ([docs](https://tushare.pro/document/2?doc_id=27))

Unadjusted daily OHLCV market bars for A-share securities.

## Metadata

- **Grain**: one security × one trading date
- **Primary key**: `ts_code`, `trade_date`
- **Date field**: `trade_date`
- **Calendar**: CN_A_SHARE

## Notes

Daily data is generally available around 15:00-16:00 CST. Suspended securities are not returned by the API. Prices are unadjusted — use `stk_factor_pro` for forward/backward-adjusted prices.
