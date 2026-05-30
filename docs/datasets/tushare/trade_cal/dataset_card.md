# Dataset Card: trade_cal

**Provider**: [Tushare Pro](https://tushare.pro)
**API**: `trade_cal` ([docs](https://tushare.pro/document/2?doc_id=26))

Exchange trading calendar indicating whether each date is a trading day, for SSE and SZSE.

## Metadata

- **Grain**: one exchange × one calendar date
- **Primary key**: `exchange`, `cal_date`
- **Date field**: `cal_date`
- **Partition**: by `exchange` (SSE, SZSE)

## Notes

Used by daily datasets for missingness classification — dates where `is_open=0` explain expected gaps from market holidays.
