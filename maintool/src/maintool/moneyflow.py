from __future__ import annotations

from decimal import Decimal

from .tushare_daily import fake_daily_rows


FIELDS = (
    "ts_code",
    "trade_date",
    "buy_sm_vol",
    "buy_sm_amount",
    "sell_sm_vol",
    "sell_sm_amount",
    "buy_md_vol",
    "buy_md_amount",
    "sell_md_vol",
    "sell_md_amount",
    "buy_lg_vol",
    "buy_lg_amount",
    "sell_lg_vol",
    "sell_lg_amount",
    "buy_elg_vol",
    "buy_elg_amount",
    "sell_elg_vol",
    "sell_elg_amount",
    "net_mf_vol",
    "net_mf_amount",
)


def fake_moneyflow_rows(symbols: list[str], trade_date: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, daily_row in enumerate(fake_daily_rows(symbols, trade_date)):
        vol = Decimal(daily_row.vol)
        amount = Decimal(daily_row.amount)

        buy_sm_vol = quantize_int(vol * Decimal("0.18"))
        sell_sm_vol = quantize_int(vol * Decimal("0.16"))
        buy_md_vol = quantize_int(vol * Decimal("0.22"))
        sell_md_vol = quantize_int(vol * Decimal("0.20"))
        buy_lg_vol = quantize_int(vol * Decimal("0.14"))
        sell_lg_vol = quantize_int(vol * Decimal("0.13"))
        buy_elg_vol = quantize_int(vol * Decimal("0.10"))
        sell_elg_vol = quantize_int(vol * Decimal("0.09"))

        buy_sm_amount = quantize_amount(amount * Decimal("0.18"))
        sell_sm_amount = quantize_amount(amount * Decimal("0.16"))
        buy_md_amount = quantize_amount(amount * Decimal("0.22"))
        sell_md_amount = quantize_amount(amount * Decimal("0.20"))
        buy_lg_amount = quantize_amount(amount * Decimal("0.14"))
        sell_lg_amount = quantize_amount(amount * Decimal("0.13"))
        buy_elg_amount = quantize_amount(amount * Decimal("0.10"))
        sell_elg_amount = quantize_amount(amount * Decimal("0.09"))

        net_mf_vol = str(
            int(buy_sm_vol)
            + int(buy_md_vol)
            + int(buy_lg_vol)
            + int(buy_elg_vol)
            - int(sell_sm_vol)
            - int(sell_md_vol)
            - int(sell_lg_vol)
            - int(sell_elg_vol)
        )
        net_mf_amount = quantize_amount(
            Decimal(buy_sm_amount)
            + Decimal(buy_md_amount)
            + Decimal(buy_lg_amount)
            + Decimal(buy_elg_amount)
            - Decimal(sell_sm_amount)
            - Decimal(sell_md_amount)
            - Decimal(sell_lg_amount)
            - Decimal(sell_elg_amount)
            + Decimal(index) / Decimal("1000")
        )

        rows.append(
            {
                "ts_code": daily_row.ts_code,
                "trade_date": daily_row.trade_date,
                "buy_sm_vol": buy_sm_vol,
                "buy_sm_amount": buy_sm_amount,
                "sell_sm_vol": sell_sm_vol,
                "sell_sm_amount": sell_sm_amount,
                "buy_md_vol": buy_md_vol,
                "buy_md_amount": buy_md_amount,
                "sell_md_vol": sell_md_vol,
                "sell_md_amount": sell_md_amount,
                "buy_lg_vol": buy_lg_vol,
                "buy_lg_amount": buy_lg_amount,
                "sell_lg_vol": sell_lg_vol,
                "sell_lg_amount": sell_lg_amount,
                "buy_elg_vol": buy_elg_vol,
                "buy_elg_amount": buy_elg_amount,
                "sell_elg_vol": sell_elg_vol,
                "sell_elg_amount": sell_elg_amount,
                "net_mf_vol": net_mf_vol,
                "net_mf_amount": net_mf_amount,
            }
        )
    return rows


def quantize_int(value: Decimal) -> str:
    return str(int(value.quantize(Decimal("1"))))


def quantize_amount(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.001")))
