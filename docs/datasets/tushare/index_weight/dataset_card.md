# Dataset Card: index_weight

**Provider**: [Tushare Pro](https://tushare.pro)
**API**: `index_weight` ([docs](https://tushare.pro/document/2?doc_id=96))

Monthly index constituent weights for A-share market indices (SSE 50, CSI 300, CSI 500, etc.).

## Metadata

- **Grain**: one index code × one constituent × one trade date
- **Primary key**: `index_code`, `con_code`, `trade_date`
- **Date field**: `trade_date`

## Usage

This dataset replaces the old `instrument_universe`. To resolve index members for a given time range, query all `con_code` rows for a specific `index_code` (e.g., `000300.SH` for CSI 300) across the desired date range. The `@universe:index:CSI300` selector uses this dataset for symbol resolution.
