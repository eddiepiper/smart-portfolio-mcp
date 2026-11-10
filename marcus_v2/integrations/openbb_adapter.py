"""Market-data provider abstraction for the Market Intelligence agent.

Providers:
- OpenBBProvider: OpenBB Platform (openbb-core 2.0.1) QueryExecutor with the
  free `yfinance` provider extension. VERIFIED in the build environment
  (live daily history). No paid providers are configured or enrolled.
- YahooChartProvider: direct Yahoo v8 chart endpoint (the same source the
  trader-* skills use). Used to cross-check OpenBB (compare_data_providers).
- FixtureProvider: reads saved bars from JSON for offline tests and demos.

All providers return the same RawBars shape. Nothing here fills gaps,
adjusts prices or invents values: missing bars stay missing.
"""
from __future__ import annotations

import asyncio
import json
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional


@dataclass
class RawBar:
    bar_date: date
    open: Optional[Decimal]
    high: Optional[Decimal]
    low: Optional[Decimal]
    close: Optional[Decimal]
    volume: Optional[Decimal]


@dataclass
class RawBars:
    provider: str
    symbol: str
    currency: Optional[str]
    bars: list[RawBar]
    source_timestamp: Optional[datetime] = None
    limitations: list[str] = field(default_factory=list)
    error: Optional[str] = None


def _d(x) -> Optional[Decimal]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return Decimal(repr(f))


class ProviderError(Exception):
    pass


class BaseProvider:
    name = "base"

    def historical(self, symbol: str, start: date, end: date, interval: str = "1d") -> RawBars:
        raise NotImplementedError


class OpenBBProvider(BaseProvider):
    name = "openbb:yfinance"

    def historical(self, symbol, start, end, interval="1d") -> RawBars:
        try:
            from openbb_core.provider.query_executor import QueryExecutor
        except ImportError as exc:  # pragma: no cover
            raise ProviderError(f"openbb-core not installed: {exc}") from exc
        params = {"symbol": symbol, "start_date": start, "end_date": end, "interval": interval}
        try:
            rows = asyncio.run(QueryExecutor().execute("yfinance", "EquityHistorical", params, {}))
        except Exception as exc:  # noqa: BLE001
            return RawBars(self.name, symbol, None, [], error=f"{type(exc).__name__}: {exc}")
        bars = [RawBar(r.date if isinstance(r.date, date) and not isinstance(r.date, datetime) else r.date.date(),
                       _d(r.open), _d(r.high), _d(r.low), _d(r.close), _d(r.volume)) for r in rows]
        return RawBars(self.name, symbol, None, bars, datetime.now(timezone.utc),
                       ["OpenBB yfinance provider: free, delayed, unofficial Yahoo data; currency not returned by this endpoint",
                        "Yahoo cookie/crumb blocked in this network; some fundamentals unavailable"])


class YahooChartProvider(BaseProvider):
    name = "yahoo:chart"

    def __init__(self, host: str = "query2.finance.yahoo.com", timeout: int = 20):
        self.host, self.timeout = host, timeout

    def historical(self, symbol, start, end, interval="1d") -> RawBars:
        p1 = int(datetime.combine(start, datetime.min.time(), timezone.utc).timestamp())
        p2 = int(datetime.combine(end, datetime.max.time(), timezone.utc).timestamp())
        url = f"https://{self.host}/v8/finance/chart/{symbol}?period1={p1}&period2={p2}&interval={interval}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (marcus-v2)"})
            data = json.load(urllib.request.urlopen(req, timeout=self.timeout))
            res = data["chart"]["result"][0]
        except Exception as exc:  # noqa: BLE001
            return RawBars(self.name, symbol, None, [], error=f"{type(exc).__name__}: {exc}")
        meta, q = res["meta"], res["indicators"]["quote"][0]
        tz = meta.get("exchangeTimezoneName", "UTC")
        from zoneinfo import ZoneInfo
        bars = []
        for i, ts in enumerate(res.get("timestamp") or []):
            d = datetime.fromtimestamp(ts, ZoneInfo(tz)).date()
            bars.append(RawBar(d, _d(q["open"][i]), _d(q["high"][i]), _d(q["low"][i]), _d(q["close"][i]), _d(q["volume"][i])))
        mt = meta.get("regularMarketTime")
        return RawBars(self.name, symbol, meta.get("currency"), bars,
                       datetime.fromtimestamp(mt, timezone.utc) if mt else None,
                       ["Yahoo chart endpoint: free, delayed, unofficial"])


class FixtureProvider(BaseProvider):
    """Offline provider: {symbol: {"currency": "USD", "bars": [[date, o, h, l, c, v], ...]}}"""
    name = "fixture"

    def __init__(self, path: str | Path, name: str = "fixture"):
        self.data = json.loads(Path(path).read_text())
        self.name = name

    def historical(self, symbol, start, end, interval="1d") -> RawBars:
        rec = self.data.get(symbol)
        if rec is None:
            return RawBars(self.name, symbol, None, [], error="symbol not in fixture")
        bars = [RawBar(date.fromisoformat(b[0]), *[_d(x) for x in b[1:6]]) for b in rec["bars"]
                if start <= date.fromisoformat(b[0]) <= end]
        ts = rec.get("source_timestamp")
        return RawBars(self.name, symbol, rec.get("currency"), bars,
                       datetime.fromisoformat(ts) if ts else None, ["offline fixture data"])
