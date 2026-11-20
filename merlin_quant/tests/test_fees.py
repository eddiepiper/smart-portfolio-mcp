"""Broker fee schedule: staff online rate, 0.06% of trade value, minimum USD 15, on every buy and sell."""

import json
from datetime import date
from pathlib import Path

import pytest

from merlin_quant.config import EXAMPLE_CONFIG, Config, commission_for, load_config
from merlin_quant.contracts import Action
from merlin_quant.engine import PaperEngine
from merlin_quant.tests.conftest import CUTOFF, NOW, cfg_with


def _cfg(costs: dict | None = None, risk: dict | None = None) -> Config:
    data = json.loads(Path(EXAMPLE_CONFIG).read_text())
    data["costs"].update(costs or {})
    data["risk"].update(risk or {})
    return Config.model_validate(data)


def _team(tmp_path, cfg, md):
    from merlin_quant.workflow import Team
    t = Team(tmp_path / "home", cfg, md, runtime_override="scripted")
    t.engine.init()
    t.engine.open()
    return t


def test_example_config_is_the_staff_rate():
    c = load_config().costs
    assert (c.commission_rate, c.min_commission, c.commission_per_share) == (0.0006, 15.0, 0.0)
    assert "0.06%" in c.basis and "USD 15" in c.basis


@pytest.mark.parametrize("qty,price,fee", [
    (6, 156.14, 15.0),           # the ~$937 NVDA idea: 0.06% is $0.56, so the $15 minimum applies
    (12, 166.0, 15.0),           # a $2,000 order: still the minimum
    (100, 250.0, 15.0),          # exactly $25,000: 0.06% = $15, the break point
    (200, 250.0, 30.0),          # $50,000: the 0.06% wins
    (0, 100.0, 0.0),             # no trade, no fee
])
def test_commission_is_max_of_rate_and_minimum(qty, price, fee):
    assert commission_for(qty, price, per_share=0.0, rate=0.0006, minimum=15.0) == pytest.approx(fee)
    assert load_config().costs.commission(qty, price) == pytest.approx(fee)


def test_older_config_without_rate_keeps_old_fees():
    # an owner config written before this change has no commission_rate: fees stay per-share + minimum
    c = _cfg({"commission_rate": 0.0, "commission_per_share": 0.005, "min_commission": 1.0}).costs
    data = json.loads(Path(EXAMPLE_CONFIG).read_text())
    del data["costs"]["commission_rate"], data["costs"]["basis"]
    assert Config.model_validate(data).costs.commission_rate == 0.0
    assert c.commission(1000, 50.0) == pytest.approx(5.0)
    assert c.commission(10, 50.0) == pytest.approx(1.0)


def test_buy_and_sell_each_pay_the_minimum(team, md):
    m, q, c, j, p = team.research("AAPL", CUTOFF, now=NOW)
    assert p.action == Action.BUY
    team.approve(p.proposal_id, "test-owner")
    buy, r = team.execute(p.proposal_id, now=NOW)
    assert r.outcome == "PASS"
    assert buy.commission == pytest.approx(max(0.0006 * buy.quantity * buy.fill_price, 15.0))
    assert "0.0600% of value" in buy.fill_basis and "min 15" in buy.fill_basis
    assert buy.net_cash_change == pytest.approx(-(buy.gross_value + buy.commission), abs=0.01)

    sell_p = p.model_copy(update={"proposal_id": "prop-trim", "action": Action.REDUCE, "quantity": 3,
                                  "status": "APPROVED"})
    team.engine.records.put("proposals", sell_p.proposal_id, sell_p)
    sell, r = team.execute(sell_p.proposal_id, now=NOW)
    assert r.outcome == "PASS" and sell.commission == pytest.approx(15.0)  # 3 shares: the minimum
    st = team.engine.ledger.state()
    assert st.fees == pytest.approx(buy.commission + sell.commission)
    basis_per = (buy.quantity * buy.fill_price + buy.commission) / buy.quantity
    assert st.realized_pnl == pytest.approx(3 * sell.fill_price - 15.0 - 3 * basis_per)  # net of both fees
    team.engine.reconcile()


def test_proposal_is_sized_net_of_commission_and_shows_fees(team):
    m, q, c, j, p = team.research("AAPL", CUTOFF, now=NOW)
    nav = p.position_context["nav"]
    e = p.estimated_costs
    assert e["commission_one_side"] == pytest.approx(team.cfg.costs.commission(p.quantity, p.reference_price))
    assert e["commission_round_trip"] == pytest.approx(2 * e["commission_one_side"])
    assert e["round_trip_fee_pct"] == pytest.approx(e["commission_round_trip"] / e["order_value"], rel=1e-4)
    assert e["breakeven_move_pct"] > e["round_trip_fee_pct"]  # spread on top of commission
    assert p.quantity * p.reference_price + e["commission_one_side"] <= p.target_weight * nav + 1e-9
    report = next((team.engine.home / "reports").glob("*AAPL*.md")).read_text()
    assert "Estimated broker fees" in report and "break even" in report


def test_sizing_drops_a_share_when_the_fee_would_overshoot(tmp_path, md):
    from merlin_quant.decision import build_proposal
    from merlin_quant.ledger import State
    cfg = _cfg({"min_commission": 50.0})
    team = _team(tmp_path, cfg, md)
    m, q, c, j, p0 = team.research("AAPL", CUTOFF, now=NOW)
    price = p0.reference_price
    # set NAV so the target weight buys exactly k shares before fees; the fee must cost at least one share
    k = 20
    nav = (k * price + 1.0) / j.proposed_target_weight
    st = State(initialized=True, initial_capital=nav, cash=nav)
    p = build_proposal(j, m, q, c, st, md, cfg, now=NOW)
    assert p.quantity < k
    assert p.quantity * price + 50.0 <= j.proposed_target_weight * nav


def test_risk_gate_fee_limit_rejects_small_buys_only_when_set(tmp_path, md):
    t = _team(tmp_path / "a", _cfg(), md)
    m, q, c, j, p = t.research("AAPL", CUTOFF, now=NOW)
    t.approve(p.proposal_id, "test-owner")
    small = p.model_copy(update={"proposal_id": "prop-small", "quantity": 4, "status": "APPROVED"})
    t.engine.records.put("proposals", small.proposal_id, small)
    rec, r = t.execute(small.proposal_id, now=NOW)
    drag = next(x for x in r.checks if x.name == "commission_drag")
    assert drag.passed and "no limit set" in drag.detail and "break even" in drag.detail

    t2 = _team(tmp_path / "b", _cfg(risk={"max_round_trip_fee_pct": 0.02}), md)
    m, q, c, j, p = t2.research("AAPL", CUTOFF, now=NOW)
    small = p.model_copy(update={"proposal_id": "prop-small", "quantity": 4, "status": "APPROVED"})
    t2.engine.records.put("proposals", small.proposal_id, small)
    rec, r = t2.execute(small.proposal_id, now=NOW)
    assert rec is None and r.outcome == "REJECT"
    assert any(x.startswith("commission_drag") and "limit 2.00%" in x for x in r.reasons)


def test_cash_check_counts_the_commission(team):
    m, q, c, j, p = team.research("AAPL", CUTOFF, now=NOW)
    from merlin_quant import risk as risk_gate
    st = team.engine.ledger.state()
    r = risk_gate.evaluate(p, st, team.cfg, team.md, last_completed_session=CUTOFF, executed_proposals=set(),
                           orders_this_session=0, now=NOW)
    cash = next(x for x in r.checks if x.name == "available_cash")
    assert "incl. commission 15.00" in cash.detail


def test_changing_the_fee_schedule_never_rewrites_recorded_fills(tmp_path, md):
    cheap = _cfg({"commission_rate": 0.0, "min_commission": 1.0, "commission_per_share": 0.005})
    t = _team(tmp_path, cheap, md)
    m, q, c, j, p = t.research("AAPL", CUTOFF, now=NOW)
    t.approve(p.proposal_id, "test-owner")
    rec, _ = t.execute(p.proposal_id, now=NOW)
    old_fee = rec.commission
    assert old_fee < 15.0
    # reopen the same portfolio under the staff schedule: the old fill keeps the fee it was charged
    eng = PaperEngine(tmp_path / "home", _cfg(), md).open()
    st = eng.ledger.state()
    assert st.fees == pytest.approx(old_fee)
    assert any(e["type"] == "CONFIG" for e in eng.ledger.events())  # the schedule change is itself on record
    snap = eng.snapshot(date(2026, 11, 13))
    assert snap.fees_paid == pytest.approx(old_fee, abs=0.01)
    assert snap.estimated_exit_fees == pytest.approx(15.0)  # selling it later would pay the new minimum
