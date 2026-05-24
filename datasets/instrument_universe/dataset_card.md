# Instrument Universe Dataset Card

## Summary

`instrument_universe` stores named sets of instruments that other maintained datasets can reference. It is intended to answer "which instruments are in scope?" for maintenance runs such as daily price ingestion.

The first provider-backed universe type is a Tushare index universe sourced from `index_weight`, for examples such as SSE 50 (`index:SSE50`), CSI 300, and CSI 500.

Tushare is not treated as the exchange's official primary publication. In this repository it is the maintained provider path for China market index universes until an official exchange/index-provider source is added and verified.

## Coverage

Published universes:

- `index:SSE50`: Tushare source index `000016.SH`, latest published snapshot `20260430`.

## Provider Discovery Procedure

Index universe maintenance uses a two-step Tushare procedure:

1. Discover or verify the index code with `index_basic`.
2. Retrieve the member list and weights with `index_weight`.

This procedure matters because the provider-native index code is not obvious to a new user or maintenance agent. For example, this repository maps:

| universe_id | index name | Tushare source_id | index_basic market |
| --- | --- | --- | --- |
| `index:SSE50` | SSE 50 / 上证50 | `000016.SH` | `SSE` |
| `index:CSI300` | CSI 300 / 沪深300 | `000300.SH` | usually `CSI` or code lookup by name |
| `index:CSI500` | CSI 500 / 中证500 | `000905.SH` | usually `CSI` or code lookup by name |

Suggested lookup flow:

```text
index_basic(name=<index short name>, market=<candidate market>)
  -> confirm ts_code, name/fullname, market, publisher, category
index_weight(index_code=<confirmed ts_code>, start_date=<window>, end_date=<window>)
  -> normalize con_code rows into instrument_universe members
```

Useful Tushare fields from `index_basic` include `ts_code`, `name`, `fullname`, `market`, `publisher`, `category`, `base_date`, `list_date`, and `weight_rule`. The provider supports market filters such as `SSE`, `SZSE`, and `CSI`.

The run manifest should preserve both:

- the logical `universe_id`, such as `index:SSE50`
- the provider-native `source_id`, such as `000016.SH`

## Known Missingness

Provider snapshots can lag the current trading day. A universe run should record the `as_of_date` returned by the provider, and downstream runs should preserve the selector and resolved member list in their run manifest.

If `index_weight` returns no rows for the requested recent period, do not synthesize a member list. Expand the request to an earlier monthly window or first verify the index code with `index_basic`.

## Validation Expectations

- `universe_id`, `member_code`, and `as_of_date` must be present.
- Duplicate `(universe_id, member_code, as_of_date)` rows block publish.
- Date fields must be `YYYYMMDD` when present.
- Weights must be decimal values when present and cannot be negative.
- An empty universe blocks publish.
