# Dataset Card: stk_factor_pro

**Provider**: [Tushare Pro](https://tushare.pro)
**API**: `stk_factor_pro` ([docs](https://tushare.pro/document/2?doc_id=34))

Daily OHLCV bars with forward/backward-adjusted prices, valuation indicators, and technical analysis factors for A-share securities.

## Metadata

- **Grain**: one security × one trading date
- **Primary key**: `ts_code`, `trade_date`
- **Date field**: `trade_date`
- **Calendar**: CN_A_SHARE

## Notes

Includes both unadjusted (`open`, `high`, `low`, `close`) and adjusted (`*_hfq`, `*_qfq`) prices. Technical indicators (MACD, BOLL, KDJ, MA, EMA, etc.) are computed by the provider. Many fields are nullable — indicators may not be available for newly listed stocks.
