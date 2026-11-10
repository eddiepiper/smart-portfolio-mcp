"""Marcus V2 orchestrator: Workflows A-D over the six specialists and existing components.

Marcus coordinates and decides; specialists compute. Recommendation logic is
deterministic and auditable: every input report is referenced, every
disagreement is listed, and nothing reaches the Execution Specialist without an
owner approval recorded against the recommendation.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Optional

from ..agents.data_guardian.agent import DataGuardian
from ..agents.execution_specialist.agent import ExecutionSpecialist
from ..agents.market_intelligence.agent import MarketIntelligence
from ..agents.portfolio_analyst.agent import PortfolioAnalyst
from ..agents.research_challenger.agent import ResearchChallenger
from ..agents.strategy_lab.agent import StrategyLab
from ..core.contracts import (Action, ApprovalStatus, BacktestRequest, InvestmentRecommendation, MarketIntelligenceReport,
                              PaperOrder, QualityStatus, ResearchReport, RetrievalStatus, Severity, new_id, utcnow)
from ..core.runtime import Runtime
from ..integrations.existing import QuantAdapter, TradingAgentsAdapter, parse_price_monitor_event


class UnvalidatedData(Exception):
    pass


def require_validated(report: MarketIntelligenceReport) -> None:
    if report.data_quality_status not in (QualityStatus.VALID, QualityStatus.WARNING):
        raise UnvalidatedData(f"{report.report_id}: data quality {report.data_quality_status}; Data Guardian must accept data before decisions use it")


class Marcus:
    def __init__(self, rt: Runtime, *, mi: MarketIntelligence, dg: DataGuardian, sl: StrategyLab,
                 rc: ResearchChallenger, ex: ExecutionSpecialist, pa: PortfolioAnalyst,
                 tradingagents: TradingAgentsAdapter, quant: QuantAdapter, mandate_id: str = "marcus-paper"):
        self.rt, self.mi, self.dg, self.sl, self.rc, self.ex, self.pa = rt, mi, dg, sl, rc, ex, pa
        self.ta, self.quant, self.mandate_id = tradingagents, quant, mandate_id
        self.seen_events_path = rt.home / "seen_events.json"

    # ------------------------------------------------------------------ helpers
    def _call(self, agent_id: str, tool: str, corr: str, idempotency_key: Optional[str] = None, **kw):
        """Every specialist call goes through the dispatcher: permissions, timeouts and telemetry."""
        res = self.rt.dispatcher.call(agent_id, tool, correlation_id=corr, idempotency_key=idempotency_key, **kw)
        if res.status != "ok":
            raise RuntimeError(f"{agent_id}.{tool} {res.status}: {res.error}")
        return res.output

    def _seen(self) -> set[str]:
        return set(json.loads(self.seen_events_path.read_text())) if self.seen_events_path.exists() else set()

    def _mark_seen(self, ids: set[str]) -> None:
        self.seen_events_path.write_text(json.dumps(sorted(self._seen() | ids)))

    def _incident(self, kind: str, detail: dict) -> str:
        d = self.rt.home / "incidents"
        d.mkdir(parents=True, exist_ok=True)
        iid = new_id("inc")
        (d / f"{iid}.json").write_text(json.dumps({"incident_id": iid, "kind": kind, "for": "dex", "at": utcnow().isoformat(), **detail}, default=str, indent=2))
        self.rt.audit.log_event("incident", detail.get("correlation_id", "-"), incident_id=iid, incident_kind=kind)
        return iid

    # --------------------------------------------------------- Workflow A: monitoring
    def workflow_a_daily_monitoring(self, payloads: list[dict], watchlist: set[str]) -> dict:
        corr = new_id("corr")
        seen = self._seen()
        events, rejected, duplicates = [], [], []
        for p in payloads:
            try:
                ev = parse_price_monitor_event(p)
            except ValueError as exc:
                rejected.append(str(exc))
                continue
            if ev.event_id in seen or any(e.event_id == ev.event_id for e in events):
                duplicates.append(ev.event_id)
                continue
            events.append(ev)
        self._mark_seen({e.event_id for e in events})
        material = [e for e in events if e.instrument_id in watchlist]
        assessments = []
        for e in material:
            today = e.occurred_at.date()
            rep = self._call("market_intelligence", "get_historical_prices", corr, instrument_id=e.instrument_id, start=today - timedelta(days=10), end=today, requesting_agent="marcus")
            dq = self._call("data_guardian", "validate_report", corr, report=rep, purpose="decision")
            assessments.append({"event_id": e.event_id, "instrument_id": e.instrument_id, "event": e.payload,
                                "data_quality": dq.status.value, "dq_report": dq.report_id,
                                "research_refresh_needed": dq.status != QualityStatus.REJECTED and e.event_type in ("trigger_hit", "price_alert"),
                                "note": "data rejected: no action may rely on this event" if dq.status == QualityStatus.REJECTED else ""})
        out = {"workflow": "A", "correlation_id": corr, "events": len(payloads), "new": len(events),
               "duplicates_ignored": duplicates, "rejected": rejected, "material": assessments}
        self.rt.evidence.put(out, "monitoring_assessment", obj_id=f"wa_{corr}")
        return out

    # ------------------------------------------------------ Workflow B: opportunity
    def workflow_b_new_opportunity(self, instrument_id: str, as_of: date, *, plan: Optional[dict] = None,
                                   strategy: Optional[dict] = None, research: Optional[ResearchReport] = None,
                                   portfolio_equity: Optional[Decimal] = None) -> InvestmentRecommendation:
        corr = new_id("corr")
        mandate = self.rt.mandates.active(self.mandate_id, as_of)
        limitations, disagreements = [], []
        # 1-2 market intelligence + data guardian
        ctx = self._call("market_intelligence", "get_market_context", corr, instrument_id=instrument_id, requesting_agent="marcus")
        dq = self._call("data_guardian", "validate_report", corr, report=ctx, purpose="decision")
        rec = InvestmentRecommendation(instrument_id=instrument_id, action=Action.WAIT, mandate_id=mandate.mandate_id,
                                       mandate_version=mandate.version, data_quality_refs=[dq.report_id])
        if dq.status == QualityStatus.REJECTED:
            # Workflow D: data outage / invalid data -> block new entries, disclose, raise incident
            rec.decision_status = "blocked"
            rec.owner_approval = ApprovalStatus.NOT_REQUIRED
            rec.evidence_limitations = [f"market data REJECTED: {i.message}" for i in dq.issues if i.severity in (Severity.ERROR, Severity.CRITICAL)][:10]
            rec.risk_assessment = {"incident": self._incident("data_rejected", {"correlation_id": corr, "instrument_id": instrument_id, "dq_report": dq.report_id})}
            return self._store(rec)
        if dq.status == QualityStatus.WARNING:
            limitations.append(f"market data accepted with warnings ({len(dq.issues)} findings, see {dq.report_id})")
        c = ctx.context
        # 3. primary research (TradingAgents)
        research = research or self.ta.research(instrument_id, self.mi.get_instrument_metadata(instrument_id).symbol, as_of)
        self.rt.evidence.put(research, "research_report")
        rec.research_refs = [research.report_id]
        if research.status == RetrievalStatus.FAILED:
            rec.decision_status = "blocked"
            rec.owner_approval = ApprovalStatus.NOT_REQUIRED
            rec.evidence_limitations = limitations + [f"primary research unavailable: {', '.join(research.failed_feeds)}",
                                                      "no new entry without primary research (Workflow D)"]
            rec.risk_assessment = {"incident": self._incident("research_unavailable", {"correlation_id": corr, "instrument_id": instrument_id, "detail": research.failed_feeds})}
            return self._store(rec)
        limitations += [f"research feed failed: {f}" for f in research.failed_feeds]
        # 4. challenge
        ch = self._call("research_challenger", "review_investment_thesis", corr, research=research, market=ctx)
        rec.challenge_refs = [ch.report_id]
        contradictions = [i.message for i in ch.contradictory_evidence if i.severity == Severity.ERROR]
        disagreements += [f"Research Challenger: {m}" for m in contradictions]
        limitations += ch.missing_information
        # 5. strategy lab (optional, rule-based plans)
        bt = None
        if strategy:
            hist = self._call("market_intelligence", "get_historical_prices", corr, instrument_id=instrument_id,
                              start=as_of - timedelta(days=strategy.get("lookback_days", 730)), end=as_of,
                              requesting_agent="strategy_lab")
            self._call("data_guardian", "validate_report", corr, report=hist, purpose="research")
            from ..integrations.vectorbt_adapter import data_version
            from ..agents.strategy_lab.agent import frame_from_report
            req = BacktestRequest(strategy_id=strategy["strategy_id"], instrument_id=instrument_id, symbol=hist.instrument.symbol,
                                  start=as_of - timedelta(days=strategy.get("lookback_days", 730)), end=as_of,
                                  data_version=data_version(frame_from_report(hist)), entry_rule=strategy["entry_rule"],
                                  exit_rule=strategy["exit_rule"], initial_cash=Decimal(str(strategy.get("initial_cash", "20000"))),
                                  fixed_fee=Decimal(str(strategy.get("fixed_fee", "15"))))
            bt = self._call("strategy_lab", "run_strategy_backtest", corr, req=req, report=hist)
            rec.backtest_refs = [bt.result_id]
            if bt.status != "OK":
                limitations.append(f"Strategy Lab: {bt.status} ({'; '.join(bt.concerns)[:300]})")
            elif bt.metrics.get("total_return_pct") is not None and bt.benchmark_metrics.get("total_return_pct") is not None \
                    and bt.metrics["total_return_pct"] < bt.benchmark_metrics["total_return_pct"]:
                disagreements.append(f"Strategy Lab: rule returned {bt.metrics['total_return_pct']}% vs buy-and-hold {bt.benchmark_metrics['total_return_pct']}%")
        # 6. quant (independent): gets evidence, not Marcus's conclusion
        q = None
        if ch.quant_review_recommended or bt is not None:
            q = self.quant.request_review([x for x in [research.report_id, ch.report_id, bt.result_id if bt else None] if x],
                                          {"instrument_id": instrument_id, "data_quality_status": dq.status.value,
                                           "context": c, "backtest": bt.model_dump(mode="json") if bt else None,
                                           "challenge": ch.model_dump(mode="json")})
            self.rt.evidence.put(q, "quant_review", obj_id=q.review_id)
            rec.quant_refs = [q.review_id]
            if q.verdict == "CHALLENGED":
                disagreements += [f"Quant: {f}" for f in q.findings] or ["Quant: challenged"]
            elif q.verdict in ("UNAVAILABLE", "INSUFFICIENT_EVIDENCE"):
                limitations.append(f"Quant review {q.verdict}")
        # 7. decision (deterministic)
        ta_action = research.proposed_action or Action.WAIT
        rec.action = Action.WAIT
        reasons = []
        if ta_action == Action.BUY:
            blockers = []
            if contradictions:
                blockers.append("research contradicted by validated data")
            if not c.get("uptrend"):
                blockers.append("trend filter not met (price > MA50 > MA200, MA50 rising)")
            if q is not None and q.verdict == "CHALLENGED":
                blockers.append("Quant challenged the evidence")
            if q is not None and q.verdict in ("UNAVAILABLE", "INSUFFICIENT_EVIDENCE") and mandate.version >= 1:
                blockers.append("required independent Quant review not available")
            if not plan:
                blockers.append("no entry/stop/target plan supplied")
            if blockers:
                reasons += blockers
            else:
                rec.action = Action.BUY
        elif ta_action in (Action.HOLD, Action.REDUCE, Action.EXIT):
            rec.action = ta_action
        if ta_action != rec.action:
            disagreements.append(f"Marcus {rec.action.value} vs TradingAgents {ta_action.value}: " + "; ".join(reasons))
        # 8. sizing from mandate risk limits
        if rec.action == Action.BUY and plan:
            entry, stop = Decimal(str(plan["limit"])), Decimal(str(plan["stop"]))
            equity = Decimal(str(portfolio_equity or mandate.paper_capital))
            max_loss = (equity * mandate.max_loss_per_trade_pct / 100).quantize(Decimal("0.01"))
            per_share = entry - stop
            qty = (max_loss / per_share).to_integral_value(ROUND_FLOOR) if per_share > 0 else Decimal(0)
            cap = (equity * mandate.max_position_pct / 100 / entry).to_integral_value(ROUND_FLOOR)
            qty = min(qty, cap)
            rec.proposed_quantity = qty
            rec.entry_plan = {k: str(v) for k, v in plan.items()}
            rec.risk_assessment = {"max_loss_limit": str(max_loss), "loss_at_stop": str((qty * per_share).quantize(Decimal("0.01"))),
                                   "position_value": str((qty * entry).quantize(Decimal("0.01"))), "equity_basis": str(equity),
                                   "reward_to_risk": str(((Decimal(str(plan["target"])) - entry) / per_share).quantize(Decimal("0.01"))) if plan.get("target") else None}
            if qty <= 0:
                rec.action = Action.WAIT
                disagreements.append("sizing: stop too far for the mandate's max loss per trade")
        rec.evidence_limitations = limitations
        rec.disagreements = disagreements
        rec.portfolio_context = {"mandate": f"{mandate.mandate_id} v{mandate.version}", "context": {k: v for k, v in c.items() if k in ("last_close", "ma50", "ma200", "rsi14", "uptrend")}}
        rec.decision_status = "proposed"
        rec.owner_approval = ApprovalStatus.PENDING if rec.action in (Action.BUY, Action.REDUCE, Action.EXIT) else ApprovalStatus.NOT_REQUIRED
        return self._store(rec)

    def _store(self, rec: InvestmentRecommendation) -> InvestmentRecommendation:
        self.rt.evidence.put(rec, "investment_recommendation")
        self.rt.audit.log_event("recommendation", "-", recommendation_id=rec.recommendation_id, action=rec.action.value,
                                status=rec.decision_status, approval=rec.owner_approval.value)
        return rec

    # ------------------------------------------------------------------ FX
    def fx_rates(self, start: date, end: date, pair: str = "FX:USDSGD") -> dict:
        """Validated daily closes for a currency pair (units of quote currency per USD).
        Returns {"rates": {date: Decimal}, "series": pd.Series, "dq_report": id, "status": ...}."""
        import pandas as pd
        corr = new_id("corr")
        rep = self._call("market_intelligence", "get_historical_prices", corr, instrument_id=pair, start=start, end=end,
                         requesting_agent="portfolio_analyst")
        dq = self._call("data_guardian", "validate_report", corr, report=rep, purpose="research")
        good = [o for o in rep.observations if o.validation_status != QualityStatus.REJECTED
                and o.price_type.value in ("official_close", "historical") and o.close is not None]
        rates = {o.bar_date: o.close for o in good}
        series = pd.Series({pd.Timestamp(d): float(v) for d, v in rates.items()}).sort_index()
        return {"rates": rates, "series": series, "dq_report": dq.report_id, "status": dq.status.value,
                "source": rep.source_provider}

    # ----------------------------------------------------------- approvals and execution
    def approve_recommendation(self, recommendation_id: str, approver: str) -> InvestmentRecommendation:
        if not approver.startswith("owner:"):
            raise PermissionError("only the human owner approves recommendations")
        rec = InvestmentRecommendation(**self.rt.evidence.get(recommendation_id))
        rec.owner_approval = ApprovalStatus.APPROVED
        rec.decision_status = "approved"
        self.rt.evidence.put(rec, "investment_recommendation")
        self.rt.audit.log_event("approval", "-", recommendation_id=recommendation_id, approver=approver)
        return rec

    def execute_approved(self, recommendation_id: str, order: PaperOrder, market: MarketIntelligenceReport,
                         portfolio_equity: Decimal) -> dict:
        require_validated(market)                      # data check first: unvalidated data never reaches execution
        rec = InvestmentRecommendation(**self.rt.evidence.get(recommendation_id))
        return self._call("execution_specialist", "simulate_execution", new_id("corr"), idempotency_key=f"exec:{order.order_id}",
                          order=order, market=market, recommendation=rec, portfolio_equity=portfolio_equity)

    # ------------------------------------------------------ Workflow C: rebalancing
    def workflow_c_portfolio_review(self, observations: dict, **kw) -> dict:
        rep = self._call("portfolio_analyst", "generate_performance_report", new_id("corr"), observations=observations, **kw)
        mandate = self.rt.mandates.active(self.mandate_id)
        proposals = []
        for iid, w in rep.concentration.get("weights_pct", {}).items():
            if w > float(mandate.max_position_pct):
                proposals.append({"instrument_id": iid, "action": "REDUCE", "reason": f"weight {w}% > mandate {mandate.max_position_pct}%",
                                  "owner_approval": "pending"})
        out = {"workflow": "C", "analysis_report": rep.report_id, "equity": rep.equity, "returns": rep.returns,
               "exceptions": rep.reconciliation_exceptions, "warnings": rep.warnings, "proposals": proposals}
        self.rt.evidence.put(out, "portfolio_review", obj_id=f"wc_{rep.report_id}")
        return out
