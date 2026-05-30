# Reference Selectors

Reference selectors let maintenance commands refer to published reference datasets instead of long literal lists.

## Universe Selectors

Syntax:

```text
@universe:<universe_id>
```

Example:

```text
@universe:index:SSE50
```

When used with `tushare_daily --symbols`, the maintool resolves the selector from `datasets/tushare/index_weight/published/current`, queries all `con_code` values for the matching `index_code`, and writes both the selector and the concrete resolved symbols into the run manifest.

Example command:

```bash
uv run python bin/fintool --repo-root .. maintain-plan tushare_daily \
  --provider tushare \
  --enable-real-api \
  --trade-date 20260522 \
  --symbols '@universe:index:SSE50'
```

The run manifest records:

```json
{
  "symbol_selector": "@universe:index:SSE50",
  "symbol_selector_resolved_at": "20260430",
  "resolved_symbols": ["600028.SH", "600030.SH"]
}
```

This keeps the run reproducible even if the universe is updated later.

## Index Code Discovery

For Tushare-backed China index universes, do not assume the provider-native index code is obvious. Use Tushare `index_basic` to discover or verify the code, then use `index_weight` to retrieve constituents.

Examples currently used by this repository:

```text
index:SSE50  -> 000016.SH
index:CSI300 -> 000300.SH
index:CSI500 -> 000905.SH
```

The maintained source of truth for these mappings is the `tushare_index_weight` dataset card and run manifests.
