from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from marcus_v2.agents.data_guardian.agent import DataGuardian
from marcus_v2.agents.market_intelligence.agent import MarketIntelligence
from marcus_v2.agents.portfolio_analyst.agent import PortfolioAnalyst
from marcus_v2.core import capital
from marcus_v2.core.contracts import LedgerEvent, LedgerEventType
from marcus_v2.core.ledger import balances
from marcus_v2.core.permissions import PermissionDenied, require
from marcus_v2.integrations.openbb_adapter import FixtureProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 11, 10, 7, 30, tzinfo=timezone.utc)


@pytest.fixture
def market(rt):
    mi = MarketIntelligence(rt, providers={"fixture": FixtureProvider(FIXTURES / "bars.json")}, clock=lambda: NOW)
    dg = DataGuardian(rt, clock=lambda: NOW)

    def get(iid, start=date(2026, 10, 1), end=date(2026, 11, 9)):
        r = mi.get_historical_prices(iid, start, end)
        dg.validate_report(r, purpose="research")
        return r.observations
    return get


@pytest.fixture
def legacy(rt):
    events, notes = capital.plan_legacy_import(FIXTURES / "legacy_trader", rt.portfolio_id)
    capital.apply_events(rt.ledger_writer(), events, "owner:edward")
    return notes


def test_legacy_import_preserves_history(rt, legacy):
    s = balances(rt.ledger_reader().events())
    assert s["cash"]["USD"] == Decimal("296.40")                 # matches trader/portfolio.json
    assert s["positions"]["US:BAC"]["quantity"] == 4 and s["contributed"]["USD"] == Decimal("500")
    assert any("approximate" in n for n in legacy) and not any("DISCREPANCY" in n for n in legacy)


def test_import_requires_owner(rt):
    events, _ = capital.plan_legacy_import(FIXTURES / "legacy_trader", rt.portfolio_id)
    with pytest.raises(capital.CapitalError):
        capital.apply_events(rt.ledger_writer(), events, "marcus")


def test_sgd_top_up_blocked_on_currency_discrepancy(rt, legacy):
    plan = capital.plan_top_up(rt.ledger_writer(), rt.portfolio_id, Decimal("20000"), "SGD",
                               datetime(2026, 11, 10, tzinfo=ZoneInfo("Asia/Singapore")),
                               owner_stated_original=(Decimal("500"), "SGD"))
    assert plan.status == "BLOCKED" and any("USD" in d for d in plan.discrepancies)
    with pytest.raises(capital.CapitalError):
        capital.apply_plan(rt.ledger_writer(), plan, "owner:edward")
    assert len(rt.ledger_reader().events()) == 2                  # nothing written


def test_top_up_is_contribution_not_return(rt, legacy, market):
    # owner confirms the record is USD and approves a same-currency top-up to 20,000 (test scenario)
    plan = capital.plan_top_up(rt.ledger_writer(), rt.portfolio_id, Decimal("20000"), "USD",
                               datetime(2026, 11, 6, 17, tzinfo=ZoneInfo("America/New_York")))
    assert plan.status == "PENDING_APPROVAL" and plan.proposed_events[0].amount == Decimal("19500")
    capital.apply_plan(rt.ledger_writer(), plan, "owner:edward")
    capital.apply_plan(rt.ledger_writer(), plan, "owner:edward")              # idempotent replay
    pa = PortfolioAnalyst(rt)
    rep = pa.generate_performance_report({"US:BAC": market("US:BAC")}, base_currency="USD")
    assert rep.contributed_capital["USD"] == "20000"
    # equity reconciles: cash + 4 BAC at the last validated close (50.55 on Nov 9)
    assert Decimal(rep.equity) == Decimal("19796.40") + Decimal("4") * Decimal("50.55")
    # hand-computed TWR (inflow excluded): Nov 5: (296.40+4*49.76)/500, Nov 6: (19796.40+4*49.42-19500)/(296.40+4*49.76),
    # Nov 9: (19796.40+4*50.55)/(19796.40+4*49.42)
    e5, e6, e9 = 296.40 + 4 * 49.76, 19796.40 + 4 * 49.42, 19796.40 + 4 * 50.55
    expected = ((e5 / 500) * ((e6 - 19500) / e5) * (e9 / e6) - 1) * 100
    assert rep.returns["time_weighted_return_pct"] == pytest.approx(expected, abs=1e-3)
    assert rep.returns["simple_gain_on_contributed_pct"] == pytest.approx((e9 / 20000 - 1) * 100, abs=1e-3)


def test_fees_reduce_equity(rt, market):
    led = rt.ledger_writer()
    t = datetime(2026, 11, 3, 16, tzinfo=ZoneInfo("America/New_York"))
    led.append(LedgerEvent(event_type=LedgerEventType.CONTRIBUTION, portfolio_id="p", currency="USD", amount=Decimal("1000"), event_at=t, idempotency_key="c"))
    led.append(LedgerEvent(event_type=LedgerEventType.BUY, portfolio_id="p", instrument_id="US:BAC", quantity=Decimal(10),
                           currency="USD", amount=Decimal("-511.00"), price=Decimal("49.60"), event_at=t, idempotency_key="b"))
    rep = PortfolioAnalyst(rt).generate_performance_report({"US:BAC": market("US:BAC")})
    assert Decimal(rep.equity) == Decimal("489.00") + Decimal("10") * Decimal("50.55")
    assert Decimal(rep.unrealised_pnl["USD"]) == Decimal("10") * Decimal("50.55") - Decimal("511.00")


def test_missing_prices_flagged_not_invented(rt, legacy):
    rep = PortfolioAnalyst(rt).generate_performance_report({})
    assert rep.equity is None and any("no validated price" in r for r in rep.reconciliation_exceptions)
    assert rep.holdings[0]["market_value"] is None


def test_reports_reproducible_and_benchmark_aligned(rt, legacy, market):
    pa = PortfolioAnalyst(rt)
    obs = {"US:BAC": market("US:BAC")}
    a = pa.generate_performance_report(obs, benchmark=market("US:SPY"))
    b = pa.generate_performance_report(obs, benchmark=market("US:SPY"))
    keep = lambda r: r.model_dump(exclude={"report_id", "created_at"})
    assert keep(a) == keep(b)
    assert a.benchmark and a.benchmark["currency"] == "USD"
    assert "max_drawdown_pct" in a.risk and a.ledger_head
    assert any("not meaningful" in w for w in a.warnings)           # few returns -> no Sharpe claims


def test_analyst_is_read_only(rt):
    with pytest.raises(PermissionDenied):
        require("portfolio_analyst", "append_ledger")
    pa = PortfolioAnalyst(rt)
    assert not hasattr(pa.reader, "append")


# ---- owner decision 2026-11-10: original capital S$500, mandate raised to S$20,000 ----
def _fx(rt):
    from marcus_v2.marcus.team import build_team
    # FX fixture was fetched ~08:20 UTC on 2026-11-10; the clock must be after that or Data Guardian
    # (correctly) rejects the source timestamps as being in the future
    later = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    team = build_team(rt, providers={"fixture": FixtureProvider(FIXTURES / "bars.json")}, clock=lambda: later)
    fx = team.fx_rates(date(2026, 10, 1), date(2026, 11, 9))
    assert date(2026, 10, 7) not in fx["rates"] and date(2026, 10, 20) not in fx["rates"]   # inconsistent Yahoo bars excluded
    return fx


def test_sgd_legacy_import_uses_dated_validated_fx(rt):
    fx = _fx(rt)
    assert fx["rates"][date(2026, 11, 5)] == Decimal("1.27873")
    events, notes = capital.plan_legacy_import(FIXTURES / "legacy_trader", rt.portfolio_id, capital_currency="SGD",
                                               fx_rates=fx["rates"], fx_source="yahoo validated")
    capital.apply_events(rt.ledger_writer(), events, "owner:edward")
    s = balances(rt.ledger_reader().events())
    assert s["contributed"] == {"SGD": Decimal("500")}                       # FX legs are not contributions
    assert s["cash"]["SGD"] == Decimal("500") - Decimal("260.35")           # 203.60 USD x 1.27873
    assert s["cash"]["USD"] == Decimal("0") and s["positions"]["US:BAC"]["quantity"] == 4
    assert any("owner decision" in n for n in notes)


def test_missing_fx_rate_refuses_import(rt):
    with pytest.raises(capital.CapitalError):
        capital.plan_legacy_import(FIXTURES / "legacy_trader", rt.portfolio_id, capital_currency="SGD", fx_rates={})


def test_sgd_top_up_to_20000_and_sgd_reporting(rt, market):
    fx = _fx(rt)
    events, _ = capital.plan_legacy_import(FIXTURES / "legacy_trader", rt.portfolio_id, capital_currency="SGD", fx_rates=fx["rates"])
    capital.apply_events(rt.ledger_writer(), events, "owner:edward")
    plan = capital.plan_top_up(rt.ledger_writer(), rt.portfolio_id, Decimal("20000"), "SGD",
                               datetime(2026, 11, 10, 9, tzinfo=ZoneInfo("Asia/Singapore")),
                               owner_stated_original=(Decimal("500"), "SGD"))
    assert plan.status == "PENDING_APPROVAL" and plan.proposed_events[0].amount == Decimal("19500")
    capital.apply_plan(rt.ledger_writer(), plan, "owner:edward")
    assert balances(rt.ledger_reader().events())["contributed"] == {"SGD": Decimal("20000")}
    rep = PortfolioAnalyst(rt).generate_performance_report({"US:BAC": market("US:BAC")}, base_currency="SGD",
                                                           fx={"USD": fx["series"]})
    rate = Decimal(str(fx["series"].iloc[-1]))                              # latest validated USD->SGD (Nov 9)
    expected = Decimal("239.65") + Decimal("19500") + (Decimal("4") * Decimal("50.55") * rate).quantize(Decimal("0.01"))
    assert Decimal(rep.equity) == expected and rep.contributed_capital == {"SGD": "20000"}
    assert any("converted to SGD" in w for w in rep.warnings)
