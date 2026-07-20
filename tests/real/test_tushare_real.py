from __future__ import annotations

import os
import unittest

from findata.providers.tushare import TushareClient, TushareHTTPTransport


@unittest.skipUnless(
    os.environ.get("FINDATA_ALLOW_REAL_API") == "1",
    "real API tests require explicit human approval via FINDATA_ALLOW_REAL_API=1",
)
class RealTushareContractTests(unittest.TestCase):
    def test_trade_calendar_contract(self) -> None:
        token = os.environ.get("TUSHARE_API_TOKEN")
        self.assertTrue(token, "TUSHARE_API_TOKEN is required after human approval")
        client = TushareClient(token=token or "", transport=TushareHTTPTransport())

        table = client.query(
            "tushare_trade_cal",
            exchange="SSE",
            start_date="20260720",
            end_date="20260720",
        )

        self.assertEqual(table.schema.names, ["exchange", "cal_date", "is_open", "pretrade_date"])


if __name__ == "__main__":
    unittest.main()
