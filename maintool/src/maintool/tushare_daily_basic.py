from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


FIELDS = (
    "ts_code",
    "trade_date",
    "close",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
)


@dataclass(frozen=True)
class DailyBasicRow:
    ts_code: str
    trade_date: str
    close: str
    turnover_rate: str
    turnover_rate_f: str
    volume_ratio: str
    pe: str
    pe_ttm: str
    pb: str
    ps: str
    ps_ttm: str
    dv_ratio: str
    dv_ttm: str
    total_share: str
    float_share: str
    free_share: str
    total_mv: str
    circ_mv: str

    def as_dict(self) -> dict[str, str]:
        return {
            "ts_code": self.ts_code,
            "trade_date": self.trade_date,
            "close": self.close,
            "turnover_rate": self.turnover_rate,
            "turnover_rate_f": self.turnover_rate_f,
            "volume_ratio": self.volume_ratio,
            "pe": self.pe,
            "pe_ttm": self.pe_ttm,
            "pb": self.pb,
            "ps": self.ps,
            "ps_ttm": self.ps_ttm,
            "dv_ratio": self.dv_ratio,
            "dv_ttm": self.dv_ttm,
            "total_share": self.total_share,
            "float_share": self.float_share,
            "free_share": self.free_share,
            "total_mv": self.total_mv,
            "circ_mv": self.circ_mv,
        }


def fake_daily_basic_rows(symbols: list[str], trade_date: str) -> list[DailyBasicRow]:
    rows: list[DailyBasicRow] = []
    for index, symbol in enumerate(symbols):
        base = Decimal("10.00") + Decimal(index)
        close = base + Decimal("0.20")
        total_share = Decimal("100000") + Decimal(index * 1000)
        float_share = total_share * Decimal("0.80")
        free_share = total_share * Decimal("0.60")
        total_mv = (total_share * close).quantize(Decimal("0.001"))
        circ_mv = (float_share * close).quantize(Decimal("0.001"))
        rows.append(
            DailyBasicRow(
                ts_code=symbol,
                trade_date=trade_date,
                close=str(close),
                turnover_rate=str(Decimal("1.25") + Decimal(index) / Decimal("10")),
                turnover_rate_f=str(Decimal("1.50") + Decimal(index) / Decimal("10")),
                volume_ratio=str(Decimal("0.80") + Decimal(index) / Decimal("100")),
                pe=str(Decimal("12.50") + Decimal(index)),
                pe_ttm=str(Decimal("13.10") + Decimal(index)),
                pb=str(Decimal("1.20") + Decimal(index) / Decimal("10")),
                ps=str(Decimal("2.30") + Decimal(index) / Decimal("10")),
                ps_ttm=str(Decimal("2.40") + Decimal(index) / Decimal("10")),
                dv_ratio=str(Decimal("1.10")),
                dv_ttm=str(Decimal("1.20")),
                total_share=str(total_share),
                float_share=str(float_share),
                free_share=str(free_share),
                total_mv=str(total_mv),
                circ_mv=str(circ_mv),
            )
        )
    return rows
