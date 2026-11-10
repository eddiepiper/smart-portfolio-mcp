"""Portfolio Analyst agent (foundation: QuantStats).

Read-only. Values the verified ledger with Data-Guardian-accepted prices and
reports performance independently of Marcus's narrative. Contributions are
cash flows, never returns (time-weighted returns strip them out). Missing prices
are reported as exceptions — a holding without a validated price is left
unvalued, not valued at cost or at a guess.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd
import quantstats as qs

from ...core.contracts import (LedgerEvent, LedgerEventType, MarketObservation, PortfolioAnalysisReport, PriceType,
                               QualityStatus)
from ...core.ledger import LedgerReader, balances
from ..base import Agent

MIN_RETURNS_FOR_RISK = 20


def _usable(o: MarketObservation) -> bool:
    return (o.validation_status in (QualityStatus.VALID, QualityStatus.WARNING)
            and o.price_type in (PriceType.OFFICIAL_CLOSE, PriceType.HISTORICAL) and o.close is not None)


class PortfolioAnalyst(Agent):
    agent_id = "portfolio_analyst"

    def __init__(self, rt):
        super().__init__(rt)
        self.reader: LedgerReader = rt.ledger_reader()          # read-only view, no write methods

    def register(self):
        self.tool("generate_performance_report", self.generate_performance_report, "read_ledger", timeout_s=180)
        self.tool("reconcile_cash_flows", self.reconcile_cash_flows, "read_ledger")
        self.tool("calculate_portfolio_equity", self.calculate_portfolio_equity, "read_ledger")
        self.tool("detect_performance_anomalies", self.detect_performance_anomalies, "read_ledger")
        return self

    # --------------------------------------------------------------------- pieces
    def reconcile_cash_flows(self) -> list[str]:
        problems = self.reader.verify()
        try:
            balances(self.reader.events())
        except Exception as exc:  # noqa: BLE001
            problems.append(str(exc))
        for ev in self.reader.events():
            if ev.event_type in (LedgerEventType.BUY, LedgerEventType.SELL) and (ev.price is None or not ev.quantity):
                problems.append(f"{ev.event_id}: trade without price or quantity")
            if ev.event_type == LedgerEventType.BUY and ev.amount >= 0:
                problems.append(f"{ev.event_id}: buy with non-negative cash amount")
            if ev.event_type == LedgerEventType.CONTRIBUTION and ev.instrument_id:
                problems.append(f"{ev.event_id}: contribution tied to an instrument")
        return problems

    def _prices(self, observations: dict[str, list[MarketObservation]]) -> dict[str, pd.Series]:
        out = {}
        for iid, obs in observations.items():
            good = [o for o in obs if _usable(o)]
            if good:
                out[iid] = pd.Series({pd.Timestamp(o.bar_date): float(o.close) for o in good}).sort_index()
        return out

    def calculate_portfolio_equity(self, observations: dict[str, list[MarketObservation]], fx: Optional[dict[str, pd.Series]] = None,
                                   base_currency: str = "USD") -> dict:
        """Daily equity in base currency from ledger + validated closes. fx maps currency -> series of base per unit."""
        events = self.reader.events()
        prices = self._prices(observations)
        if not events:
            return {"equity": pd.Series(dtype=float), "flows": pd.Series(dtype=float), "exceptions": ["empty ledger"]}
        start = min(e.event_at.date() for e in events)
        cal = sorted({d for s in prices.values() for d in s.index if d.date() >= start})
        exceptions, rows, flows = [], {}, defaultdict(float)
        for d in cal:
            upto = [e for e in events if e.event_at.date() <= d.date()]
            st = balances(upto)
            total, missing = 0.0, False
            for ccy, amt in st["cash"].items():
                total += float(amt) * self._fx(fx, ccy, base_currency, d, exceptions)
            for iid, p in st["positions"].items():
                s = prices.get(iid)
                px = s.loc[:d].iloc[-1] if s is not None and not s.loc[:d].empty else None
                if px is None:
                    missing = True
                    exceptions.append(f"{d.date()}: no validated price for {iid}; equity not computed for this day")
                    break
                total += float(p["quantity"]) * px * self._fx(fx, p["currency"], base_currency, d, exceptions)
            if not missing:
                rows[d] = total
            for e in upto:
                if e.event_at.date() == d.date() and e.event_type in (LedgerEventType.CONTRIBUTION, LedgerEventType.WITHDRAWAL, LedgerEventType.IMPORT):
                    flows[d] += float(e.amount) * self._fx(fx, e.currency, base_currency, d, exceptions)
        return {"equity": pd.Series(rows).sort_index(), "flows": pd.Series(flows).sort_index(), "exceptions": sorted(set(exceptions))}

    @staticmethod
    def _fx(fx, ccy, base, d, exceptions) -> float:
        if ccy == base:
            return 1.0
        s = (fx or {}).get(ccy)
        if s is None or s.loc[:d].empty:
            exceptions.append(f"no {ccy}->{base} FX rate on {d.date()}; {ccy} amounts excluded")
            return float("nan")
        return float(s.loc[:d].iloc[-1])

    @staticmethod
    def time_weighted_returns(equity: pd.Series, flows: pd.Series) -> pd.Series:
        """r_t = (E_t - F_t) / E_{t-1} - 1, so contributions never count as performance."""
        f = flows.reindex(equity.index, fill_value=0.0)
        r = (equity - f) / equity.shift(1) - 1
        return r.iloc[1:].replace([np.inf, -np.inf], np.nan).dropna()

    def detect_performance_anomalies(self, returns: pd.Series, threshold: float = 0.15) -> list[str]:
        return [f"{d.date()}: daily portfolio return {r:.1%} exceeds {threshold:.0%}; verify prices and events"
                for d, r in returns.items() if abs(r) > threshold]

    # ----------------------------------------------------------------- main report
    def generate_performance_report(self, observations: dict[str, list[MarketObservation]], *,
                                    base_currency: str = "USD", fx: Optional[dict[str, pd.Series]] = None,
                                    benchmark: Optional[list[MarketObservation]] = None,
                                    period: Optional[tuple[date, date]] = None) -> PortfolioAnalysisReport:
        events = self.reader.events()
        warnings: list[str] = []
        recon = self.reconcile_cash_flows()
        st = balances(events) if not any("sell of" in r for r in recon) else {"cash": {}, "positions": {}, "contributed": {}, "realised": {}}
        prices = self._prices(observations)
        holdings, unreal, mv_total = [], defaultdict(Decimal), Decimal("0")
        valued_all = True
        for iid, p in st["positions"].items():
            s = prices.get(iid)
            if s is None:
                valued_all = False
                recon.append(f"no validated price for holding {iid}: left unvalued")
                holdings.append({"instrument_id": iid, "quantity": str(p["quantity"]), "cost": str(p["cost"]), "market_value": None})
                continue
            px = Decimal(str(s.iloc[-1]))
            mv = (px * p["quantity"]).quantize(Decimal("0.01"))
            holdings.append({"instrument_id": iid, "quantity": str(p["quantity"]), "cost": str(p["cost"].quantize(Decimal("0.01"))),
                             "price": str(px), "price_date": str(s.index[-1].date()), "market_value": str(mv),
                             "unrealised_pnl": str((mv - p["cost"]).quantize(Decimal("0.01"))), "currency": p["currency"]})
            unreal[p["currency"]] += mv - p["cost"]
            if p["currency"] == base_currency:
                mv_total += mv
        cash_base = st["cash"].get(base_currency, Decimal("0"))
        other_ccy = [c for c in list(st["cash"]) + [h.get("currency") for h in holdings if h.get("currency")] if c and c != base_currency]
        if other_ccy and not fx:
            warnings.append(f"amounts in {sorted(set(other_ccy))} not converted to {base_currency}: no FX series supplied")
        equity = (cash_base + mv_total) if valued_all and not other_ccy else None
        # reconciliation: equity must equal cash + holdings at market
        if equity is not None:
            check = cash_base + sum(Decimal(h["market_value"]) for h in holdings if h.get("market_value"))
            if check != equity:
                recon.append(f"equity {equity} != cash + holdings {check}")
        series = self.calculate_portfolio_equity(observations, fx, base_currency)
        recon += series["exceptions"]
        eq, flows = series["equity"], series["flows"]
        if period:
            eq = eq.loc[pd.Timestamp(period[0]):pd.Timestamp(period[1])]
        rets = self.time_weighted_returns(eq, flows) if len(eq) > 1 else pd.Series(dtype=float)
        returns: dict = {"observations": int(len(rets))}
        risk: dict = {}
        if len(rets):
            returns["time_weighted_return_pct"] = round(float((1 + rets).prod() - 1) * 100, 4)
            contributed_total = sum(float(v) for c, v in st["contributed"].items() if c == base_currency)
            if contributed_total and equity is not None:
                returns["simple_gain_on_contributed_pct"] = round((float(equity) / contributed_total - 1) * 100, 4)
            dd = qs.stats.max_drawdown(rets)
            risk["max_drawdown_pct"] = round(float(dd) * 100, 4)
            if len(rets) >= MIN_RETURNS_FOR_RISK:
                risk["volatility_ann_pct"] = round(float(qs.stats.volatility(rets)) * 100, 4)
                risk["sharpe"] = round(float(qs.stats.sharpe(rets)), 4)
                risk["sortino"] = round(float(qs.stats.sortino(rets)), 4)
            else:
                warnings.append(f"only {len(rets)} daily returns: volatility and risk-adjusted ratios not meaningful (need {MIN_RETURNS_FOR_RISK})")
        else:
            warnings.append("insufficient valued history to compute returns")
        bench = {}
        if benchmark:
            bs = self._prices({"benchmark": benchmark}).get("benchmark")
            if bs is not None and len(rets):
                bs = bs.reindex(rets.index.union(eq.index)).ffill().reindex(eq.index)
                br = bs.pct_change().dropna().reindex(rets.index).dropna()
                if len(br) == len(rets):
                    bench = {"instrument_id": benchmark[0].instrument_id, "currency": benchmark[0].currency,
                             "return_pct": round(float((1 + br).prod() - 1) * 100, 4),
                             "dates": [str(br.index[0].date()), str(br.index[-1].date())]}
                    if benchmark[0].currency != base_currency:
                        warnings.append(f"benchmark in {benchmark[0].currency} vs portfolio base {base_currency}: not comparable without FX")
                else:
                    warnings.append("benchmark dates do not align with portfolio valuation dates")
        anomalies = self.detect_performance_anomalies(rets) if len(rets) else []
        recon += anomalies
        weights = {}
        if equity:
            for h in holdings:
                if h.get("market_value"):
                    weights[h["instrument_id"]] = round(float(Decimal(h["market_value"]) / equity) * 100, 2)
        rep = PortfolioAnalysisReport(
            portfolio_id=self.rt.portfolio_id, period_start=eq.index[0].date() if len(eq) else None,
            period_end=eq.index[-1].date() if len(eq) else None, base_currency=base_currency,
            contributed_capital={k: str(v) for k, v in st["contributed"].items()},
            cash={k: str(v) for k, v in st["cash"].items()}, holdings=holdings,
            equity=None if equity is None else str(equity),
            realised_pnl={k: str(v.quantize(Decimal("0.01"))) for k, v in st["realised"].items()},
            unrealised_pnl={k: str(v.quantize(Decimal("0.01"))) for k, v in unreal.items()},
            returns=returns, risk=risk,
            concentration={"weights_pct": weights, "largest_pct": max(weights.values()) if weights else None},
            benchmark=bench, reconciliation_exceptions=sorted(set(recon)), warnings=warnings,
            evidence=[e.event_id for e in events], ledger_head=self.reader.head())
        self.rt.evidence.put(rep, "portfolio_analysis_report")
        return rep
