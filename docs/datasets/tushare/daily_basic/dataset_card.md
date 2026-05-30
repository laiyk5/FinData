# Dataset Card: daily_basic

**Provider**: [Tushare Pro](https://tushare.pro)
**API**: `daily_basic` ([docs](https://tushare.pro/document/2?doc_id=32))

Daily valuation and fundamental indicators for A-share securities (PE, PB, PS, market cap, turnover, etc.).

## Metadata

- **Grain**: one security × one trading date
- **Primary key**: `ts_code`, `trade_date`
- **Date field**: `trade_date`
- **Calendar**: CN_A_SHARE

## Notes

Many indicator fields are nullable — values may be missing for newly listed stocks or illiquid securities. `total_share` and `float_share` are in ten-thousands of shares.
