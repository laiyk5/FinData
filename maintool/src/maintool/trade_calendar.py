from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .dataset_specs import TRADE_CALENDAR_FIELDS
from .jsonio import write_json
from .run_sandbox import utc_stamp


FIELDS = TRADE_CALENDAR_FIELDS


def mock_trade_calendar_rows(exchange: str, start_date: str, end_date: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    previous_open = ""
    while current <= end:
        cal_date = current.strftime("%Y%m%d")
        is_open = "0" if current.weekday() >= 5 else "1"
        rows.append(
            {
                "exchange": exchange,
                "cal_date": cal_date,
                "is_open": is_open,
                "pretrade_date": previous_open if is_open == "1" else "",
            }
        )
        if is_open == "1":
            previous_open = cal_date
        current += timedelta(days=1)
    return rows


def write_mock_trade_calendar_response(raw_root: Path, request: dict) -> tuple[Path, int]:
    rows = mock_trade_calendar_rows(request["exchange"], request["start_date"], request["end_date"])
    payload = {
        "provider": "mock",
        "api": "trade_cal",
        "exchange": request["exchange"],
        "start_date": request["start_date"],
        "end_date": request["end_date"],
        "fields": list(FIELDS),
        "items": rows,
        "row_count": len(rows),
        "prepared_at": utc_stamp(),
    }
    raw_path = raw_root / f"trade_cal_{request['exchange']}_{request['start_date']}_{request['end_date']}.json"
    write_json(raw_path, payload)
    return raw_path, len(rows)
