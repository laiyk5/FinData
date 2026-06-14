# Dataset Card: daily

**Provider**: [Tushare Pro](https://tushare.pro)
**API**: `daily` ([docs](https://tushare.pro/document/2?doc_id=27))

Unadjusted daily OHLCV market bars for A-share securities.

## Metadata

- **Grain**: one security × one trading date
- **Primary key**: `ts_code`, `trade_date`
- **Date field**: `trade_date`
- **Calendar**: CN_A_SHARE
- **Storage**: Apache Parquet, partitioned by `trade_month=YYYYMM`

## Notes

Daily data is generally available around 15:00-16:00 CST. Suspended securities are not returned by the API. Prices are unadjusted — combine with `adj_factor` for adjusted prices. The canonical stored fields are `ts_code`, `open`, `high`, `low`, `close`, `vol`, `amount`, and `trade_date`.
