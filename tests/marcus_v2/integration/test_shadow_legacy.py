"""Shadow comparison: V2 vs the existing day-trader paper workflow on identical inputs.

Legacy record (trader/portfolio.json): BAC Plan B — buy 4 at a 50.90 limit the session after a daily
close above 50.71; recorded filled 2026-11-05 at 50.90, no commission recorded.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from marcus_v2.agents.data_guardian.agent import DataGuardian
from marcus_v2.agents.execution_specialist.agent import ExecutionSpecialist
from marcus_v2.agents.market_intelligence.agent import MarketIntelligence
from marcus_v2.core import capital
from marcus_v2.core.contracts import Action, ApprovalStatus, InvestmentRecommendation, OrderType, PaperOrder
from marcus_v2.core.mandate import default_v2_mandate
from marcus_v2.integrations.openbb_adapter import FixtureProvider

FIX = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 11, 10, 7, 30, tzinfo=timezone.utc)


def test_v2_reproduces_legacy_bac_plan_b_fill(rt):
    rt.mandates.propose(default_v2_mandate(1).model_copy(update={"effective_from": date(2026, 11, 1)}))
    rt.mandates.approve("marcus-paper", 1, "owner:edward")
    capital.apply_events(rt.ledger_writer(), [capital.LedgerEvent(
        event_type=capital.LedgerEventType.CONTRIBUTION, portfolio_id="marcus-paper", currency="USD", amount=Decimal("500"),
        event_at=datetime(2026, 11, 3, tzinfo=timezone.utc), idempotency_key="shadow-500")], "owner:edward")
    mi = MarketIntelligence(rt, providers={"fixture": FixtureProvider(FIX / "bars.json")}, clock=lambda: NOW)
    dg = DataGuardian(rt, clock=lambda: NOW)
    mkt = mi.get_historical_prices("US:BAC", date(2026, 11, 1), date(2026, 11, 9))
    dg.validate_report(mkt, purpose="execution")
    rec = InvestmentRecommendation(instrument_id="US:BAC", action=Action.BUY, mandate_id="marcus-paper", mandate_version=1,
                                   owner_approval=ApprovalStatus.APPROVED)
    # legacy sizing: max loss $10 / (50.90 - 48.50) = 4 shares; V2 with 2% of 500 equity gives the same
    assert (Decimal("500") * Decimal("2") / 100 / (Decimal("50.90") - Decimal("48.50"))).to_integral_value(rounding="ROUND_FLOOR") == 4
    order = PaperOrder(order_id="shadow-bac", portfolio_id="marcus-paper", instrument_id="US:BAC", side="buy", quantity=Decimal(4),
                       order_type=OrderType.LIMIT, limit_price=Decimal("50.90"), planned_entry=Decimal("50.90"),
                       trigger={"type": "daily_close_above", "level": "50.71"}, recommendation_id=rec.recommendation_id,
                       mandate_id="marcus-paper", mandate_version=1, decision_at=datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc),
                       currency="USD", expires_on=date(2026, 11, 9))
    # the legacy $500 account cannot afford the V2 cost model's USD 15 minimum within the 5% cash buffer? it can:
    # 203.60 + 15.00 = 218.60 < 500 - 25 buffer
    rep = ExecutionSpecialist(rt).simulate_execution(order, mkt, rec, Decimal("500"))
    f = rep["fill"]
    assert f["status"] == "FILLED" and f["bar_date"] == "2026-11-05" and f["price"] == "50.90"   # same as legacy
    assert f["fees"] == "15.00"        # DIFFERENCE: legacy recorded no commission; V2 applies the broker's USD 15 minimum
