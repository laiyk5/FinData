"""Demo random-walk dataset plugin for findata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyarrow as pa

from findata.sdk.contracts import DatasetSpec

if TYPE_CHECKING:
    from findata.sdk.plugins import DatasetPlugin


RANDOM_FLOAT_FIELDS = ("close", "daily_return")
RANDOM_FIELDS = ("ticker", "trade_date", *RANDOM_FLOAT_FIELDS, "volume")


def _random_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("ticker", pa.string(), nullable=False),
            pa.field("trade_date", pa.date32(), nullable=False),
            pa.field("close", pa.float64(), nullable=True),
            pa.field("daily_return", pa.float64(), nullable=True),
            pa.field("volume", pa.int64(), nullable=True),
        ]
    )


RANDOM_SPEC = DatasetSpec(
    name="findata-test/demo_random",
    api_name="demo_random",
    schema=_random_schema(),
    provider_fields=RANDOM_FIELDS,
    primary_key=("ticker", "trade_date"),
    partition_key="ticker",
    time_field="trade_date",
    missing_data_policy="accept-empty",
    capabilities={"row_limit": 6000, "time_accumulating": True},
)


def _generate_random_walk(
    tickers: list[str],
    start: str,
    end: str,
    *,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Generate deterministic random-walk price data."""
    import hashlib

    from datetime import date, timedelta

    rows: list[dict[str, Any]] = []
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)

    for ticker in tickers:
        # Deterministic per-ticker seed — used implicitly via the per-day hash
        price = 100.0
        cursor = start_date
        while cursor < end_date:
            if cursor.weekday() < 5:
                # Generate deterministic "random" step
                day_seed = int(
                    hashlib.md5(
                        f"{ticker}:{cursor.isoformat()}:{seed}".encode()
                    ).hexdigest()[:8],
                    16,
                )
                step = ((day_seed % 2001) - 1000) / 1000.0
                volume = (day_seed % 10000) * 100
                price = round(price + step, 2)
                if price <= 0:
                    price = 1.0
                daily_return = round(step / max(price - step, 0.01), 4)
                rows.append(
                    {
                        "ticker": ticker,
                        "trade_date": cursor,
                        "close": price,
                        "daily_return": daily_return,
                        "volume": volume,
                    }
                )
            cursor += timedelta(days=1)
    return rows


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize the generated rows — type coercion."""
    from findata.sdk.contracts import provider_date

    result: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        normalized["trade_date"] = provider_date(row["trade_date"])
        for field in RANDOM_FLOAT_FIELDS:
            value = row.get(field)
            normalized[field] = None if value in (None, "") else float(value)
        value = row.get("volume")
        normalized["volume"] = None if value in (None, "") else int(value)
        result.append(normalized)
    return result


def _normalize_update_symbols(value: Any, workspace: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [str(item) for item in value]
    else:
        raise ValueError(f"expected a string or array of strings, got {type(value).__name__}")
    return sorted(set(values))


def demo_random_plugin() -> "DatasetPlugin":
    from findata.sdk.plugins import DatasetPlugin, SettingSpec

    from findata_test.demo.plugins.datasets.synthetic.random.operations import (
        RandomDatasetRuntime,
    )

    return DatasetPlugin(
        name=RANDOM_SPEC.name,
        provider="demo",
        spec=RANDOM_SPEC,
        runtime=RandomDatasetRuntime(),
        operations=("update", "complete", "refresh"),
        settings={
            f"dataset.{RANDOM_SPEC.name}.update_symbols": SettingSpec(
                schema={"type": "array", "minItems": 1, "items": {"type": "string"}},
                normalize=_normalize_update_symbols,
                help="Tickers maintained by update (e.g. ['AAPL', 'GOOGL']).",
                required=True,
            ),
            f"dataset.{RANDOM_SPEC.name}.seed": SettingSpec(
                schema={"type": "integer", "minimum": 0},
                normalize=lambda v, ws: int(v) if not isinstance(v, int) else v,
                help="Random seed for reproducible data generation.",
                required=False,
            ),
        },
        schedule=("30 18 * * 1-5", "Asia/Shanghai"),
        family=("demo", "synthetic"),
    )
