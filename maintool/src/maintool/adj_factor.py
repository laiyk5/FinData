from __future__ import annotations

FIELDS = ("ts_code", "trade_date", "adj_factor")
FIELD_LIST = ["ts_code", "trade_date", "adj_factor"]


def fake_adj_factor_rows(symbols: list[str], trade_date: str) -> list[dict[str, str]]:
    return [
        {"ts_code": symbol, "trade_date": trade_date, "adj_factor": str(round(1.0 + i * 0.01, 3))}
        for i, symbol in enumerate(symbols)
    ]
