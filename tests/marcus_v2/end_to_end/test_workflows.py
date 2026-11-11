"""End-to-end workflows A-D with the full team on real (fixture) market data.
TradingAgents and Quant are MOCKED here (their real systems are not reachable)."""
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import json

import pytest

from marcus_v2.core.contracts import (Action, ApprovalStatus, Claim, Evidence, OrderType, PaperOrder, QualityStatus,
                                      ResearchReport, RetrievalStatus)
from marcus_v2.core.mandate import default_v2_mandate
from marcus_v2.core import capital
from marcus_v2.integrations.existing import QuantAdapter, TradingAgentsAdapter
from marcus_v2.integrations.openbb_adapter import FixtureProvider
from marcus_v2.marcus.orchestrator import UnvalidatedData
from marcus_v2.marcus.team import build_team

FIX = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 11, 10, 7, 30, tzinfo=timezone.utc)


class MockQuant:
    def __init__(self, verdict="SUPPORTED", findings=()):
        self.verdict, self.findings, self.requests = verdict, list(findings), []

    def review(self, request):
        self.requests.append(request)
        return {"verdict": self.verdict, "findings": self.findings, "reviewer": "quant (MOCKED)"}


class MockTA(TradingAgentsAdapter):
    def __init__(self, report=None):
        super().__init__("/tmp")
        self.report = report

    def research(self, instrument_id, symbol, as_of, timeout_s=0):
        if self.report is None:
            return ResearchReport(instrument_id=instrument_id, as_of=as_of, thesis="", status=RetrievalStatus.FAILED,
                                  failed_feeds=["tradingagents: no LLM API key configured (MOCKED outage)"])
        return self.report.model_copy(update={"instrument_id": instrument_id})


def ta_report(rating="Buy", action=Action.BUY, thesis="PFE is in an uptrend above its 200-day average after a calm pullback."):
    ev = [Evidence(kind=k, source="tradingagents (MOCKED)", reference=k) for k in ("market", "news", "fundamentals", "sentiment")]
    return ResearchReport(instrument_id="US:PFE", as_of=date(2026, 11, 9), rating=rating, thesis=thesis,
                          claims=[Claim(text="above its 200-day average", evidence_refs=[ev[0].evidence_id],
                                        assertion={"metric": "close_vs_ma200", "op": ">", "value": 0})],
                          evidence=ev, proposed_action=action)


@pytest.fixture
def team_factory(rt):
    rt.mandates.propose(default_v2_mandate(1).model_copy(update={"effective_from": date(2026, 11, 1)}))
    rt.mandates.approve("marcus-paper", 1, "owner:edward")

    def make(ta=None, quant=None):
        q = quant or MockQuant()
        return build_team(rt, providers={"fixture": FixtureProvider(FIX / "bars.json")}, clock=lambda: NOW,
                          quant=QuantAdapter(q), tradingagents=MockTA(ta)), q
    return make


def test_workflow_b_full_pipeline_to_paper_fill(rt, team_factory):
    marcus, quant = team_factory(ta_report())
    plan = {"limit": "29.40", "stop": "28.34", "target": "31.44"}
    rec = marcus.workflow_b_new_opportunity("US:PFE", date(2026, 11, 9), plan=plan, portfolio_equity=Decimal("20000"),
                                            strategy={"strategy_id": "pullback", "entry_rule": {"type": "pullback_in_uptrend", "window": 20},
                                                      "exit_rule": {"type": "close_below_ma", "window": 50}, "lookback_days": 700})
    assert rec.data_quality_refs and rec.research_refs and rec.challenge_refs and rec.backtest_refs and rec.quant_refs
    assert quant.requests and "conclusion" not in json.dumps(quant.requests[0])          # Quant sees evidence, not Marcus's verdict
    assert rec.action == Action.BUY and rec.owner_approval == ApprovalStatus.PENDING
    # sizing: 2% of 20,000 = 400 max loss; (29.40-28.34)=1.06/sh -> 377 shares, capped by 95% position = 646
    assert rec.proposed_quantity == Decimal(377) and Decimal(rec.risk_assessment["loss_at_stop"]) <= Decimal("400")
    # cannot execute before owner approval
    capital.apply_events(rt.ledger_writer(), [capital.LedgerEvent(event_type=capital.LedgerEventType.CONTRIBUTION,
                         portfolio_id="marcus-paper", currency="USD", amount=Decimal("20000"),
                         event_at=datetime(2026, 11, 2, tzinfo=timezone.utc), idempotency_key="e2e-fund")], "owner:edward")
    mkt = marcus.mi.get_historical_prices("US:PFE", date(2026, 11, 1), date(2026, 11, 10))
    marcus.dg.validate_report(mkt, purpose="execution")
    order = PaperOrder(order_id="e2e-pfe", portfolio_id="marcus-paper", instrument_id="US:PFE", side="buy",
                       quantity=rec.proposed_quantity, order_type=OrderType.LIMIT, limit_price=Decimal("29.40"),
                       planned_entry=Decimal("29.40"), recommendation_id=rec.recommendation_id, mandate_id="marcus-paper",
                       mandate_version=1, decision_at=datetime(2026, 11, 6, 21, 30, tzinfo=timezone.utc), currency="USD",
                       expires_on=date(2026, 11, 10))
    pending = marcus.execute_approved(rec.recommendation_id, order, mkt, Decimal("20000"))
    assert pending["fill"]["status"] == "REJECTED" and "approved" in pending["fill"]["reason"]
    with pytest.raises(PermissionError):
        marcus.approve_recommendation(rec.recommendation_id, "marcus")
    marcus.approve_recommendation(rec.recommendation_id, "owner:edward")
    order2 = order.model_copy(update={"order_id": "e2e-pfe-2"})
    done = marcus.execute_approved(rec.recommendation_id, order2, mkt, Decimal("20000"))
    # PFE Nov 9: open 29.59, low 28.98 -> limit 29.40 fills at 29.40
    assert done["fill"]["status"] == "FILLED" and done["fill"]["price"] == "29.40"
    review = marcus.workflow_c_portfolio_review({"US:PFE": mkt.observations})
    assert review["equity"] is not None and not [e for e in review["exceptions"] if "hash" in e]
    tel = rt.audit.telemetry()
    assert {"market_intelligence", "data_guardian", "research_challenger", "strategy_lab",
            "execution_specialist", "portfolio_analyst"} <= set(tel)
    assert all(v["invocations"] >= 1 for v in tel.values())


def test_workflow_b_blocks_when_quant_challenges_and_lists_disagreement(team_factory):
    marcus, _ = team_factory(ta_report(), MockQuant("CHALLENGED", ["sample of 4 trades is not significant"]))
    rec = marcus.workflow_b_new_opportunity("US:PFE", date(2026, 11, 9), plan={"limit": "29.40", "stop": "28.34", "target": "31.44"})
    assert rec.action == Action.WAIT and any("Quant" in d for d in rec.disagreements)
    assert any("TradingAgents BUY" in d for d in rec.disagreements)


def test_workflow_b_contradicted_research_not_bought(team_factory):
    marcus, _ = team_factory(ta_report(thesis="BAC is in a strong uptrend above its 200-day average."))
    rec = marcus.workflow_b_new_opportunity("US:BAC", date(2026, 11, 9), plan={"limit": "50.90", "stop": "48.50", "target": "54.50"})
    assert rec.action == Action.WAIT and any("Research Challenger" in d for d in rec.disagreements)


def test_workflow_d_research_outage_blocks_entries(team_factory):
    marcus, _ = team_factory(None)
    rec = marcus.workflow_b_new_opportunity("US:PFE", date(2026, 11, 9), plan={"limit": "29.40", "stop": "28.34"})
    assert rec.decision_status == "blocked" and rec.action == Action.WAIT
    assert any("primary research unavailable" in x for x in rec.evidence_limitations)
    inc = list((marcus.rt.home / "incidents").glob("*.json"))
    assert inc and json.loads(inc[0].read_text())["for"] == "dex"


def test_workflow_a_dedupes_and_validates(team_factory):
    marcus, _ = team_factory(ta_report())
    ev = {"event_id": "pm-1", "instrument_id": "US:BAC", "type": "trigger_hit", "level": "50.71",
          "occurred_at": "2026-11-09T21:10:00+00:00"}
    line = {"line": "ALERT BAC last $50.55: at or above your alert level $50.71", "occurred_at": "2026-11-09T21:10:00+00:00"}
    out = marcus.workflow_a_daily_monitoring([ev, ev, line, {"bad": 1}], {"US:BAC"})
    assert out["new"] == 2 and out["duplicates_ignored"] == ["pm-1"] and len(out["rejected"]) == 1
    assert all(m["data_quality"] in ("VALID", "WARNING") for m in out["material"])
    again = marcus.workflow_a_daily_monitoring([ev], {"US:BAC"})
    assert again["new"] == 0 and again["duplicates_ignored"] == ["pm-1"]        # duplicates never create new decisions


def test_unvalidated_market_data_cannot_reach_execution(team_factory):
    marcus, _ = team_factory(ta_report())
    raw = marcus.mi.get_historical_prices("US:PFE", date(2026, 11, 1), date(2026, 11, 10))
    with pytest.raises(UnvalidatedData):
        marcus.execute_approved("rec_x", None, raw, Decimal("1"))
