from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from marcus_v2.agents.data_guardian.agent import DataGuardian
from marcus_v2.agents.execution_specialist.agent import ExecutionSpecialist
from marcus_v2.agents.execution_specialist.engine import CostModel
from marcus_v2.agents.market_intelligence.agent import MarketIntelligence
from marcus_v2.core.contracts import (Action, ApprovalStatus, InvestmentRecommendation, LedgerEvent, LedgerEventType,
                                      OrderType, PaperOrder)
from marcus_v2.core.ledger import LedgerError, balances
from marcus_v2.core.mandate import default_v2_mandate
from marcus_v2.integrations import lean_adapter
from marcus_v2.integrations.openbb_adapter import FixtureProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 11, 10, 7, 30, tzinfo=timezone.utc)
DECISION = datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc)      # after the Nov 3 close


@pytest.fixture
def env(rt):
    # test mandate effective before the Nov 3 decisions (the real V2 mandate starts 2026-11-10)
    rt.mandates.propose(default_v2_mandate(1).model_copy(update={"effective_from": date(2026, 11, 1)}))
    rt.mandates.approve("marcus-paper", 1, "owner:edward")
    led = rt.ledger_writer()
    led.append(LedgerEvent(event_type=LedgerEventType.CONTRIBUTION, portfolio_id="marcus-paper", currency="USD",
                           amount=Decimal("15000"), event_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
                           idempotency_key="test-funding", memo="test funding (USD paper cash)"))
    mi = MarketIntelligence(rt, providers={"fixture": FixtureProvider(FIXTURES / "bars.json")}, clock=lambda: NOW)
    dg = DataGuardian(rt, clock=lambda: NOW)
    ex = ExecutionSpecialist(rt, costs=CostModel()).register()

    def market(iid, start=date(2026, 11, 1), end=date(2026, 11, 10)):
        r = mi.get_historical_prices(iid, start, end)
        dg.validate_report(r, purpose="research")
        return r
    return rt, ex, market


def rec(approved=True, iid="US:BAC"):
    return InvestmentRecommendation(instrument_id=iid, action=Action.BUY, mandate_id="marcus-paper", mandate_version=1,
                                    owner_approval=ApprovalStatus.APPROVED if approved else ApprovalStatus.PENDING)


def order(r, **kw):
    base = dict(order_id=kw.pop("order_id", "ord-1"), portfolio_id="marcus-paper", instrument_id=r.instrument_id, side="buy",
                quantity=Decimal(4), order_type=OrderType.LIMIT, limit_price=Decimal("50.90"),
                recommendation_id=r.recommendation_id, mandate_id="marcus-paper", mandate_version=1,
                decision_at=DECISION, currency="USD", expires_on=date(2026, 11, 9))
    base.update(kw)
    return PaperOrder(**base)


def test_gold_close_above_trigger_does_not_prove_4005_fill(env):
    rt, ex, market = env
    r = rec(iid="COMEX:GC")
    o = order(r, instrument_id="COMEX:GC", quantity=Decimal(1), limit_price=Decimal("4005"), planned_entry=Decimal("4005"),
              trigger={"type": "daily_close_above", "level": "3994"})
    rep = ex.simulate_execution(o, market("COMEX:GC"), r, portfolio_equity=Decimal("15000"))
    f = rep["fill"]
    assert "trigger_met_on_2026-11-04" in f["constraints_tested"]          # close 4073 > 3994
    assert f["status"] in ("EXPIRED", "NO_FILL") and f["price"] is None    # later lows (>= 4035) never reached 4005
    assert balances(rt.ledger_reader().events())["positions"] == {}


def test_limit_buy_fills_at_better_of_open_and_limit_with_fees(env):
    rt, ex, market = env
    r = rec()
    rep = ex.simulate_execution(order(r), market("US:BAC"), r, portfolio_equity=Decimal("15000"))
    f = rep["fill"]
    # Nov 4 BAC opened 51.26 (above 50.90) and traded down to 50.62 -> fills at the limit, 50.90
    assert f["status"] == "FILLED" and Decimal(f["price"]) == Decimal("50.90") and f["bar_date"] == "2026-11-04"
    assert Decimal(f["fees"]) == Decimal("15.00")                          # USD 15 minimum commission
    s = balances(rt.ledger_reader().events())
    assert s["positions"]["US:BAC"]["quantity"] == 4
    assert s["cash"]["USD"] == Decimal("15000") - Decimal("203.60") - Decimal("15.00")
    assert ex.reconcile_paper_fill("ord-1")["status"] == "RECONCILED"


def test_duplicate_order_id_rejected_without_second_fill(env):
    rt, ex, market = env
    r = rec()
    ex.simulate_execution(order(r), market("US:BAC"), r, portfolio_equity=Decimal("15000"))
    again = ex.simulate_execution(order(r), market("US:BAC"), r, portfolio_equity=Decimal("15000"))
    assert again["fill"]["status"] == "REJECTED" and "duplicate" in again["fill"]["reason"]
    assert len([e for e in rt.ledger_reader().events() if e.event_type == LedgerEventType.BUY]) == 1


def test_insufficient_cash(env):
    rt, ex, market = env
    r = rec()
    rep = ex.simulate_execution(order(r, quantity=Decimal(400), order_id="big"), market("US:BAC"), r, portfolio_equity=Decimal("15000"))
    assert rep["fill"]["status"] == "REJECTED" and "insufficient" in rep["fill"]["reason"]


def test_unapproved_or_wrong_mandate_rejected(env):
    rt, ex, market = env
    r = rec(approved=False)
    assert ex.simulate_execution(order(r), market("US:BAC"), r, Decimal("15000"))["fill"]["status"] == "REJECTED"
    r2 = rec()
    rep = ex.simulate_execution(order(r2, order_id="o2", mandate_version=7), market("US:BAC"), r2, Decimal("15000"))
    assert rep["fill"]["status"] == "REJECTED" and "mandate" in rep["fill"]["reason"]


def test_gap_through_stop_fills_at_open_not_stop(env):
    rt, ex, market = env
    r = rec()
    ex.simulate_execution(order(r), market("US:BAC"), r, Decimal("15000"))            # own 4 BAC
    m = market("US:BAC")
    nov5 = [x for x in m.observations if x.bar_date == date(2026, 11, 5)][0]          # O 52.65 H 53.03 L 49.76
    gapped = nov5.model_copy(update={"open": Decimal("47.90"), "low": Decimal("47.50")})
    m.observations = [x if x.bar_date != date(2026, 11, 5) else gapped for x in m.observations]
    stop = order(r, order_id="stop-1", side="sell", order_type=OrderType.STOP, stop_price=Decimal("48.50"),
                 limit_price=None, decision_at=datetime(2026, 11, 4, 21, 30, tzinfo=timezone.utc))
    f = ex.simulate_execution(stop, m, r, Decimal("15000"))["fill"]
    assert f["status"] == "FILLED" and Decimal(f["price"]) < Decimal("47.90")         # open minus slippage, not 48.50


def test_expiry(env):
    rt, ex, market = env
    r = rec()
    o = order(r, order_id="cheap", limit_price=Decimal("40.00"), expires_on=date(2026, 11, 6))
    assert ex.simulate_execution(o, market("US:BAC"), r, Decimal("15000"))["fill"]["status"] == "EXPIRED"


def test_missing_bar_is_unverifiable_not_substituted(env):
    rt, ex, market = env
    r = rec()
    m = market("US:BAC")
    m.observations = [x for x in m.observations if x.bar_date != date(2026, 11, 4)]   # drop the fill day
    f = ex.simulate_execution(order(r, order_id="gap"), m, r, Decimal("15000"))["fill"]
    assert f["status"] == "UNVERIFIABLE" and "2026-11-04" in f["reason"] and f["price"] is None


def test_inconsistent_timestamps_and_unvalidated_data(env):
    rt, ex, market = env
    r = rec()
    m = market("US:BAC")
    m.observations[-1] = m.observations[-1].model_copy(update={"instrument_id": "US:PFE"})
    assert ex.simulate_execution(order(r, order_id="ts"), m, r, Decimal("15000"))["fill"]["status"] == "UNVERIFIABLE"
    raw = market("US:BAC")
    raw.data_quality_status = None
    assert ex.simulate_execution(order(r, order_id="nv"), raw, r, Decimal("15000"))["fill"]["status"] == "UNVERIFIABLE"


def test_no_chase(env):
    rt, ex, market = env
    r = rec()
    o = order(r, order_id="chase", order_type=OrderType.MARKET, limit_price=None, planned_entry=Decimal("49.00"))
    f = ex.simulate_execution(o, market("US:BAC"), r, Decimal("15000"))["fill"]
    assert f["status"] == "NO_FILL" and "no-chase" in f["reason"]


def test_interrupted_ledger_write_then_reconcile(env, monkeypatch):
    rt, ex, market = env
    r = rec()
    real_append = ex.ledger.append
    monkeypatch.setattr(ex.ledger, "append", lambda e: real_append(e, _crash_after_wal=True))
    with pytest.raises(LedgerError):
        ex.simulate_execution(order(r, order_id="crash"), market("US:BAC"), r, Decimal("15000"))
    ex2 = ExecutionSpecialist(rt)                                   # restart: recovery quarantines the torn write
    assert any("NOT committed" in n for n in ex2.ledger.recovery_notes)
    assert ex2.reconcile_paper_fill("crash")["status"] == "unknown"  # no fill record, no ledger event: nothing invented
    again = ex2.simulate_execution(order(r, order_id="crash"), market("US:BAC"), r, Decimal("15000"))
    assert again["fill"]["status"] == "FILLED" and ex2.reconcile_paper_fill("crash")["status"] == "RECONCILED"


def test_lean_adapter_backtest_only():
    cmd = lean_adapter.build_command("backtest", "/tmp/alg")
    assert "--network" in cmd and "none" in cmd
    from marcus_v2.core.permissions import LiveTradingForbidden
    for verb in ("live", "deploy", "cloud"):
        with pytest.raises(LiveTradingForbidden):
            lean_adapter.build_command(verb, "/tmp/alg")
    src = lean_adapter.render_algorithm("BAC", date(2026, 11, 3), date(2026, 11, 9), 15000, "buy", 4, "limit", 50.9)
    assert "LimitOrder" in src and "Brokerage" not in src and "live" not in src.lower()
