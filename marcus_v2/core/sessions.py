"""Market-session and price-type classification (deterministic).

NYSE calendar for 2026-2027 is the same table the trader-* skills use
(nyse.com/markets/hours-calendars, read 2026-10-13). Years outside it are
reported as UNKNOWN rather than guessed.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .contracts import AssetClass, MarketSession, PriceType

NY = ZoneInfo("America/New_York")
NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
    "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
NYSE_EARLY_CLOSE = {"2026-11-27", "2026-12-24", "2027-11-26"}
CALENDAR_YEARS = {2026, 2027}
OPEN, CLOSE, EARLY = time(9, 30), time(16, 0), time(13, 0)
# A daily close is treated as official only this long after the closing bell.
FINALISE_AFTER = timedelta(minutes=20)


def is_trading_day(d: date) -> bool | None:
    if d.year not in CALENDAR_YEARS:
        return None
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def session_close(d: date) -> datetime:
    return datetime.combine(d, EARLY if d.isoformat() in NYSE_EARLY_CLOSE else CLOSE, NY)


def nyse_session(at: datetime) -> MarketSession:
    local = at.astimezone(NY)
    td = is_trading_day(local.date())
    if td is None:
        return MarketSession.UNKNOWN
    if not td:
        return MarketSession.CLOSED
    t = local.timetz().replace(tzinfo=None)
    close = session_close(local.date()).time()
    if t < OPEN:
        return MarketSession.PRE
    if t < close:
        return MarketSession.REGULAR
    return MarketSession.POST


def classify_daily_bar(bar_date: date, asset_class: AssetClass, now: datetime) -> PriceType:
    """Is this daily bar's close final, or still forming?"""
    now_ny = now.astimezone(NY)
    if bar_date > now_ny.date():
        return PriceType.UNSUPPORTED                   # bar from the future: impossible
    if asset_class in (AssetClass.COMMODITY, AssetClass.FX):
        # Near-24h markets (e.g. COMEX gold on Globex): today's bar is provisional until the
        # next day; settlement times differ from NYSE, so only prior days count as final.
        return PriceType.PROVISIONAL_INTRADAY if bar_date >= now_ny.date() else PriceType.HISTORICAL
    if bar_date < now_ny.date() and is_trading_day(bar_date) is None:
        # a completed past bar outside the encoded calendar years: final, but not "recent official"
        return PriceType.HISTORICAL
    if is_trading_day(bar_date) is None:
        return PriceType.UNSUPPORTED                   # today's bar in an unencoded year: cannot tell
    if bar_date < now_ny.date():
        return PriceType.OFFICIAL_CLOSE if bar_date >= now_ny.date() - timedelta(days=7) else PriceType.HISTORICAL
    if now_ny >= session_close(bar_date) + FINALISE_AFTER:
        return PriceType.OFFICIAL_CLOSE
    return PriceType.PROVISIONAL_INTRADAY


def last_completed_session(now: datetime) -> date | None:
    d = now.astimezone(NY).date()
    for _ in range(10):
        if is_trading_day(d) and now.astimezone(NY) >= session_close(d) + FINALISE_AFTER:
            return d
        d -= timedelta(days=1)
        if is_trading_day(d) is None:
            return None
    return None
