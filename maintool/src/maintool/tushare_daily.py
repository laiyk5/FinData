from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


FIELDS = (
    "ts_code",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
    "trade_date",
)


@dataclass(frozen=True)
class DailyRow:
    ts_code: str
    trade_date: str
    open: str
    high: str
    low: str
    close: str
    vol: str
    amount: str

    def as_dict(self) -> dict[str, str]:
        return {
            "ts_code": self.ts_code,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "vol": self.vol,
            "amount": self.amount,
            "trade_date": self.trade_date,
        }


def fake_daily_rows(symbols: list[str], trade_date: str) -> list[DailyRow]:
    rows: list[DailyRow] = []
    for index, symbol in enumerate(symbols):
        base = Decimal("10.00") + Decimal(index)
        open_price = base
        close_price = base + Decimal("0.20")
        high_price = close_price + Decimal("0.10")
        low_price = open_price - Decimal("0.10")
        vol = Decimal("100000") + Decimal(index * 1000)
        amount = (vol * close_price / Decimal("10")).quantize(Decimal("0.001"))
        rows.append(
            DailyRow(
                ts_code=symbol,
                trade_date=trade_date,
                open=str(open_price),
                high=str(high_price),
                low=str(low_price),
                close=str(close_price),
                vol=str(vol),
                amount=str(amount),
            )
        )
    return rows


def write_fake_update(dataset_root: Path, symbols: list[str], trade_date: str) -> tuple[Path, Path]:
    rows = fake_daily_rows(symbols, trade_date)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = dataset_root / "data" / "raw" / f"fake_daily_{trade_date}_{stamp}.json"
    staged_path = dataset_root / "data" / "staged" / f"daily_{trade_date}.csv"
    log_path = dataset_root / "logs" / f"{stamp}_fake_update.json"

    raw_payload = {
        "provider": "fake_tushare",
        "api": "daily",
        "trade_date": trade_date,
        "symbols": symbols,
        "fields": list(FIELDS),
        "items": [row.as_dict() for row in rows],
    }
    raw_path.write_text(json.dumps(raw_payload, indent=2), encoding="utf-8")

    with staged_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(row.as_dict() for row in rows)

    log_payload = {
        "run_type": "fake_update",
        "dataset": "tushare_daily",
        "provider": "fake_tushare",
        "trade_date": trade_date,
        "symbols_requested": symbols,
        "records_written": len(rows),
        "raw_path": str(raw_path.relative_to(dataset_root)),
        "staged_path": str(staged_path.relative_to(dataset_root)),
        "finished_at": stamp,
    }
    log_path.write_text(json.dumps(log_payload, indent=2), encoding="utf-8")
    return raw_path, staged_path


def validate_staged_csv(path: Path) -> list[str]:
    errors: list[str] = []
    seen_keys: set[tuple[str, str]] = set()

    with path.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = reader.fieldnames or []
        for field in FIELDS:
            if field not in fieldnames:
                errors.append(f"{path.name}: missing column {field}")
        if errors:
            return errors

        for row_number, row in enumerate(reader, start=2):
            key = (row["ts_code"], row["trade_date"])
            if key in seen_keys:
                errors.append(f"{path.name}:{row_number}: duplicate primary key {key}")
            seen_keys.add(key)

            if len(row["trade_date"]) != 8 or not row["trade_date"].isdigit():
                errors.append(f"{path.name}:{row_number}: invalid trade_date {row['trade_date']}")

            prices = {}
            for field in ("open", "high", "low", "close", "vol", "amount"):
                try:
                    prices[field] = Decimal(row[field])
                except InvalidOperation:
                    errors.append(f"{path.name}:{row_number}: invalid decimal in {field}")

            if not {"open", "high", "low", "close"}.issubset(prices):
                continue

            if prices["high"] < max(prices["open"], prices["close"], prices["low"]):
                errors.append(f"{path.name}:{row_number}: high is lower than another OHLC price")
            if prices["low"] > min(prices["open"], prices["close"], prices["high"]):
                errors.append(f"{path.name}:{row_number}: low is higher than another OHLC price")
            if prices.get("vol", Decimal("0")) < 0:
                errors.append(f"{path.name}:{row_number}: vol is negative")
            if prices.get("amount", Decimal("0")) < 0:
                errors.append(f"{path.name}:{row_number}: amount is negative")

    return errors
