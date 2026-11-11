"""Market Intelligence agent (foundation: OpenBB).

Retrieves source-attributed market information. Never recommends, trades or
edits allocations. Every report leaves here with data_quality_status=None:
downstream consumers must pass it through Data Guardian first
(marcus_v2.marcus.guards.require_validated enforces this).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from ...core.contracts import (AssetClass, Evidence, Instrument, MarketIntelligenceReport, MarketObservation,
                               MarketSession, RetrievalStatus, new_id, utcnow)
from ...core.sessions import classify_daily_bar
from ...integrations.openbb_adapter import BaseProvider, OpenBBProvider, RawBars, YahooChartProvider
from ..base import Agent, load_instruments


class MarketIntelligence(Agent):
    agent_id = "market_intelligence"

    def __init__(self, rt, providers: Optional[dict[str, BaseProvider]] = None, clock=utcnow):
        super().__init__(rt)
        self.providers = providers or {"openbb": OpenBBProvider(), "yahoo": YahooChartProvider()}
        self.primary = next(iter(self.providers))
        self.clock = clock
        reg = load_instruments()
        self.instruments = {i["instrument_id"]: Instrument(**i) for i in reg["instruments"]}

    def register(self):
        self.tool("get_instrument_metadata", self.get_instrument_metadata, "read_market_data")
        self.tool("get_historical_prices", self.get_historical_prices, "read_market_data", timeout_s=120)
        self.tool("get_market_snapshot", self.get_market_snapshot, "read_market_data")
        self.tool("get_market_context", self.get_market_context, "read_market_data", timeout_s=120)
        self.tool("compare_data_providers", self.compare_data_providers, "read_market_data", timeout_s=180)
        self.tool("check_data_availability", self.check_data_availability, "read_market_data")
        self.tool("get_macro_indicators", self.get_macro_indicators, "read_market_data")
        return self

    # ------------------------------------------------------------------ helpers
    def get_instrument_metadata(self, instrument_id: str) -> Instrument:
        if instrument_id not in self.instruments:
            raise KeyError(f"unknown instrument {instrument_id}; add it to config/instruments.json first")
        return self.instruments[instrument_id]

    def _cache_path(self, provider: str, symbol: str, start: date, end: date, interval: str):
        h = hashlib.sha256(f"{provider}|{symbol}|{start}|{end}|{interval}".encode()).hexdigest()[:24]
        return self.rt.cache_dir / f"mi_{h}.json"

    def _fetch(self, provider: str, inst: Instrument, start: date, end: date, interval: str,
               max_age_s: int) -> tuple[RawBars, bool]:
        p = self._cache_path(provider, inst.symbol, start, end, interval)
        if p.exists():
            rec = json.loads(p.read_text())
            age = (self.clock() - datetime.fromisoformat(rec["cached_at"])).total_seconds()
            if age <= max_age_s:
                return _rawbars_from_json(rec["raw"]), True
        raw = self.providers[provider].historical(inst.symbol, start, end, interval)
        if not raw.error and raw.bars:
            p.write_text(json.dumps({"cached_at": self.clock().isoformat(), "raw": _rawbars_to_json(raw)}))
        return raw, False

    def _observations(self, inst: Instrument, raw: RawBars) -> list[MarketObservation]:
        now = self.clock()
        out = []
        for b in raw.bars:
            out.append(MarketObservation(
                event_id=f"{raw.provider}:{inst.instrument_id}:{b.bar_date}",
                instrument_id=inst.instrument_id, symbol=inst.symbol, market=inst.market,
                asset_class=inst.asset_class,
                # currency comes from the provider when it reports one; otherwise the registry
                # value is used and the gap is recorded as a limitation (not silently assumed)
                currency=raw.currency or inst.currency, price_unit=inst.price_unit,
                open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume, bar_date=b.bar_date,
                source_provider=raw.provider, observed_at=raw.source_timestamp, retrieved_at=now,
                market_timezone=inst.timezone,
                session=MarketSession.CONTINUOUS if inst.asset_class in (AssetClass.COMMODITY, AssetClass.FX) else MarketSession.CLOSED,
                price_type=classify_daily_bar(b.bar_date, inst.asset_class, now),
            ))
        return out

    # -------------------------------------------------------------------- tools
    def get_historical_prices(self, instrument_id: str, start: date, end: date, *, interval: str = "1d",
                              provider: Optional[str] = None, requesting_agent: str = "marcus",
                              correlation_id: Optional[str] = None, max_age_s: int = 3600) -> MarketIntelligenceReport:
        inst = self.get_instrument_metadata(instrument_id)
        provider = provider or self.primary
        raw, cached = self._fetch(provider, inst, start, end, interval, max_age_s)
        obs = self._observations(inst, raw)
        limitations = list(raw.limitations)
        if raw.currency is None:
            limitations.append(f"provider did not report currency; registry value {inst.currency} used")
        missing = []
        if raw.error:
            missing.append(f"{provider} retrieval failed: {raw.error}")
        status = (RetrievalStatus.FAILED if raw.error or not obs else
                  RetrievalStatus.PARTIAL if any(o.close is None for o in obs) else RetrievalStatus.OK)
        if any(o.close is None for o in obs):
            missing.append("one or more bars have no close")
        ev = Evidence(kind="market_data", source=raw.provider,
                      reference=f"{inst.symbol} {start}..{end} {interval} ({len(obs)} bars{', cached' if cached else ''})")
        report = MarketIntelligenceReport(
            correlation_id=correlation_id or new_id("corr"), requesting_agent=requesting_agent, instrument=inst,
            category="historical_prices", source_provider=raw.provider, source_timestamp=raw.source_timestamp,
            observations=obs, coverage={"requested": [str(start), str(end)],
                                        "returned": [str(obs[0].bar_date), str(obs[-1].bar_date)] if obs else None,
                                        "bars": len(obs), "interval": interval, "cache_hit": cached},
            provider_limitations=limitations, missing_information=missing, evidence=[ev], status=status)
        self.rt.evidence.put(report, "market_intelligence_report")
        return report

    def get_market_snapshot(self, instrument_id: str, **kw) -> MarketIntelligenceReport:
        now = self.clock().date()
        rep = self.get_historical_prices(instrument_id, now - timedelta(days=10), now, **kw)
        rep.category = "snapshot"
        rep.observations = rep.observations[-1:]
        return rep

    def get_market_context(self, instrument_id: str, lookback_days: int = 400, **kw) -> MarketIntelligenceReport:
        """Deterministic trend context (moving averages, RSI, ATR, ranges) from validated-to-be bars."""
        now = self.clock().date()
        rep = self.get_historical_prices(instrument_id, now - timedelta(days=lookback_days), now, **kw)
        rep.category = "market_context"
        closes = [float(o.close) for o in rep.observations if o.close is not None and o.price_type.value != "provisional_intraday"]
        highs = [float(o.high) for o in rep.observations if o.high is not None and o.price_type.value != "provisional_intraday"]
        lows = [float(o.low) for o in rep.observations if o.low is not None and o.price_type.value != "provisional_intraday"]
        ctx: dict = {"bars_used": len(closes), "excluded_provisional": sum(o.price_type.value == "provisional_intraday" for o in rep.observations)}
        if len(closes) >= 200:
            ma = lambda n: sum(closes[-n:]) / n
            ctx.update(last_close=closes[-1], ma20=ma(20), ma50=ma(50), ma200=ma(200),
                       ma50_10d_ago=sum(closes[-60:-10]) / 50, rsi14=_rsi(closes),
                       atr14=_atr(highs, lows, closes), high_20d=max(highs[-20:]), high_60d=max(highs[-60:]),
                       low_10d=min(lows[-10:]), high_52w=max(highs[-252:]), low_52w=min(lows[-252:]))
            ctx["uptrend"] = ctx["last_close"] > ctx["ma50"] > ctx["ma200"] and ctx["ma50"] > ctx["ma50_10d_ago"]
        else:
            rep.missing_information.append(f"only {len(closes)} final closes; 200 needed for trend context")
        rep.context = ctx
        return rep

    def compare_data_providers(self, instrument_id: str, start: date, end: date, **kw) -> dict:
        reports = {name: self.get_historical_prices(instrument_id, start, end, provider=name, **kw) for name in self.providers}
        by = {name: {o.bar_date: o.close for o in r.observations if o.close is not None} for name, r in reports.items()}
        names = list(by)
        diffs = []
        if len(names) >= 2:
            a, b = by[names[0]], by[names[1]]
            for d in sorted(set(a) & set(b)):
                pct = abs(a[d] - b[d]) / b[d] * 100
                diffs.append({"date": str(d), names[0]: str(a[d]), names[1]: str(b[d]), "diff_pct": round(float(pct), 4)})
            only = sorted(set(a) ^ set(b))
        else:
            only = []
        return {"instrument_id": instrument_id, "providers": names, "differences": diffs,
                "dates_missing_in_one_provider": [str(d) for d in only],
                "reports": {k: v.report_id for k, v in reports.items()}, "_reports": reports}

    def check_data_availability(self, instrument_id: str) -> dict:
        try:
            inst = self.get_instrument_metadata(instrument_id)
        except KeyError as exc:
            return {"instrument_id": instrument_id, "available": False, "reason": str(exc)}
        now = self.clock().date()
        res = {}
        for name, p in self.providers.items():
            raw = p.historical(inst.symbol, now - timedelta(days=7), now)
            res[name] = {"ok": not raw.error and bool(raw.bars), "bars": len(raw.bars), "error": raw.error}
        return {"instrument_id": instrument_id, "providers": res, "available": any(v["ok"] for v in res.values())}

    def get_macro_indicators(self, series: list[str]) -> dict:
        # FRED / economy endpoints need api.stlouisfed.org + a FRED key (not configured) — reported, not faked.
        return {"status": RetrievalStatus.UNSUPPORTED.value, "series": series,
                "reason": "no macro provider configured (FRED needs a free key and network access to api.stlouisfed.org)"}


# --------------------------------------------------------------------- utilities
def _rsi(c: list[float], n: int = 14) -> float:
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n; l = (l * (n - 1) + max(-d, 0)) / n
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def _atr(h: list[float], l: list[float], c: list[float], n: int = 14) -> float:
    trs = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(len(c) - n, len(c))]
    return sum(trs) / n


def _rawbars_to_json(raw: RawBars) -> dict:
    return {"provider": raw.provider, "symbol": raw.symbol, "currency": raw.currency,
            "source_timestamp": raw.source_timestamp.isoformat() if raw.source_timestamp else None,
            "limitations": raw.limitations,
            "bars": [[str(b.bar_date), *[None if v is None else str(v) for v in (b.open, b.high, b.low, b.close, b.volume)]] for b in raw.bars]}


def _rawbars_from_json(d: dict) -> RawBars:
    from ...integrations.openbb_adapter import RawBar
    bars = [RawBar(date.fromisoformat(b[0]), *[None if v is None else Decimal(v) for v in b[1:]]) for b in d["bars"]]
    ts = d.get("source_timestamp")
    return RawBars(d["provider"], d["symbol"], d.get("currency"), bars, datetime.fromisoformat(ts) if ts else None, d.get("limitations", []))
