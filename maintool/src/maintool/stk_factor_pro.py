from __future__ import annotations

from decimal import Decimal

from .tushare_daily import fake_daily_rows
from .tushare_daily_basic import fake_daily_basic_rows


FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "open_hfq",
    "open_qfq",
    "high",
    "high_hfq",
    "high_qfq",
    "low",
    "low_hfq",
    "low_qfq",
    "close",
    "close_hfq",
    "close_qfq",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
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
    "adj_factor",
    "ma_bfq_5",
    "ma_bfq_10",
    "ma_bfq_20",
    "ema_bfq_5",
    "ema_bfq_10",
    "ema_bfq_20",
    "macd_bfq",
    "macd_dea_bfq",
    "macd_dif_bfq",
    "boll_lower_bfq",
    "boll_mid_bfq",
    "boll_upper_bfq",
    "dmi_adx_bfq",
    "dmi_adxr_bfq",
    "dmi_mdi_bfq",
    "dmi_pdi_bfq",
    "kdj_bfq",
    "kdj_d_bfq",
    "kdj_k_bfq",
    "bias1_bfq",
    "bias2_bfq",
    "bias3_bfq",
    "atr_bfq",
)


def fake_stk_factor_pro_rows(symbols: list[str], trade_date: str) -> list[dict[str, str]]:
    daily_rows = fake_daily_rows(symbols, trade_date)
    daily_basic_rows = fake_daily_basic_rows(symbols, trade_date)
    rows: list[dict[str, str]] = []

    for index, (daily_row, daily_basic_row) in enumerate(zip(daily_rows, daily_basic_rows)):
        base_close = Decimal(daily_row.close)
        base_open = Decimal(daily_row.open)
        base_high = Decimal(daily_row.high)
        base_low = Decimal(daily_row.low)
        pre_close = (base_open - Decimal("0.05")).quantize(Decimal("0.001"))
        change = (base_close - pre_close).quantize(Decimal("0.001"))
        pct_chg = (change / pre_close * Decimal("100")).quantize(Decimal("0.0001"))
        adj_factor = (Decimal("1.000") + Decimal(index) / Decimal("1000")).quantize(Decimal("0.001"))

        open_hfq = quantize_price(base_open * (Decimal("1.05") + Decimal(index) / Decimal("10000")))
        open_qfq = quantize_price(base_open * (Decimal("0.95") + Decimal(index) / Decimal("10000")))
        high_hfq = quantize_price(base_high * (Decimal("1.05") + Decimal(index) / Decimal("10000")))
        high_qfq = quantize_price(base_high * (Decimal("0.95") + Decimal(index) / Decimal("10000")))
        low_hfq = quantize_price(base_low * (Decimal("1.05") + Decimal(index) / Decimal("10000")))
        low_qfq = quantize_price(base_low * (Decimal("0.95") + Decimal(index) / Decimal("10000")))
        close_hfq = quantize_price(base_close * (Decimal("1.05") + Decimal(index) / Decimal("10000")))
        close_qfq = quantize_price(base_close * (Decimal("0.95") + Decimal(index) / Decimal("10000")))

        rows.append(
            {
                **daily_row.as_dict(),
                **daily_basic_row.as_dict(),
                "open_hfq": open_hfq,
                "open_qfq": open_qfq,
                "high_hfq": high_hfq,
                "high_qfq": high_qfq,
                "low_hfq": low_hfq,
                "low_qfq": low_qfq,
                "close_hfq": close_hfq,
                "close_qfq": close_qfq,
                "pre_close": str(pre_close),
                "change": str(change),
                "pct_chg": str(pct_chg),
                "adj_factor": str(adj_factor),
                "ma_bfq_5": quantize_signed(base_close - Decimal("0.20")),
                "ma_bfq_10": quantize_signed(base_close - Decimal("0.10")),
                "ma_bfq_20": quantize_signed(base_close + Decimal("0.05")),
                "ema_bfq_5": quantize_signed(base_close - Decimal("0.12")),
                "ema_bfq_10": quantize_signed(base_close - Decimal("0.06")),
                "ema_bfq_20": quantize_signed(base_close + Decimal("0.08")),
                "macd_bfq": quantize_signed(Decimal("0.12") + Decimal(index) / Decimal("1000")),
                "macd_dea_bfq": quantize_signed(Decimal("0.08") + Decimal(index) / Decimal("1200")),
                "macd_dif_bfq": quantize_signed(Decimal("0.04") + Decimal(index) / Decimal("1500")),
                "boll_lower_bfq": quantize_price(base_close - Decimal("1.00")),
                "boll_mid_bfq": quantize_price(base_close),
                "boll_upper_bfq": quantize_price(base_close + Decimal("1.00")),
                "dmi_adx_bfq": quantize_signed(Decimal("15.0") + Decimal(index) / Decimal("50")),
                "dmi_adxr_bfq": quantize_signed(Decimal("14.0") + Decimal(index) / Decimal("55")),
                "dmi_mdi_bfq": quantize_signed(Decimal("10.0") + Decimal(index) / Decimal("60")),
                "dmi_pdi_bfq": quantize_signed(Decimal("12.0") + Decimal(index) / Decimal("65")),
                "kdj_bfq": quantize_signed(Decimal("45.0") + Decimal(index) / Decimal("30")),
                "kdj_d_bfq": quantize_signed(Decimal("40.0") + Decimal(index) / Decimal("35")),
                "kdj_k_bfq": quantize_signed(Decimal("50.0") + Decimal(index) / Decimal("25")),
                "bias1_bfq": quantize_signed(Decimal("1.2") + Decimal(index) / Decimal("200")),
                "bias2_bfq": quantize_signed(Decimal("-0.6") + Decimal(index) / Decimal("300")),
                "bias3_bfq": quantize_signed(Decimal("0.3") + Decimal(index) / Decimal("400")),
                "atr_bfq": quantize_signed(Decimal("0.8") + Decimal(index) / Decimal("500")),
            }
        )

    return rows


def quantize_price(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.001")))


def quantize_signed(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.001")))
