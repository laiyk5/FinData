from __future__ import annotations

import unittest
from datetime import date

import pyarrow as pa

from findata.contracts import DateRange, OperandError
from findata.datasets.tushare import TUSHARE_DATASETS


class DateRangeTests(unittest.TestCase):
    def test_parses_half_open_range_and_resolves_today_once(self) -> None:
        result = DateRange.parse("2026-06-01:today", today=date(2026, 7, 20))

        self.assertEqual(result.start, date(2026, 6, 1))
        self.assertEqual(result.end, date(2026, 7, 20))
        self.assertEqual(result.to_provider_inclusive(), ("20260601", "20260719"))

    def test_rejects_empty_and_reversed_ranges(self) -> None:
        for value in ("2026-06-01:2026-06-01", "2026-07-01:2026-06-01"):
            with self.subTest(value=value), self.assertRaises(OperandError):
                DateRange.parse(value, today=date(2026, 7, 20))

    def test_rejects_invalid_range_syntax(self) -> None:
        for value in ("", "2026-01-01", "20260101:20260201", "today:today:today"):
            with self.subTest(value=value), self.assertRaises(OperandError):
                DateRange.parse(value, today=date(2026, 7, 20))


class DatasetContractTests(unittest.TestCase):
    def test_registers_the_five_v1_tushare_datasets(self) -> None:
        self.assertEqual(
            set(TUSHARE_DATASETS),
            {
                "tushare_trade_cal",
                "tushare_stock_basic",
                "tushare_index_basic",
                "tushare_index_weight",
                "tushare_daily_basic",
            },
        )

    def test_trade_calendar_schema_normalizes_provider_values(self) -> None:
        spec = TUSHARE_DATASETS["tushare_trade_cal"]

        self.assertEqual(spec.api_name, "trade_cal")
        self.assertEqual(spec.primary_key, ("exchange", "cal_date"))
        self.assertEqual(spec.partition_key, "exchange")
        self.assertEqual(spec.time_field, "cal_date")
        self.assertEqual(
            spec.schema,
            pa.schema(
                [
                    pa.field("exchange", pa.string(), nullable=False),
                    pa.field("cal_date", pa.date32(), nullable=False),
                    pa.field("is_open", pa.bool_(), nullable=False),
                    pa.field("pretrade_date", pa.date32(), nullable=True),
                ]
            ),
        )

    def test_index_weight_adds_effective_month_to_provider_fields(self) -> None:
        spec = TUSHARE_DATASETS["tushare_index_weight"]

        self.assertEqual(spec.api_name, "index_weight")
        self.assertEqual(
            spec.primary_key,
            ("index_code", "effective_month", "con_code"),
        )
        self.assertEqual(spec.partition_key, "index_code")
        self.assertEqual(spec.secondary_key, "con_code")
        self.assertEqual(spec.time_field, "effective_month")
        self.assertEqual(spec.aliases, {})

    def test_index_basic_accepts_the_documented_output_without_symbol(self) -> None:
        spec = TUSHARE_DATASETS["tushare_index_basic"]
        fields = [
            "ts_code",
            "name",
            "fullname",
            "market",
            "publisher",
            "index_type",
            "category",
            "base_date",
            "base_point",
            "list_date",
            "weight_rule",
            "desc",
            "exp_date",
        ]

        table = spec.table_from_response(
            fields,
            [[
                "000300.SH",
                "沪深300",
                "沪深300指数",
                "CSI",
                "中证指数有限公司",
                "规模",
                "规模指数",
                "20041231",
                1000.0,
                "20050408",
                "派许加权",
                "沪深市场代表性指数",
                None,
            ]],
        )

        self.assertNotIn("symbol", spec.provider_fields)
        self.assertEqual(table.column("ts_code").to_pylist(), ["000300.SH"])
        self.assertEqual(table.column("base_date").to_pylist(), [date(2004, 12, 31)])

    def test_daily_basic_schema_matches_declared_logical_contract(self) -> None:
        spec = TUSHARE_DATASETS["tushare_daily_basic"]

        self.assertEqual(len(spec.schema), 19)
        self.assertEqual(spec.schema.field("trade_date").type, pa.date32())
        self.assertEqual(spec.schema.field("limit_status").type, pa.int8())
        self.assertEqual(spec.schema.field("pe").type, pa.float64())
        self.assertTrue(spec.schema.field("pe").nullable)
        self.assertEqual(spec.capabilities["symbol_set_cap"], 1)
        self.assertEqual(spec.capabilities["row_limit"], 6000)


if __name__ == "__main__":
    unittest.main()
