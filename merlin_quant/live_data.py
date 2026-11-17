"""Build a real `MarketData` file from free public sources. PAPER ONLY.

Sources (no paid service, no API key):

- **Yahoo Finance chart JSON** (query1, then query2): daily OHLCV with split and
  dividend events. Yahoo's chart prices are split-adjusted; this module turns
  them back into *raw* (unadjusted) bars plus split actions, which is what
  `MarketData` stores and what `PointInTimeView` re-adjusts point in time.
- **Yahoo Finance search JSON**: recent headlines with their publish times.
- **SEC EDGAR** (`company_tickers.json`, `submissions`, XBRL `companyfacts`):
  quarterly fundamentals and 8-K filings. SEC requires a User-Agent with a real
  name and email: pass `--sec-user-agent` or set `SEC_USER_AGENT`.

Point-in-time rules applied here (the view enforces the rest):

- every quarterly figure is the value *first reported*, dated by the filing
  that first reported it; later restatements never overwrite history;
- a fourth quarter derived from a 10-K (annual minus three quarters) is dated
  by the 10-K's filing date; cash-flow quarters are de-cumulated from
  year-to-date figures;
- news keeps its publish time, 8-Ks their SEC acceptance time;
- today's bar is dropped until the session has closed.

Re-running into an existing file keeps the headlines it already holds, so the
news history grows as you fetch (Yahoo only serves recent items).

From the Claude cloud container these hosts are blocked by the network policy:
run `fetch-data` on a machine with internet access (for example your Mac).
"""

from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from merlin_quant.calendar import CLOSE, COVERED, NY, is_trading_day
from merlin_quant.data import DataUnavailable, MarketData

YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
YAHOO_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) merlin-quant-paper/1.0"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SOURCE_NAME = "LIVE_PUBLIC v1 (Yahoo Finance chart/search, SEC EDGAR XBRL + submissions)"

BN = 1e9
FLOW_CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                "RevenuesNetOfInterestExpense"],
    "cost_of_revenue": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
}
SHARE_CONCEPTS = ["WeightedAverageNumberOfDilutedSharesOutstanding"]
CASH_CONCEPTS = ["CashAndCashEquivalentsAtCarryingValue",
                 "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
DEBT_PARTS = (["LongTermDebtNoncurrent", "LongTermDebt"], ["LongTermDebtCurrent", "DebtCurrent"])
REPORT_FORMS = ("10-Q", "10-K", "20-F", "40-F")
EIGHT_K_ITEMS = {"1.01": "material agreement", "1.03": "bankruptcy", "2.01": "acquisition/disposal",
                 "2.02": "results of operations", "2.05": "restructuring", "2.06": "impairment",
                 "4.02": "non-reliance on prior financials", "5.02": "officer/director change",
                 "7.01": "Reg FD disclosure", "8.01": "other events"}


class FetchError(DataUnavailable):
    """A public data source could not be reached or returned something unusable."""


# ---------------------------------------------------------------- http

class Http:
    """Tiny JSON-over-HTTPS client with polite pacing. Tests replace `get_json`."""

    def __init__(self, sec_user_agent: str | None = None, pause: float = 0.15, timeout: float = 30):
        self.sec_ua, self.pause, self.timeout, self._last = sec_user_agent, pause, timeout, 0.0

    def get_json(self, url: str) -> dict | list:
        host = urllib.parse.urlparse(url).hostname or ""
        if host.endswith("sec.gov"):
            if not self.sec_ua:
                raise FetchError("SEC requires a User-Agent with your name and email: pass --sec-user-agent "
                                 "'Your Name you@example.com' or set SEC_USER_AGENT")
            ua = self.sec_ua
        else:
            ua = YAHOO_UA
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json",
                                                   "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
        except urllib.error.HTTPError as exc:
            raise FetchError(f"HTTP {exc.code} from {host}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FetchError(f"could not reach {host}: {getattr(exc, 'reason', exc)}") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise FetchError(f"{host} returned non-JSON") from exc


def _yahoo(http: Http, path: str) -> dict:
    last: Exception | None = None
    for host in YAHOO_HOSTS:
        try:
            return http.get_json(f"https://{host}{path}")
        except FetchError as exc:
            last = exc
    raise FetchError(f"Yahoo unavailable: {last}")


# ---------------------------------------------------------------- prices

def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def fetch_bars(http: Http, symbol: str, start: date, end: date, now: datetime) -> tuple[list[dict], list[dict], dict]:
    """Raw daily bars, split/dividend actions and Yahoo's instrument meta for one symbol."""
    q = urllib.parse.urlencode({"period1": _epoch(start), "period2": _epoch(end + timedelta(days=1)),
                                "interval": "1d", "events": "div|split", "includePrePost": "false"})
    data = _yahoo(http, f"/v8/finance/chart/{urllib.parse.quote(symbol)}?{q}")
    chart = (data or {}).get("chart") or {}
    if chart.get("error"):
        raise FetchError(f"Yahoo: {symbol}: {chart['error'].get('description', chart['error'])}")
    res = (chart.get("result") or [None])[0]
    if not res or not res.get("timestamp"):
        raise FetchError(f"Yahoo returned no bars for {symbol}")
    meta = res.get("meta", {})
    quote = res["indicators"]["quote"][0]
    ev = res.get("events") or {}
    splits = sorted(((datetime.fromtimestamp(s["date"], NY).date(), s["numerator"] / s["denominator"])
                     for s in (ev.get("splits") or {}).values() if s.get("denominator")), key=lambda x: x[0])
    last_session = _last_closed(now)

    def factor_after(d: date) -> float:
        """Cumulative ratio of splits with ex-date after d: Yahoo price x factor = raw price."""
        f = 1.0
        for ex, r in splits:
            if ex > d:
                f *= r
        return f

    bars = []
    for i, ts in enumerate(res["timestamp"]):
        d = datetime.fromtimestamp(ts, NY).date()
        vals = [quote[k][i] for k in ("open", "high", "low", "close", "volume")]
        if any(v is None for v in vals) or d > last_session or d < start or d > end:
            continue
        if not (COVERED[0] <= d <= COVERED[1]) or not is_trading_day(d):
            continue
        f = factor_after(d)
        o, h, lo, c, v = vals
        bars.append(dict(date=d.isoformat(), open=round(o * f, 4), high=round(h * f, 4), low=round(lo * f, 4),
                         close=round(c * f, 4), volume=int(round(v / f))))
    if not bars:
        raise FetchError(f"Yahoo returned no completed sessions for {symbol} in {start}..{end}")
    bars.sort(key=lambda b: b["date"])

    actions = [dict(type="split", ex_date=ex.isoformat(), ratio=r, source="yahoo")
               for ex, r in splits if start <= ex <= end]
    for dv in (ev.get("dividends") or {}).values():
        ex = datetime.fromtimestamp(dv["date"], NY).date()
        if start <= ex <= min(end, last_session):
            actions.append(dict(type="dividend", ex_date=ex.isoformat(), pay_date=ex.isoformat(),
                                amount=round(dv["amount"] * factor_after(ex), 6), source="yahoo",
                                pay_date_note="Yahoo has no pay date; credited on the ex-date"))
    actions.sort(key=lambda a: a["ex_date"])
    return bars, actions, meta


def _last_closed(now: datetime) -> date:
    ny = now.astimezone(NY)
    return ny.date() if ny.time() >= CLOSE else ny.date() - timedelta(days=1)


# ---------------------------------------------------------------- news

def fetch_news(http: Http, symbol: str, count: int = 25) -> list[dict]:
    q = urllib.parse.urlencode({"q": symbol, "quotesCount": 0, "newsCount": count, "enableFuzzyQuery": "false"})
    data = _yahoo(http, f"/v1/finance/search?{q}")
    out = []
    for n in (data or {}).get("news", []) or []:
        ts, title = n.get("providerPublishTime"), (n.get("title") or "").strip()
        if not ts or not title:
            continue
        related = [t.upper() for t in n.get("relatedTickers") or []]
        if related and symbol.upper() not in related:
            continue
        out.append(dict(published=datetime.fromtimestamp(ts, NY).isoformat(), headline=title,
                        source=f"yahoo:{n.get('publisher', 'unknown')}"))
    return out


def eight_k_news(submissions: dict, since: date) -> list[dict]:
    r = (submissions.get("filings") or {}).get("recent") or {}
    out = []
    for form, acc, filed, items in zip(r.get("form", []), r.get("acceptanceDateTime", []),
                                       r.get("filingDate", []), r.get("items", [])):
        if form not in ("8-K", "8-K/A") or filed < since.isoformat():
            continue
        try:
            ts = datetime.fromisoformat(acc.replace("Z", "+00:00")).astimezone(NY)
        except ValueError:
            continue
        codes = [c.strip() for c in (items or "").split(",") if c.strip()]
        label = "; ".join(f"{c} {EIGHT_K_ITEMS.get(c, '')}".strip() for c in codes) or "no items listed"
        out.append(dict(published=ts.isoformat(), headline=f"SEC {form} filed: {label}", source="sec_edgar"))
    return out


# ---------------------------------------------------------------- fundamentals

def _units(facts: dict, concept: str, unit: str) -> list[dict]:
    node = facts.get("facts", {}).get("us-gaap", {}).get(concept) or {}
    return [f for f in node.get("units", {}).get(unit, []) if f.get("form", "").split("/")[0] in REPORT_FORMS]


def _first_reported(rows: list[dict], key) -> dict:
    """{key(row): row} keeping the earliest filing of each period (point in time, no restatements)."""
    out: dict = {}
    for f in rows:
        k = key(f)
        if k is None:
            continue
        if k not in out or (f.get("filed", ""), f.get("accn", "")) < (out[k].get("filed", ""), out[k].get("accn", "")):
            out[k] = f
    return out


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def quarterly_flow(facts: dict, concepts: list[str], unit: str = "USD") -> dict[str, tuple[float, str]]:
    """{period_end: (three-month value, filed)} for a flow concept.

    Direct three-month facts first; otherwise de-cumulated from year-to-date
    facts sharing a fiscal-year start (cash-flow statements, and Q4 from a
    10-K). The filed date is the later of the facts used.
    """
    merged: dict[str, tuple[float, str]] = {}
    for concept in concepts:
        per = _first_reported([f for f in _units(facts, concept, unit) if "start" in f],
                              key=lambda f: (f["start"], f["end"]))
        q: dict[str, tuple[float, str]] = {}
        by_start: dict[str, list[tuple[str, dict]]] = {}
        for (s, e), f in per.items():
            n = _days(s, e)
            if 80 <= n <= 100:
                q[e] = (f["val"], f["filed"])
            if 80 <= n <= 380:
                by_start.setdefault(s, []).append((e, f))
        for s, rows in by_start.items():
            rows.sort(key=lambda r: r[0])
            prev = None  # (end, cumulative value, filed) of the previous year-to-date fact
            for e, f in rows:
                if prev and e not in q and 80 <= _days(prev[0], e) <= 100:
                    q[e] = (f["val"] - prev[1], max(f["filed"], prev[2]))
                prev = (e, f["val"], f["filed"])
        for (s, e), f in per.items():  # Q4 (or any gap) = annual minus the three quarters inside it
            if e in q or not 350 <= _days(s, e) <= 380:
                continue
            inside = [(qe, v) for qe, v in q.items() if s < qe < e and _days(s, qe) >= 80]
            if len(inside) == 3:
                q[e] = (f["val"] - sum(v[0] for _, v in inside), max([f["filed"]] + [v[1] for _, v in inside]))
        for e, v in q.items():
            merged.setdefault(e, v)  # later concepts only fill gaps (companies switch tags)
    return merged


def _instant(facts: dict, concepts: list[str], unit: str = "USD") -> dict[str, float]:
    out: dict[str, float] = {}
    for c in concepts:
        rows = _first_reported([f for f in _units(facts, c, unit) if "start" not in f], key=lambda f: f["end"])
        for e, f in rows.items():
            out.setdefault(e, f["val"])
    return out


def _shares(facts: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for c in SHARE_CONCEPTS:
        rows = _first_reported([f for f in _units(facts, c, "shares") if "start" in f],
                               key=lambda f: (f["start"], f["end"]))
        for (s, e), f in sorted(rows.items(), key=lambda kv: _days(*kv[0])):  # 3-month figure wins
            out.setdefault(e, f["val"])
    return out


def build_filings(facts: dict, max_quarters: int = 12) -> tuple[list[dict], list[str]]:
    """Quarterly filing rows in `MarketData` units (USD bn; shares bn), oldest first, plus notes."""
    notes: list[str] = []
    flow = {k: quarterly_flow(facts, c) for k, c in FLOW_CONCEPTS.items()}
    shares, cash = _shares(facts), _instant(facts, CASH_CONCEPTS)
    debt_parts = [_instant(facts, c) for c in DEBT_PARTS]
    rows = []
    for pe in sorted(flow["revenue"]):
        need = ("revenue", "operating_income", "net_income", "cfo")
        if any(pe not in flow[k] for k in need) or pe not in shares or pe not in cash:
            continue
        rev, filed = flow["revenue"][pe]
        if pe in flow["gross_profit"]:
            gp = flow["gross_profit"][pe][0]
        elif pe in flow["cost_of_revenue"]:
            gp = rev - flow["cost_of_revenue"][pe][0]
        else:
            continue
        capex = flow["capex"].get(pe, (0.0, ""))[0]
        if pe not in flow["capex"]:
            notes.append(f"{pe}: no capex tag, FCF = operating cash flow")
        debt_vals = [p[pe] for p in debt_parts if pe in p]
        if not debt_vals:
            notes.append(f"{pe}: no debt tag reported, debt = 0")
        filed = max([filed] + [flow[k][pe][1] for k in need])
        rows.append(dict(period_end=pe, filed=filed, revenue=round(rev / BN, 4),
                         gross_profit=round(gp / BN, 4),
                         operating_income=round(flow["operating_income"][pe][0] / BN, 4),
                         net_income=round(flow["net_income"][pe][0] / BN, 4),
                         fcf=round((flow["cfo"][pe][0] - capex) / BN, 4),
                         diluted_shares=round(shares[pe] / BN, 6), cash=round(cash[pe] / BN, 4),
                         debt=round(sum(debt_vals) / BN, 4), units="USD bn; shares bn",
                         source="sec_xbrl_first_reported"))
    return rows[-max_quarters:], notes


# ---------------------------------------------------------------- assembly

def _sec_ciks(http: Http) -> dict[str, tuple[str, str]]:
    data = http.get_json(SEC_TICKERS)
    out = {}
    for v in data.values():
        out.setdefault(v["ticker"].upper().replace("-", "."), (str(v["cik_str"]).zfill(10), v.get("title", "")))
    return out


def build_market_data(symbols: list[str], benchmark: str, start: date, end: date | None = None,
                      http: Http | None = None, now: datetime | None = None, sectors: dict | None = None,
                      previous: MarketData | None = None, log: Callable[[str], None] = print) -> MarketData:
    """Fetch everything for *symbols* (fundamentals + news) and *benchmark* (prices only)."""
    http = http or Http(os.environ.get("SEC_USER_AGENT"))
    now = now or datetime.now(timezone.utc)
    end = min(end or now.astimezone(NY).date(), COVERED[1])
    start = max(start, COVERED[0])
    sectors = sectors or {}
    bars, actions, filings, news, profiles = {}, {}, {}, {}, {}
    ciks = _sec_ciks(http)
    for sym in [*symbols, benchmark]:
        b, a, meta = fetch_bars(http, sym, start, end, now)
        bars[sym], actions[sym] = b, a
        profiles[sym] = dict(name=meta.get("longName") or meta.get("shortName") or sym,
                             sector=sectors.get(sym, "Index ETF" if sym == benchmark else ""),
                             exchange=meta.get("fullExchangeName") or meta.get("exchangeName") or "US",
                             currency=meta.get("currency", "USD"), instrument_type=meta.get("instrumentType", ""))
        log(f"{sym}: {len(b)} sessions {b[0]['date']}..{b[-1]['date']}, {len(a)} corporate actions")
        if sym == benchmark:
            continue
        if sym not in ciks:
            raise FetchError(f"{sym} is not in SEC company_tickers.json: no filings, Merlin cannot value it")
        cik, title = ciks[sym]
        sub = http.get_json(SEC_SUBMISSIONS.format(cik=cik))
        profiles[sym].update(name=sub.get("name") or title or profiles[sym]["name"], cik=cik,
                             sector=sectors.get(sym) or sub.get("sicDescription", ""))
        rows, notes = build_filings(http.get_json(SEC_FACTS.format(cik=cik)))
        if len(rows) < 8:
            log(f"{sym}: WARNING only {len(rows)} complete quarters from SEC XBRL (Merlin needs 4, Quant uses 8)")
        filings[sym] = rows
        for n in notes[-3:]:
            log(f"{sym}: note {n}")
        items = eight_k_news(sub, start)
        try:
            items += fetch_news(http, sym)
        except FetchError as exc:
            log(f"{sym}: headlines unavailable ({exc}); 8-K filings only")
        if previous is not None:
            items += previous.news.get(sym, [])
        uniq = {(n["published"], n["headline"]): n for n in items}
        news[sym] = sorted(uniq.values(), key=lambda n: n["published"])
        log(f"{sym}: {len(rows)} quarters (latest {rows[-1]['period_end'] if rows else 'none'}), "
            f"{len(news[sym])} news items")
    news[benchmark] = []
    return MarketData(source_name=SOURCE_NAME, source_kind="market_data", currency="USD", bars=bars,
                      actions=actions, filings=filings, news=news, profiles=profiles, halts={})
