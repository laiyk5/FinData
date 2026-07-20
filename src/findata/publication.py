from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum
from zoneinfo import ZoneInfo


class PublicationWindow(str, Enum):
    BEFORE = "before-window"
    INSIDE = "inside-window"
    AFTER = "after-window"


SHANGHAI = ZoneInfo("Asia/Shanghai")


def daily_window(target: date, now: datetime) -> PublicationWindow:
    local = _aware(now).astimezone(SHANGHAI)
    if target < local.date():
        return PublicationWindow.AFTER
    if target > local.date():
        return PublicationWindow.BEFORE
    if local.timetz().replace(tzinfo=None) < time(15, 0):
        return PublicationWindow.BEFORE
    if local.timetz().replace(tzinfo=None) < time(17, 0):
        return PublicationWindow.INSIDE
    return PublicationWindow.AFTER


def monthly_window(month: date, now: datetime) -> PublicationWindow:
    local = _aware(now).astimezone(SHANGHAI)
    target = (month.year, month.month)
    current = (local.year, local.month)
    if target < current:
        return PublicationWindow.AFTER
    if target > current:
        return PublicationWindow.BEFORE
    return PublicationWindow.INSIDE


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("publication-window time must be timezone-aware")
    return value
