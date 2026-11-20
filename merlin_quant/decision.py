"""Stage C synthesis and the paper-trade proposal.

The joint assessment quotes each lead verbatim (it never rewrites a lead's
assessment) and lists agreements and disagreements side by side. The proposed
action comes from the decision policy in config, applied by fixed rules; sizing
is integer arithmetic on the ledger's NAV. No model output is parsed into an
order: the only model-derived inputs are the two closed-enum stances, the
convictions and the challenge verdict.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from merlin_quant.config import Config
from merlin_quant.contracts import (Action, ChallengeReport, JointAssessment, MerlinReport, QuantReport, Stance,
                                    TradeProposal, Verdict, stable_id)
from merlin_quant.data import MarketData
from merlin_quant.ledger import State


def synthesize(m: MerlinReport, q: QuantReport, c: ChallengeReport, cfg: Config, held_weight: float) -> JointAssessment:
    p = cfg.decision_policy
    agree, disagree = [], []
    if m.stance == q.stance:
        agree.append(f"Both leads are {m.stance.value}.")
    else:
        disagree.append(f"Merlin is {m.stance.value} on fundamentals; Quant is {q.stance.value} on the market "
                        f"(TradingAgents rating {q.tradingagents_rating}).")
    for ch in c.challenges:
        (disagree if ch.severity in ("high", "medium") else agree).append(
            f"[{ch.topic}/{ch.severity}] {ch.finding}" if ch.severity != "low" else f"Minor: {ch.finding}")

    bull_m = m.stance == Stance.BULLISH and m.conviction >= p.min_conviction
    if c.verdict == Verdict.INCONCLUSIVE or Stance.INSUFFICIENT_EVIDENCE in (m.stance, q.stance):
        action, w, why = Action.NO_ACTION, held_weight, "Inconclusive or insufficient evidence: no action."
    elif c.verdict == Verdict.DISAGREE:
        if held_weight > 0 and q.stance == Stance.BEARISH:
            w = min(held_weight, p.partial_target_weight)
            action = Action.REDUCE if w < held_weight else Action.HOLD
            why = "Leads disagree and Quant is bearish: trim an existing position to the partial weight."
        else:
            action, w = (Action.HOLD if held_weight > 0 else Action.NO_ACTION), held_weight
            why = "Leads disagree: disagreement never adds exposure under the policy."
    elif bull_m and q.stance != Stance.BEARISH:
        w = p.full_target_weight if c.verdict == Verdict.AGREE else p.partial_target_weight
        action = Action.BUY if w > held_weight else Action.HOLD
        why = f"Merlin BULLISH (conviction {m.conviction:.2f}) and Quant {c.verdict.value}: target {w:.0%}."
    elif m.stance == Stance.BEARISH and q.stance == Stance.BEARISH and held_weight > 0:
        action, w, why = Action.SELL, 0.0, "Both leads bearish with a position held: exit."
    else:
        action, w, why = (Action.HOLD if held_weight > 0 else Action.NO_ACTION), held_weight, \
            "No rule calls for a change."
    return JointAssessment(
        assessment_id=stable_id("joint", {"m": m.report_id, "q": q.report_id, "c": c.report_id, "cfg": cfg.version,
                                          "held": round(held_weight, 6)}),
        instrument=m.instrument, merlin_report_id=m.report_id, quant_report_id=q.report_id,
        challenge_report_id=c.report_id, merlin_thesis=m.thesis, merlin_stance=m.stance,
        quant_assessment=q.technical_assessment[:1500], quant_stance=q.stance, challenge_verdict=c.verdict,
        quant_challenges=[f"[{x.severity}] {x.finding}" for x in c.challenges], agreements=agree,
        disagreements=disagree, unresolved_risks=list(m.material_risks) + [x.finding for x in c.challenges
                                                                          if x.topic == "downside_risks"],
        disagreement_preserved=bool(disagree) or m.stance == q.stance,
        policy_applied=p.rules, proposed_action=action, proposed_target_weight=round(w, 6), rationale=why)


def build_proposal(j: JointAssessment, m: MerlinReport, q: QuantReport, c: ChallengeReport, st: State,
                   md: MarketData, cfg: Config, now: datetime | None = None) -> TradeProposal:
    """Deterministic sizing from the target weight; returns a schema-validated proposal."""
    cutoff = q.data_cutoff
    price, pdate = md.view(cutoff).close(j.instrument, cfg.risk.max_price_staleness_sessions)
    nav = st.cash + sum(h.quantity * (md.raw_close(s, cutoff) or (0, None))[0]
                        for s, h in st.holdings.items() if h.quantity)
    held = st.holdings[j.instrument].quantity if j.instrument in st.holdings else 0
    target_qty = math.floor(j.proposed_target_weight * nav / price)
    action, qty = j.proposed_action, 0
    if action == Action.BUY:
        qty = target_qty - held
        # Fee-aware: the buy's commission comes out of the same budget, so shares + commission stay within the
        # target weight's dollars (with a $15 minimum this can be one share fewer on a small order).
        budget = j.proposed_target_weight * nav - held * price
        while qty > 0 and qty * price + cfg.costs.commission(qty, price) > budget:
            qty -= 1
    elif action == Action.REDUCE:
        qty = held - target_qty
    elif action == Action.SELL:
        qty = held
    if action in (Action.BUY, Action.REDUCE, Action.SELL) and qty <= 0:
        action, qty = (Action.HOLD if held else Action.NO_ACTION), 0
    reasons = [] if action in (Action.BUY, Action.SELL, Action.REDUCE) else [f"{action.value} is not an order"]
    now = now or datetime.now(timezone.utc)
    return TradeProposal(
        proposal_id=stable_id("prop", {"j": j.assessment_id, "action": action.value, "qty": qty, "cutoff": cutoff}),
        instrument=j.instrument, market=m.market, decision_timestamp=now, data_cutoff=cutoff,
        merlin_report_id=m.report_id, quant_report_id=q.report_id, challenge_report_id=c.report_id,
        assessment_id=j.assessment_id, challenge_outcome=c.verdict, action=action, quantity=qty,
        target_weight=j.proposed_target_weight, reference_price=price, reference_price_date=pdate,
        reference_price_source=md.source_name, thesis=f"{j.rationale} Merlin: {m.thesis[:400]}",
        risk_considerations=[*j.unresolved_risks[:6]] or ["none recorded"],
        position_context={"held_quantity": float(held), "nav": round(nav, 2), "cash": round(st.cash, 2),
                          "target_quantity": float(target_qty)},
        eligibility="ELIGIBLE" if not reasons else "NOT_ELIGIBLE", eligibility_reasons=reasons,
        estimated_costs=cfg.costs.fee_estimate(qty, price) if qty > 0 else {})
