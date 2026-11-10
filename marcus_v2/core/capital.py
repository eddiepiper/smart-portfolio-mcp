"""Controlled paper-capital accounting: legacy import and effective-dated top-ups.

Nothing here resets the ledger. Every change is a new, dated, idempotent ledger
event, and every write needs a named human approver. When the legacy record
does not match what the owner states (e.g. currency), the plan reports the
discrepancy and refuses to apply until the owner resolves it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from .contracts import LedgerEvent, LedgerEventType
from .ledger import Ledger, balances


class CapitalError(Exception):
    pass


def _require_owner(approver: Optional[str]) -> None:
    if not approver or not approver.startswith("owner:"):
        raise CapitalError("capital changes need an owner approval (approver='owner:<name>')")


@dataclass
class CapitalPlan:
    portfolio_id: str
    target_capital: Decimal
    target_currency: str
    existing_contributed: dict[str, Decimal]
    proposed_events: list[LedgerEvent] = field(default_factory=list)
    discrepancies: list[str] = field(default_factory=list)
    status: str = "PENDING_APPROVAL"     # or BLOCKED

    def summary(self) -> dict:
        return {"portfolio_id": self.portfolio_id, "target": f"{self.target_currency} {self.target_capital}",
                "existing_contributed": {k: str(v) for k, v in self.existing_contributed.items()},
                "proposed": [{"type": e.event_type.value, "amount": f"{e.currency} {e.amount}", "event_at": e.event_at.isoformat(), "memo": e.memo}
                             for e in self.proposed_events],
                "discrepancies": self.discrepancies, "status": self.status}


def plan_legacy_import(trader_dir: str | Path, portfolio_id: str, *, capital_currency: str = "USD",
                       fx_rates: Optional[dict] = None, fx_source: str = "") -> tuple[list[LedgerEvent], list[str]]:
    """Translate the day-trader paper account (trader/portfolio.json + config.json) into ledger events.

    capital_currency: the currency the owner states the original budget was in. Trades in the legacy
    record are USD (US-listed shares). When the capital currency differs, each purchase is funded by a
    dated FX conversion at a validated rate from `fx_rates` ({date: units of capital currency per USD}).
    A missing rate raises CapitalError: no rate is ever assumed.
    """
    d = Path(trader_dir)
    pf = json.loads((d / "portfolio.json").read_text())
    cfg = json.loads((d / "config.json").read_text()) if (d / "config.json").exists() else {}
    notes, events = [], []
    budget = Decimal(str(cfg.get("budget", "0")))
    trade_ccy = "USD"
    if capital_currency == "USD":
        notes.append("legacy config has no currency field; budget read as USD")
    else:
        notes.append(f"owner decision: original capital was {capital_currency} {budget} (legacy config has no currency field)")
    notes.append("legacy record has no contribution date; 2026-11-03 (paper account set-up) is used and marked approximate")
    events.append(LedgerEvent(event_type=LedgerEventType.CONTRIBUTION, portfolio_id=portfolio_id, currency=capital_currency,
                              amount=budget, event_at=datetime(2026, 11, 3, tzinfo=ZoneInfo("Asia/Singapore")),
                              idempotency_key=f"legacy-contribution:{portfolio_id}",
                              memo=f"imported from trader/config.json budget ({capital_currency}, owner-confirmed); date approximate"))
    spent_capital = Decimal("0")
    for pos in pf.get("positions", []):
        for lot in pos.get("lots", []):
            qty = Decimal(str(lot["shares"]))
            cost = Decimal(str(lot["cost"]))
            when = datetime.fromisoformat(lot["date"]).replace(hour=16, tzinfo=ZoneInfo("America/New_York"))
            if capital_currency != trade_ccy:
                rate = (fx_rates or {}).get(when.date())
                if rate is None:
                    raise CapitalError(f"no validated {capital_currency}/{trade_ccy} rate for {when.date()}; cannot fund lot {lot['id']}")
                out = (cost * Decimal(str(rate))).quantize(Decimal("0.01"))
                spent_capital += out
                key = f"legacy-fx:{portfolio_id}:{lot['id']}"
                memo = f"FX for legacy lot {lot['id']}: {trade_ccy} {cost} at {rate} {capital_currency}/{trade_ccy} ({fx_source})"
                events.append(LedgerEvent(event_type=LedgerEventType.FX, portfolio_id=portfolio_id, currency=capital_currency,
                                          amount=-out, price=Decimal(str(rate)), price_source=fx_source, event_at=when,
                                          idempotency_key=key + ":out", memo=memo))
                events.append(LedgerEvent(event_type=LedgerEventType.FX, portfolio_id=portfolio_id, currency=trade_ccy,
                                          amount=cost, price=Decimal(str(rate)), price_source=fx_source, event_at=when,
                                          idempotency_key=key + ":in", memo=memo))
            else:
                spent_capital += cost
            events.append(LedgerEvent(event_type=LedgerEventType.BUY, portfolio_id=portfolio_id,
                                      instrument_id=f"US:{pos['ticker']}", quantity=qty, currency=trade_ccy, amount=-cost,
                                      price=(cost / qty).quantize(Decimal("0.01")), price_source="legacy trader/portfolio.json",
                                      event_at=when, idempotency_key=f"legacy-lot:{portfolio_id}:{lot['id']}",
                                      memo="imported legacy paper lot; legacy record holds no fee"))
    actual = Decimal(str(pf.get("cash", "0")))
    if capital_currency == trade_ccy:
        implied = budget - spent_capital
        if implied != actual:
            notes.append(f"DISCREPANCY: legacy cash {actual} != budget minus lots {implied}")
    else:
        notes.append(f"legacy cash figure {actual} was single-currency arithmetic (budget minus USD cost); "
                     f"with FX the remaining capital is {capital_currency} {budget - spent_capital}")
    return events, notes


def plan_top_up(ledger: Ledger, portfolio_id: str, target: Decimal, currency: str, effective: datetime,
                owner_stated_original: Optional[tuple[Decimal, str]] = None) -> CapitalPlan:
    state = balances(ledger.events())
    contributed = state["contributed"]
    plan = CapitalPlan(portfolio_id, target, currency, contributed)
    if not contributed:
        plan.discrepancies.append("ledger has no contributions: import the legacy record first")
        plan.status = "BLOCKED"
        return plan
    other = {c: v for c, v in contributed.items() if c != currency}
    if other:
        plan.discrepancies.append(
            f"existing contributions are in {', '.join(f'{c} {v}' for c, v in other.items())}, but the mandate is {currency}: "
            "a top-up cannot be computed until the owner confirms the original currency (or approves an FX-dated conversion)")
        plan.status = "BLOCKED"
    if owner_stated_original:
        amt, ccy = owner_stated_original
        if contributed.get(ccy) != amt:
            plan.discrepancies.append(f"owner states original capital {ccy} {amt}; ledger shows {', '.join(f'{c} {v}' for c, v in contributed.items())}")
            plan.status = "BLOCKED"
    if plan.status == "BLOCKED":
        return plan
    gap = target - contributed.get(currency, Decimal("0"))
    if gap <= 0:
        plan.discrepancies.append(f"already at or above target ({contributed.get(currency)})")
        plan.status = "BLOCKED"
        return plan
    plan.proposed_events.append(LedgerEvent(
        event_type=LedgerEventType.CONTRIBUTION, portfolio_id=portfolio_id, currency=currency, amount=gap,
        event_at=effective, idempotency_key=f"topup:{portfolio_id}:{currency}:{target}:{effective.date()}",
        memo=f"simulated capital contribution to reach {currency} {target} mandate (not investment return)"))
    return plan


def apply_events(ledger: Ledger, events: list[LedgerEvent], approver: str) -> list[LedgerEvent]:
    _require_owner(approver)
    out = []
    for e in events:
        e.memo = f"{e.memo} | approved by {approver}"
        out.append(ledger.append(e))
    return out


def apply_plan(ledger: Ledger, plan: CapitalPlan, approver: str) -> list[LedgerEvent]:
    if plan.status != "PENDING_APPROVAL":
        raise CapitalError(f"plan is {plan.status}: {plan.discrepancies}")
    return apply_events(ledger, plan.proposed_events, approver)
