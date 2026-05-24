# Dataset Card: Tushare Daily

## Summary

`tushare_daily` stores unadjusted A-share daily market bars from the Tushare Pro `daily` API.

The dataset name intentionally matches both the provider and API name. This keeps the first pilot dataset narrow, auditable, and easy to compare against the provider documentation.

## Intended Use

This dataset is intended for financial research, data repository maintenance experiments, and development of validation, missingness, publication, and archive workflows.

Because the endpoint returns unadjusted prices, consumers should not use this dataset as split/dividend-adjusted price history unless an adjustment process is added later.

## Source

Provider: Tushare Pro

API: `daily`

Documentation: https://tushare.pro/document/2?doc_id=27

The endpoint provides daily OHLCV market data for A-share securities. According to the provider documentation, daily data is generally available around 15:00-16:00 China time, and no records are returned for suspended securities on suspended trading dates.

## Request Scheduling

The maintool plans Tushare `daily` requests before preparation and records the selected request plan in each run manifest.

The scheduler uses the provider's documented parameter shapes:

- comma-separated `ts_code` with `start_date` and `end_date` for symbol/date-range batches
- comma-separated `ts_code` with `trade_date` for one-day symbol batches
- `trade_date` without `ts_code` for all-market daily pulls when explicitly selected

The default strategy is `auto`. It chooses the lowest request count while keeping each planned symbol/date-range batch at or below the documented 6000-row request limit. For selected universes such as CSI300, this usually means batched comma-separated symbols over date chunks, not all-market `trade_date` pulls.

The strategy can be overridden with `--daily-request-strategy symbol_range` or `--daily-request-strategy trade_date_all`. When `trade_date_all` is used for a selected symbol list, ingestion filters raw rows back to the requested symbols before staging.

## Grain

One row represents one Tushare security code on one trading date.

Expected primary key:

```text
ts_code, trade_date
```

## Coverage

Current coverage: fake-provider scaffold data only

Real Tushare ingestion is available only through explicit CLI opt-in with `--provider tushare --enable-real-api`. The default provider remains fake.

## Known Missingness

No real missingness has been assessed yet.

Expected missingness categories include:

- Market holidays and non-trading days
- Security suspensions
- Provider-side unavailable history
- Symbols outside the configured universe

Suspension-related missingness may be acceptable when confirmed against an appropriate trading calendar or suspension source.

## Storage

Early staged and published data uses CSV for easier inspection. Parquet may be introduced later after validation and inspection tools are mature.

## Validation Expectations

Minimum checks should include:

- Required fields exist.
- Primary keys are unique.
- `trade_date` is an 8-digit date in `YYYYMMDD` format.
- `high` is greater than or equal to `open`, `close`, and `low`.
- `low` is less than or equal to `open`, `close`, and `high`.
- `vol` and `amount` are non-negative.
- Missingness is documented.

## Status

Status: fake-provider scaffold
