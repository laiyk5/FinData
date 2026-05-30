# Dataset Card: moneyflow

**Provider**: [Tushare Pro](https://tushare.pro)
**API**: `moneyflow` ([docs](https://tushare.pro/document/2?doc_id=31))

Daily money flow data categorized by order size (small/medium/large/extra-large) for A-share securities.

## Metadata

- **Grain**: one security × one trading date
- **Primary key**: `ts_code`, `trade_date`
- **Date field**: `trade_date`
- **Calendar**: CN_A_SHARE

## Notes

All value fields are nullable. Money flow is calculated from tick-level data by the provider.
