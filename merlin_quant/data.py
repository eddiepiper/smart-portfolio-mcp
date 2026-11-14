"""Market data with point-in-time enforcement.

`MarketData` holds a dataset (unadjusted daily bars, corporate actions, filings
with their filing dates, timestamped news, halts). Research code never reads it
directly: it asks for `view(cutoff)`, a `PointInTimeView` that only returns what
was knowable at the cutoff session's close:

- bars dated <= cutoff, split-adjusted only for splits whose ex-date <= cutoff;
- filings whose *filing date* <= cutoff (not their period end);
- news published before the cutoff session's close (16:00 ET);
- corporate actions with ex-date <= cutoff.

Only the execution engine reads a bar after a decision's cutoff, and only for
the fill session, which must be strictly later than the cutoff.

Every dataset names its source. The built-in one is `SYNTHETIC_FIXTURE`:
deterministic, generated from fixed seeds, and labelled synthetic everywhere it
appears. It is test data, not market history.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from merlin_quant.calendar import NY, previous_trading_day, trading_days


class DataUnavailable(RuntimeError):
    """The data source could not be reached or returned nothing."""


class StaleData(RuntimeError):
    pass


class LookAheadError(RuntimeError):
    pass


@dataclass
class MarketData:
    source_name: str
    source_kind: str  # "synthetic_fixture" | "market_data"
    currency: str
    bars: dict[str, list[dict]]
    actions: dict[str, list[dict]] = field(default_factory=dict)
    filings: dict[str, list[dict]] = field(default_factory=dict)
    news: dict[str, list[dict]] = field(default_factory=dict)
    profiles: dict[str, dict] = field(default_factory=dict)
    halts: dict[str, list[str]] = field(default_factory=dict)
    unavailable: bool = False

    # -------------------------------------------------------------- access
    def _require(self):
        if self.unavailable:
            raise DataUnavailable(f"{self.source_name}: data source unavailable")

    def view(self, cutoff: date) -> "PointInTimeView":
        self._require()
        return PointInTimeView(self, cutoff)

    def execution_bar(self, symbol: str, session: date, decision_cutoff: date) -> dict:
        """The fill session's raw bar. Refuses any session not after the decision cutoff."""
        self._require()
        if session <= decision_cutoff:
            raise LookAheadError(f"fill session {session} must be after the decision cutoff {decision_cutoff}")
        for b in self.bars.get(symbol, []):
            if b["date"] == session.isoformat():
                return b
        raise DataUnavailable(f"no {symbol} bar for session {session} yet")

    def raw_close(self, symbol: str, d: date) -> tuple[float, date] | None:
        """Latest unadjusted close on or before d (for marking the book)."""
        self._require()
        best = None
        for b in self.bars.get(symbol, []):
            if b["date"] <= d.isoformat():
                best = b
        return (best["close"], date.fromisoformat(best["date"])) if best else None

    def digest(self) -> str:
        blob = json.dumps({"bars": self.bars, "actions": self.actions, "filings": self.filings,
                           "news": self.news}, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    # -------------------------------------------------------------- io
    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.__dict__, indent=1, default=str))

    @classmethod
    def load(cls, path: Path) -> "MarketData":
        return cls(**json.loads(Path(path).read_text()))


class PointInTimeView:
    def __init__(self, md: MarketData, cutoff: date):
        self.md, self.cutoff = md, cutoff
        self._close_ts = datetime.combine(cutoff, datetime.min.time().replace(hour=16), tzinfo=NY)

    @property
    def source(self) -> str:
        return self.md.source_name

    def symbols(self) -> list[str]:
        return sorted(self.md.bars)

    def profile(self, symbol: str) -> dict:
        return dict(self.md.profiles.get(symbol, {}))

    def actions(self, symbol: str) -> list[dict]:
        return [a for a in self.md.actions.get(symbol, []) if a["ex_date"] <= self.cutoff.isoformat()]

    def bars(self, symbol: str, adjusted: bool = True) -> list[dict]:
        rows = [dict(b) for b in self.md.bars.get(symbol, []) if b["date"] <= self.cutoff.isoformat()]
        if not rows:
            raise DataUnavailable(f"no {symbol} bars on or before {self.cutoff}")
        if adjusted:
            for a in self.actions(symbol):
                if a["type"] != "split":
                    continue
                for r in rows:
                    if r["date"] < a["ex_date"]:
                        for k in ("open", "high", "low", "close"):
                            r[k] = r[k] / a["ratio"]
                        r["volume"] = r["volume"] * a["ratio"]
        return rows

    def close(self, symbol: str, max_staleness_sessions: int = 0) -> tuple[float, date]:
        """Last close at the cutoff, refusing stale prices.

        A price is stale when its date is older than the last completed session
        at the cutoff by more than *max_staleness_sessions* sessions.
        """
        last = self.bars(symbol)[-1]
        d = date.fromisoformat(last["date"])
        expected = self.cutoff
        lag, probe = 0, expected
        while probe > d:
            probe = previous_trading_day(probe)
            lag += 1
        if lag > max_staleness_sessions:
            raise StaleData(f"{symbol}: last close {d} is {lag} session(s) older than {expected}")
        return last["close"], d

    def filings(self, symbol: str) -> list[dict]:
        return [f for f in self.md.filings.get(symbol, []) if f["filed"] <= self.cutoff.isoformat()]

    def ttm(self, symbol: str) -> dict:
        fs = self.filings(symbol)
        if len(fs) < 4:
            raise DataUnavailable(f"{symbol}: fewer than four filed quarters at {self.cutoff}")
        last4 = fs[-4:]
        out = {k: sum(f[k] for f in last4) for k in
               ("revenue", "gross_profit", "operating_income", "net_income", "fcf")}
        out.update({k: last4[-1][k] for k in ("diluted_shares", "cash", "debt")})
        out["latest_period_end"] = last4[-1]["period_end"]
        out["latest_filed"] = last4[-1]["filed"]
        if len(fs) >= 8:
            prev = fs[-8:-4]
            out["revenue_prior_ttm"] = sum(f["revenue"] for f in prev)
        return out

    def news(self, symbol: str, days: int = 14) -> list[dict]:
        start = self._close_ts - timedelta(days=days)
        out = []
        for n in self.md.news.get(symbol, []):
            ts = datetime.fromisoformat(n["published"])
            if start <= ts <= self._close_ts:
                out.append(n)
        return out

    def is_halted(self, symbol: str, d: date) -> bool:
        return d.isoformat() in self.md.halts.get(symbol, [])


# ====================================================================== fixtures

FIXTURE_START, FIXTURE_END = date(2025, 1, 2), date(2026, 11, 13)

# (start price, [(segment end, annual drift)], daily vol, avg volume, seed)
_PATHS = {
    "AAPL": (170.0, [("2025-12-31", 0.10), ("2026-08-31", 0.08), ("2026-11-13", 0.35)], 0.011, 55e6, 13),
    "MSFT": (420.0, [("2025-12-31", 0.08), ("2026-11-13", 0.06)], 0.011, 22e6, 20),
    "NVDA": (135.0, [("2026-06-30", 0.65), ("2026-09-15", 0.10), ("2026-11-13", -1.6)], 0.024, 240e6, 13),
    "SPY": (590.0, [("2026-11-13", 0.09)], 0.008, 70e6, 14),
}
# quarterly revenue (billions) at the first quarter, quarterly growth, margins, shares (bn), cash, debt
_FUND = {
    "AAPL": dict(rev0=210.0, g=0.015, gm=0.46, om=0.31, nm=0.25, fcf=0.27, sh=15.0, cash=65.0, debt=95.0, seed=21),
    "MSFT": dict(rev0=102.0, g=0.030, gm=0.69, om=0.45, nm=0.36, fcf=0.30, sh=7.45, cash=75.0, debt=45.0, seed=22),
    "NVDA": dict(rev0=68.0, g=0.090, gm=0.73, om=0.60, nm=0.53, fcf=0.48, sh=24.5, cash=55.0, debt=8.5, seed=23),
}
_PROFILES = {
    "AAPL": dict(name="Apple Inc. (synthetic fixture)", sector="Technology Hardware", exchange="NASDAQ"),
    "MSFT": dict(name="Microsoft Corp. (synthetic fixture)", sector="Software", exchange="NASDAQ"),
    "NVDA": dict(name="NVIDIA Corp. (synthetic fixture)", sector="Semiconductors", exchange="NASDAQ"),
    "SPY": dict(name="S&P 500 ETF (synthetic fixture, benchmark)", sector="Index ETF", exchange="NYSE Arca"),
}
_NEWS = {
    "AAPL": [("2026-10-30T16:35:00-04:00", "[SYNTHETIC] Apple-like fixture co. reports services revenue up double digits; guides in line"),
             ("2026-11-05T09:10:00-05:00", "[SYNTHETIC] Supplier checks point to steady handset builds into the holiday quarter"),
             ("2026-11-12T12:00:00-05:00", "[SYNTHETIC] Analyst raises target on the fixture co. citing margin resilience")],
    "MSFT": [("2026-10-28T16:20:00-04:00", "[SYNTHETIC] Cloud growth decelerates slightly; capex guidance raised"),
             ("2026-11-10T08:00:00-05:00", "[SYNTHETIC] Enterprise AI seat pricing debate weighs on software names")],
    "NVDA": [("2026-10-22T07:30:00-04:00", "[SYNTHETIC] Reports of hyperscaler order push-outs for next-gen accelerators"),
             ("2026-11-04T10:15:00-05:00", "[SYNTHETIC] Export-licence uncertainty returns for advanced chips"),
             ("2026-11-11T15:00:00-05:00", "[SYNTHETIC] Heavy options put volume as shares break below the 200-day average"),
             ("2026-11-20T16:30:00-05:00", "[SYNTHETIC] FUTURE ITEM: results beat (published after the demo cutoff, must never be seen)")],
    "SPY": [],
}


def _gen_bars(symbol: str) -> list[dict]:
    p0, segments, vol, adv, seed = _PATHS[symbol]
    rng = random.Random(seed)
    days = trading_days(FIXTURE_START, FIXTURE_END)
    price, out, seg_i = p0, [], 0
    for d in days:
        while d.isoformat() > segments[seg_i][0]:
            seg_i += 1
        drift = segments[seg_i][1] / 252
        ret = drift + vol * rng.gauss(0, 1)
        o = price * (1 + vol * 0.3 * rng.gauss(0, 1))
        c = price * math.exp(ret)
        h = max(o, c) * (1 + abs(rng.gauss(0, vol * 0.5)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol * 0.5)))
        v = int(adv * math.exp(rng.gauss(0, 0.25)))
        out.append(dict(date=d.isoformat(), open=round(o, 2), high=round(h, 2), low=round(lo, 2),
                        close=round(c, 2), volume=v))
        price = c
    return out


def _apply_split_to_raw(bars: list[dict], ex_date: str, ratio: float) -> None:
    """Fixture bars are generated as one continuous series; a split makes the raw
    (unadjusted) prices on and after the ex-date 1/ratio of the generated path."""
    for b in bars:
        if b["date"] >= ex_date:
            for k in ("open", "high", "low", "close"):
                b[k] = round(b[k] / ratio, 2)
            b["volume"] = int(b["volume"] * ratio)


def _gen_filings(symbol: str) -> list[dict]:
    f = _FUND[symbol]
    rng = random.Random(f["seed"])
    quarter_ends = ["2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30",
                    "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]
    rows, rev = [], f["rev0"]
    for i, pe in enumerate(quarter_ends):
        rev = rev * (1 + f["g"] + rng.gauss(0, 0.01)) if i else rev
        filed = (date.fromisoformat(pe) + timedelta(days=33)).isoformat()
        rows.append(dict(period_end=pe, filed=filed, revenue=round(rev, 3),
                         gross_profit=round(rev * (f["gm"] + rng.gauss(0, 0.005)), 3),
                         operating_income=round(rev * (f["om"] + rng.gauss(0, 0.006)), 3),
                         net_income=round(rev * (f["nm"] + rng.gauss(0, 0.006)), 3),
                         fcf=round(rev * (f["fcf"] + rng.gauss(0, 0.01)), 3),
                         diluted_shares=f["sh"] * (4 if symbol == "NVDA" and pe >= "2026-06-30" else 1),
                         cash=f["cash"], debt=f["debt"], units="USD bn; shares bn"))
    return rows


def synthetic_fixture(unavailable: bool = False) -> MarketData:
    """The deterministic synthetic dataset used by tests and the demo.

    Contains, on purpose: a 4:1 NVDA split (ex 2026-06-10), quarterly AAPL/MSFT
    dividends, an NVDA news item published after the demo cutoff, and a filing
    filed after its period end, so look-ahead and corporate-action handling are
    exercised.
    """
    bars = {s: _gen_bars(s) for s in _PATHS}
    _apply_split_to_raw(bars["NVDA"], "2026-06-10", 4.0)
    actions = {
        "NVDA": [dict(type="split", ex_date="2026-06-10", ratio=4.0)],
        "AAPL": [dict(type="dividend", ex_date=d, pay_date=p, amount=0.26) for d, p in
                 [("2026-02-09", "2026-02-12"), ("2026-05-11", "2026-05-14"), ("2026-08-10", "2026-08-13"),
                  ("2026-11-09", "2026-11-12")]],
        "MSFT": [dict(type="dividend", ex_date=d, pay_date=p, amount=0.83) for d, p in
                 [("2026-02-19", "2026-03-12"), ("2026-05-21", "2026-06-11"), ("2026-08-20", "2026-09-10"),
                  ("2026-11-19", "2026-12-10")]],
    }
    news = {s: [dict(published=t, headline=h, source="synthetic_fixture") for t, h in items]
            for s, items in _NEWS.items()}
    profiles = {s: dict(p, currency="USD") for s, p in _PROFILES.items()}
    return MarketData(
        source_name="SYNTHETIC_FIXTURE v1 (deterministic, seeded; not market history)",
        source_kind="synthetic_fixture", currency="USD", bars=bars, actions=actions,
        filings={s: _gen_filings(s) for s in _FUND}, news=news, profiles=profiles,
        halts={"MSFT": ["2026-11-12"]}, unavailable=unavailable,
    )
