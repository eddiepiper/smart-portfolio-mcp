"""Checks before buying: three deterministic guards that can only hold a buy back.

They exist because of the November 2026 NVDA calls: the team said BUY on 17 and
18 Nov (about $156) with Merlin's fair value at $244, but Merlin's newest SEC
quarter was the July one, NVDA reported on 17 Nov, and the stock then fell
about 19% while the whole market sold off. None of these checks reads LLM
text, and none of them ever blocks a sale or a trim: getting out is always
allowed.

1. ``data_freshness`` (stale fundamentals). The company has published an
   earnings release (an SEC 8-K with item 2.02, "results of operations") that
   is newer than the latest 10-Q/10-K in our SEC numbers, or the latest quarter
   in the data is so old that a newer one must already be out. Merlin's
   valuation is then built on last quarter's numbers, so Merlin is capped at
   NEUTRAL and a buy becomes WAIT until the new quarter reaches SEC XBRL data.
2. ``earnings_window``. A report is due within ``pre_sessions`` sessions of the
   cutoff, or happened within ``post_sessions`` sessions before it. One report
   can move a stock several times its normal daily move, so a new buy then is
   mostly a bet on the report. This is the approved idea behind lesson
   L-20261119-earnings_window, enforced in code rather than only as advice in
   a prompt. Report dates come from the owner (config ``guards.earnings_dates``)
   or are estimated from the company's own 8-K history (same quarter a year
   earlier + 364 days, or the last report + 91 days).
3. ``market_regime``. The market (the benchmark plus any extra indices in the
   data, e.g. QQQ) is selling off hard (big one-day or five-day fall, or deep
   below its 20-session high), or the stock is falling much faster than the
   market over five sessions.

Each guard returns PASS, FLAG (shown, does not block) or BLOCK (a buy becomes
WAIT). Whether a firing guard blocks or only flags is set per guard in config.
Thresholds live in config (``guards``) with documented defaults; they are not
set by any model.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from merlin_quant.calendar import NY, OPEN, CalendarError, is_trading_day, next_trading_day, trading_days
from merlin_quant.data import DataUnavailable, PointInTimeView

RESULTS_8K = re.compile(r"\b8-K(/A)?\b.*\b2\.02\b")


@dataclass
class GuardResult:
    name: str
    outcome: str  # PASS | FLAG | BLOCK
    plain: str  # one plain-language sentence for the owner
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class GuardReport:
    instrument: str
    cutoff: date
    known_by: str
    mode: str  # FULL | LIMITED (no SEC financial history: no valuation, price and market checks only)
    results: list[GuardResult]

    @property
    def blocking(self) -> list[GuardResult]:
        return [r for r in self.results if r.outcome == "BLOCK"]

    @property
    def flagged(self) -> list[GuardResult]:
        return [r for r in self.results if r.outcome == "FLAG"]

    @property
    def stale_fundamentals(self) -> bool:
        return any(r.name == "data_freshness" and r.details.get("stale") for r in self.results)

    def wait_reason(self) -> str:
        return " ".join(r.plain for r in self.blocking)

    def as_dict(self) -> dict:
        return {"instrument": self.instrument, "cutoff": self.cutoff.isoformat(), "known_by": self.known_by,
                "mode": self.mode, "results": [r.as_dict() for r in self.results]}


def known_by_time(cutoff: date, now: datetime | None) -> datetime:
    """What a decision may know: the cutoff close, or later news up to *now* if *now* is before the next open.

    Guards only ever hold a buy back, so letting them see an 8-K filed after the close but before the decision
    is honest (the owner could read it too) and cannot leak future prices into a grade.
    """
    close = datetime.combine(cutoff, datetime.min.time().replace(hour=16), tzinfo=NY)
    if now is None:
        return close
    try:
        nxt = datetime.combine(next_trading_day(cutoff), OPEN, tzinfo=NY)
    except CalendarError:
        return close
    return max(close, min(now.astimezone(NY), nxt))


# ------------------------------------------------------------------ earnings dates

def results_releases(view: PointInTimeView, symbol: str, known_by: datetime) -> list[datetime]:
    """Earnings-release times: the company's 8-K filings with item 2.02, published by *known_by*."""
    out = []
    for n in view.md.news.get(symbol, []):
        if n.get("source") != "sec_edgar" or not RESULTS_8K.search(n.get("headline", "")):
            continue
        ts = datetime.fromisoformat(n["published"])
        if ts <= known_by:
            out.append(ts.astimezone(NY))
    return sorted(out)


def _first_reaction_session(ts: datetime) -> date:
    """The first session that can trade on a release: the same day if before the open, else the next session."""
    d = ts.astimezone(NY).date()
    try:
        if ts.astimezone(NY).time() < OPEN and is_trading_day(d):
            return d
        return next_trading_day(d)
    except CalendarError:
        return d


def next_report(view: PointInTimeView, symbol: str, releases: list[datetime], owner_dates: list[str],
                min_gap_days: int) -> tuple[date | None, str]:
    """Next expected report date after the cutoff, and where it came from ('owner' or 'estimated')."""
    cutoff = view.cutoff
    future = sorted(date.fromisoformat(d) for d in owner_dates if date.fromisoformat(d) > cutoff)
    if future:
        return future[0], "owner"
    if not releases:
        return None, "unknown"
    last = releases[-1].date()
    cands = [r.date() + timedelta(days=364) for r in releases] + [last + timedelta(days=91)]
    cands = [c for c in cands if c > max(cutoff, last + timedelta(days=min_gap_days))]
    return (min(cands), "estimated") if cands else (None, "unknown")


def sessions_between(a: date, b: date) -> int:
    """Trading sessions after *a* up to and including *b* (0 if b <= a)."""
    if b <= a:
        return 0
    try:
        return len(trading_days(a + timedelta(days=1), b))
    except CalendarError:
        return (b - a).days * 5 // 7


# ------------------------------------------------------------------ the guards

def _outcome(fired: bool, action: str) -> str:
    return ("BLOCK" if action == "block" else "FLAG") if fired else "PASS"


def check_data_freshness(view: PointInTimeView, symbol: str, gcfg, known_by: datetime) -> GuardResult:
    fs = view.filings(symbol)
    if len(fs) < 4:
        return GuardResult("data_freshness", _outcome(True, gcfg.limited_mode_action),
                           f"LIMITED MODE: {symbol} has {len(fs)} quarter(s) of SEC financials (a new listing or a "
                           "company we have no filings for), so there is no valuation behind this call; only price "
                           "and market checks apply.", {"stale": False, "limited": True, "quarters": len(fs)})
    latest = fs[-1]
    pe, filed = date.fromisoformat(latest["period_end"]), date.fromisoformat(latest["filed"])
    age = (view.cutoff - pe).days
    newer = [r for r in results_releases(view, symbol, known_by) if r.date() > filed]
    details = {"latest_quarter_end": pe.isoformat(), "latest_quarter_filed": filed.isoformat(),
               "quarter_age_days": age, "max_quarter_age_days": gcfg.max_quarter_age_days,
               "results_released_since": [r.isoformat() for r in newer], "limited": False}
    if newer:
        details["stale"] = True
        return GuardResult("data_freshness", _outcome(True, gcfg.stale_data_action),
                           f"WAIT: {symbol} released new quarterly results on {newer[-1]:%d %b %Y} but our SEC numbers "
                           f"still stop at the quarter ending {pe:%d %b %Y}, so the valuation is built on old numbers. "
                           "Wait until the new 10-Q/10-K is in the SEC data (re-run fetch-data).", details)
    if age > gcfg.max_quarter_age_days:
        details["stale"] = True
        return GuardResult("data_freshness", _outcome(True, gcfg.stale_data_action),
                           f"WAIT: the newest quarter in our SEC data ended {pe:%d %b %Y}, {age} days before the "
                           f"cutoff (limit {gcfg.max_quarter_age_days}); a newer quarter should already be out.",
                           details)
    details["stale"] = False
    return GuardResult("data_freshness", "PASS", f"SEC numbers are current (latest quarter ended {pe:%d %b %Y}, "
                       f"filed {filed:%d %b %Y}; no newer results release).", details)


def check_earnings_window(view: PointInTimeView, symbol: str, gcfg, known_by: datetime) -> GuardResult:
    ew = gcfg.earnings_window
    rel = results_releases(view, symbol, known_by)
    owner = list(gcfg.earnings_dates.get(symbol, []))
    nxt, how = next_report(view, symbol, rel, owner, ew.min_gap_days)
    details = {"pre_sessions": ew.pre_sessions, "post_sessions": ew.post_sessions,
               "next_report": nxt.isoformat() if nxt else None, "next_report_source": how,
               "lesson": "L-20261119-earnings_window"}
    # most recent report on or before the cutoff (8-K, or an owner date that has passed)
    past = [_first_reaction_session(r) for r in rel]
    past += [date.fromisoformat(d) for d in owner if date.fromisoformat(d) <= view.cutoff]
    past = sorted(p for p in past if p <= next_trading_day(view.cutoff))
    if past:
        since = sessions_between(past[-1], view.cutoff)
        details.update(last_report_session=past[-1].isoformat(), sessions_since_report=since)
        if past[-1] > view.cutoff or since < ew.post_sessions:
            return GuardResult("earnings_window", _outcome(True, ew.action),
                               f"WAIT: {symbol} reported results very recently (first trading session "
                               f"{past[-1]:%d %b %Y}); the price is still reacting, so no new buy for "
                               f"{ew.post_sessions} session(s) after a report.", details)
    if nxt is None:
        if rel or owner:
            return GuardResult("earnings_window", "PASS", "No report expected soon.", details)
        return GuardResult("earnings_window", "FLAG", f"{symbol} has no earnings history in the data, so its first "
                           "report date is unknown; add it to guards.earnings_dates when it is announced.", details)
    until = sessions_between(view.cutoff, nxt)
    details["sessions_until_report"] = until
    if until <= ew.pre_sessions:
        return GuardResult("earnings_window", _outcome(True, ew.action),
                           f"WAIT: {symbol} is due to report around {nxt:%d %b %Y} ({how} date), {until} trading "
                           f"session(s) after the cutoff. A buy now is mostly a bet on that one report.", details)
    return GuardResult("earnings_window", "PASS", f"Next report around {nxt:%d %b %Y} ({how}), {until} sessions "
                       "away.", details)


def _ret(closes: list[float], n: int) -> float | None:
    return closes[-1] / closes[-1 - n] - 1 if len(closes) > n else None


def check_market_regime(view: PointInTimeView, symbol: str, benchmark: str, gcfg) -> GuardResult:
    mc = gcfg.market
    hits, details, missing = [], {"thresholds": mc.model_dump()}, []
    for idx in [benchmark, *[i for i in mc.extra_indices if i != benchmark]]:
        try:
            closes = [b["close"] for b in view.bars(idx)]
        except DataUnavailable:
            missing.append(idx)
            continue
        r1, r5 = _ret(closes, 1), _ret(closes, 5)
        dd = closes[-1] / max(closes[-mc.drawdown_lookback_sessions:]) - 1
        details[idx] = {"1d": r1, "5d": r5, f"from_{mc.drawdown_lookback_sessions}d_high": round(dd, 4)}
        if r1 is not None and r1 <= -mc.index_1d_drop:
            hits.append(f"{idx} fell {-r1:.1%} in one session (limit {mc.index_1d_drop:.1%})")
        if r5 is not None and r5 <= -mc.index_5d_drop:
            hits.append(f"{idx} fell {-r5:.1%} over five sessions (limit {mc.index_5d_drop:.1%})")
        if dd <= -mc.index_drawdown:
            hits.append(f"{idx} is {-dd:.1%} below its {mc.drawdown_lookback_sessions}-session high "
                        f"(limit {mc.index_drawdown:.1%})")
    if missing:
        details["indices_not_in_data"] = missing
    rel_hit = None
    try:
        s = [b["close"] for b in view.bars(symbol)]
        bm = [b["close"] for b in view.bars(benchmark)]
        rs, rb = _ret(s, 5), _ret(bm, 5)
        if rs is not None and rb is not None:
            details["stock_vs_market_5d"] = round(rs - rb, 4)
            if rs - rb <= -mc.stock_vs_market_5d:
                rel_hit = (f"{symbol} did {rb - rs:.1%} worse than {benchmark} over five sessions "
                           f"(limit {mc.stock_vs_market_5d:.0%}): it is falling much faster than the market")
        else:
            details["stock_vs_market_5d"] = None
            details["note"] = f"{symbol} has fewer than six sessions of prices; relative check skipped"
    except DataUnavailable:
        details["stock_vs_market_5d"] = None
    if hits:
        return GuardResult("market_regime", _outcome(True, mc.action),
                           "WAIT: the market is selling off hard: " + "; ".join(hits) + ". New buys wait for it to "
                           "calm down.", details)
    if rel_hit:
        return GuardResult("market_regime", _outcome(True, mc.relative_action), "WAIT: " + rel_hit + ".", details)
    return GuardResult("market_regime", "PASS", f"No market-wide sell-off ({benchmark} within limits) and "
                       f"{symbol} is not falling much faster than the market.", details)


def evaluate(view: PointInTimeView, symbol: str, cfg, now: datetime | None = None) -> GuardReport:
    """Run every guard for a possible new buy of *symbol* at *view*'s cutoff."""
    g = cfg.guards
    kb = known_by_time(view.cutoff, now)
    fresh = check_data_freshness(view, symbol, g, kb)
    results = [fresh, check_earnings_window(view, symbol, g, kb),
               check_market_regime(view, symbol, cfg.portfolio.benchmark, g)]
    if not g.enabled:
        results = [GuardResult(r.name, "FLAG" if r.outcome != "PASS" else "PASS", "(guards switched off) " + r.plain,
                               r.details) for r in results]
    return GuardReport(instrument=symbol, cutoff=view.cutoff, known_by=kb.isoformat(),
                       mode="LIMITED" if fresh.details.get("limited") else "FULL", results=results)
