"""NYSE trading-day calendar (full-day closures 2025-2027) and session helpers.

Early closes (1:00 p.m. ET) are not modelled: execution here is at the session
open, so an early close does not change a fill. Dates outside 2025-2027 raise,
rather than silently treating an unknown holiday as a trading day.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SGT = ZoneInfo("Asia/Singapore")

NYSE_HOLIDAYS = {
    # 2025
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
COVERED = (date(2025, 1, 1), date(2027, 12, 31))
OPEN, CLOSE = time(9, 30), time(16, 0)


class CalendarError(ValueError):
    pass


def _check(d: date) -> None:
    if not (COVERED[0] <= d <= COVERED[1]):
        raise CalendarError(f"{d} is outside the calendar's covered range {COVERED[0]}..{COVERED[1]}")


def is_trading_day(d: date) -> bool:
    _check(d)
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    while not is_trading_day(n):
        n += timedelta(days=1)
    return n


def previous_trading_day(d: date) -> date:
    p = d - timedelta(days=1)
    while not is_trading_day(p):
        p -= timedelta(days=1)
    return p


def trading_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def last_completed_session(now: datetime) -> date:
    """The latest session whose close has passed at *now* (any timezone-aware datetime)."""
    ny = now.astimezone(NY)
    d = ny.date()
    if is_trading_day(d) and ny.time() >= CLOSE:
        return d
    return previous_trading_day(d)


def market_session(now: datetime) -> str:
    ny = now.astimezone(NY)
    if not is_trading_day(ny.date()):
        return "CLOSED_HOLIDAY_OR_WEEKEND"
    if ny.time() < OPEN:
        return "PRE_OPEN"
    if ny.time() < CLOSE:
        return "OPEN"
    return "CLOSED_AFTER_HOURS"


def fmt_times(dt: datetime) -> str:
    """'2026-11-13 16:00 ET / 2026-11-14 05:00 SGT' — the owner is in Singapore."""
    return f"{dt.astimezone(NY):%Y-%m-%d %H:%M} ET / {dt.astimezone(SGT):%Y-%m-%d %H:%M} SGT"
