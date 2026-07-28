from __future__ import annotations

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from findata_plugins.tushare.shared.publication import (
    PublicationWindow,
    daily_window,
    monthly_window,
)


class PublicationWindowTests(unittest.TestCase):
    def test_daily_window_uses_shanghai_release_boundaries(self) -> None:
        zone = ZoneInfo("Asia/Shanghai")
        target = date(2026, 7, 20)
        self.assertEqual(
            daily_window(target, datetime(2026, 7, 20, 14, 59, tzinfo=zone)),
            PublicationWindow.BEFORE,
        )
        self.assertEqual(
            daily_window(target, datetime(2026, 7, 20, 15, 0, tzinfo=zone)),
            PublicationWindow.INSIDE,
        )
        self.assertEqual(
            daily_window(target, datetime(2026, 7, 20, 17, 0, tzinfo=zone)),
            PublicationWindow.AFTER,
        )
        self.assertEqual(
            daily_window(date(2026, 7, 19), datetime(2026, 7, 20, 8, 0, tzinfo=zone)),
            PublicationWindow.AFTER,
        )

    def test_monthly_window_distinguishes_current_and_future_month(self) -> None:
        now = datetime(2026, 7, 20, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(monthly_window(date(2026, 6, 1), now), PublicationWindow.AFTER)
        self.assertEqual(monthly_window(date(2026, 7, 1), now), PublicationWindow.INSIDE)
        self.assertEqual(monthly_window(date(2026, 8, 1), now), PublicationWindow.BEFORE)


if __name__ == "__main__":
    unittest.main()
