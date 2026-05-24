# Dataset Card: Trade Calendar

## Summary

`trade_calendar` stores exchange trading calendar data in a provider-independent schema.

The first real provider is Tushare Pro `trade_cal`, but the canonical dataset is named after the data concept rather than the provider.

## Intended Use

This dataset is intended to support financial data maintenance, especially missingness classification for price and market datasets.

## Source

Initial provider: Tushare Pro

API: `trade_cal`

Documentation: https://tushare.pro/document/2?doc_id=26

## Grain

One row represents one exchange on one calendar date.

Expected primary key:

```text
exchange, cal_date
```

## Coverage

Current coverage: scaffold

## Known Missingness

No real missingness has been assessed yet.

Unlike price data, missing calendar dates are structural errors unless explicitly outside the requested coverage.

## Storage

CSV is the v1 storage format for raw inspection and easy debugging.

## Validation Expectations

Minimum checks should include:

- Required fields exist.
- Primary keys are unique.
- `cal_date` is valid `YYYYMMDD`.
- `pretrade_date` is empty or valid `YYYYMMDD`.
- `is_open` is only `0` or `1`.
- Calendar coverage is continuous for the requested date range.
- Open-day `pretrade_date` points to a previous open day when present.

## Status

Status: scaffold
