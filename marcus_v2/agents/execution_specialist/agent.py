"""Execution Specialist agent (foundation: QuantConnect LEAN fill semantics, isolated).

Determines whether an authorised paper order can be filled and records the
result. Deterministic code computes every number (engine.simulate); the agent
only orchestrates and explains. It is the only agent holding a ledger writer.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional

from ...core.contracts import (FillStatus, InvestmentRecommendation, LedgerEvent, LedgerEventType,
                               MarketIntelligenceReport, PaperFill, PaperOrder, QualityStatus)
from ...core.ledger import balances
from ...core.permissions import assert_no_live_trading
from ..base import Agent
from .engine import Context, CostModel, simulate


class ExecutionSpecialist(Agent):
    agent_id = "execution_specialist"

    def __init__(self, rt, costs: Optional[CostModel] = None):
        super().__init__(rt)
        self.costs = costs or CostModel()
        self.ledger = rt.ledger_writer()

    def register(self):
        self.tool("simulate_execution", self.simulate_execution, "simulate_paper_execution", state_changing=True, timeout_s=120)
        self.tool("check_available_cash", self.check_available_cash, "read_ledger")
        self.tool("estimate_transaction_cost", self.estimate_transaction_cost, "simulate_paper_execution")
        self.tool("check_order_status", self.check_order_status, "read_ledger")
        self.tool("reconcile_paper_fill", self.reconcile_paper_fill, "read_ledger")
        self.tool("explain_execution_exception", self.explain_execution_exception, "simulate_paper_execution")
        return self

    # ------------------------------------------------------------------ queries
    def check_available_cash(self, currency: str) -> Decimal:
        return balances(self.ledger.events())["cash"].get(currency, Decimal("0"))

    def estimate_transaction_cost(self, notional: Decimal) -> Decimal:
        return self.costs.commission(Decimal(notional))

    def _fills_dir(self):
        d = self.rt.home / "fills"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def check_order_status(self, order_id: str) -> Optional[dict]:
        p = self._fills_dir() / f"{order_id}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def _processed(self) -> set[str]:
        """Orders with a terminal outcome. NO_FILL / UNVERIFIABLE stay re-evaluable when new data arrives."""
        out = set()
        for p in self._fills_dir().glob("*.json"):
            if json.loads(p.read_text()).get("status") in ("FILLED", "REJECTED", "EXPIRED"):
                out.add(p.stem)
        return out

    # ------------------------------------------------------------------ main tool
    def simulate_execution(self, order: PaperOrder, market: MarketIntelligenceReport,
                           recommendation: InvestmentRecommendation, portfolio_equity: Decimal) -> dict:
        assert_no_live_trading(json.dumps(order.model_dump(mode="json")))
        if market.data_quality_status not in (QualityStatus.VALID, QualityStatus.WARNING):
            fill = PaperFill(order_id=order.order_id, status=FillStatus.UNVERIFIABLE,
                             reason=f"market data not validated by Data Guardian ({market.data_quality_status})")
            return self._report(order, fill)
        state = balances(self.ledger.events())
        pos = state["positions"].get(order.instrument_id, {}).get("quantity", Decimal("0"))
        mandate = None
        try:
            mandate = self.rt.mandates.active(order.mandate_id, order.decision_at.date())
        except Exception:  # noqa: BLE001 - reported by the engine as a mandate failure
            pass
        ctx = Context(cash_available=state["cash"].get(order.currency, Decimal("0")),
                      portfolio_equity=Decimal(portfolio_equity), position_quantity=pos,
                      processed_order_ids=self._processed(),
                      approved=recommendation.owner_approval.value == "approved" and recommendation.recommendation_id == order.recommendation_id,
                      active_mandate=mandate)
        fill = simulate(order, market.observations, ctx, self.costs)
        if fill.status == FillStatus.FILLED:
            notional = fill.price * fill.quantity
            amount = -(notional + fill.fees) if order.side == "buy" else (notional - fill.fees)
            ev = self.ledger.append(LedgerEvent(
                event_type=LedgerEventType.BUY if order.side == "buy" else LedgerEventType.SELL,
                portfolio_id=order.portfolio_id, instrument_id=order.instrument_id, quantity=fill.quantity,
                currency=order.currency, amount=amount.quantize(Decimal("0.01")), price=fill.price,
                price_source=fill.price_source, event_at=_bar_dt(fill.bar_date), decision_ref=order.recommendation_id,
                execution_ref=fill.fill_id, idempotency_key=f"fill:{order.order_id}",
                memo=f"paper {order.order_type.value} {order.side}; fees {fill.fees}; slippage {fill.slippage}/sh"))
            fill.ledger_event_id = ev.event_id
        if fill.status != FillStatus.REJECTED or "duplicate" not in fill.reason:
            (self._fills_dir() / f"{order.order_id}.json").write_text(fill.model_dump_json(indent=2))
        return self._report(order, fill)

    def _report(self, order: PaperOrder, fill: PaperFill) -> dict:
        rep = {"report": "PAPER_EXECUTION_REPORT", "paper_order_id": order.order_id,
               "instrument_id": order.instrument_id, "side": order.side, "quantity": str(order.quantity),
               "eligibility": fill.status.value, "constraints_tested": fill.constraints_tested,
               "assumptions": {"fill_model": "daily bars, LEAN-compatible: market at next open; limit at better of open/limit; stop at worse of open/stop",
                               "commission": f"{self.costs.commission_pct * 100}% min {self.costs.min_commission} {self.costs.currency}",
                               "slippage_pct": str(self.costs.slippage_pct * 100)},
               "fill": fill.model_dump(mode="json"),
               "remaining_cash": str(self.check_available_cash(order.currency)), "currency": order.currency}
        self.rt.evidence.put(rep, "paper_execution_report", obj_id=f"per_{order.order_id}")
        return rep

    def reconcile_paper_fill(self, order_id: str) -> dict:
        """Cross-check the fill record against the ledger (after a crash or restart)."""
        rec = self.check_order_status(order_id)
        led = [e for e in self.ledger.events() if e.idempotency_key == f"fill:{order_id}"]
        if rec is None and not led:
            return {"order_id": order_id, "status": "unknown"}
        if rec and rec["status"] == "FILLED" and not led:
            return {"order_id": order_id, "status": "EXCEPTION", "detail": "fill recorded but no ledger event"}
        if led and (rec is None or rec["status"] != "FILLED"):
            return {"order_id": order_id, "status": "EXCEPTION", "detail": "ledger event without a fill record"}
        if led and Decimal(rec["price"]) != led[0].price:
            return {"order_id": order_id, "status": "EXCEPTION", "detail": "fill price differs from ledger"}
        return {"order_id": order_id, "status": "RECONCILED", "ledger_event": led[0].event_id if led else None,
                "ledger_recovery_notes": self.ledger.recovery_notes}

    def explain_execution_exception(self, report: dict) -> str:
        f = report["fill"]
        if f["status"] == "FILLED":
            return f"Filled {f['quantity']} at {f['price']} on {f['bar_date']} (fees {f['fees']})."
        resp = self.rt.llm.complete(self.instructions or "Explain paper execution outcomes plainly.",
                                    f"Outcome {f['status']}: {f['reason']}. Constraints tested: {f['constraints_tested']}")
        return resp.text or f"{f['status']}: {f['reason']}"


def _bar_dt(d):
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    return datetime.combine(d, time(16, 0), ZoneInfo("America/New_York"))
