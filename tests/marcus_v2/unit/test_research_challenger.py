from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from marcus_v2.agents.data_guardian.agent import DataGuardian
from marcus_v2.agents.market_intelligence.agent import MarketIntelligence
from marcus_v2.agents.research_challenger.agent import ResearchChallenger
from marcus_v2.core.contracts import Action, Claim, Evidence, ResearchReport
from marcus_v2.core.llm import MockLLM, NullLLM
from marcus_v2.integrations.openbb_adapter import FixtureProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 11, 10, 7, 30, tzinfo=timezone.utc)


@pytest.fixture
def ctx_report(rt):
    mi = MarketIntelligence(rt, providers={"fixture": FixtureProvider(FIXTURES / "bars.json")}, clock=lambda: NOW)
    dg = DataGuardian(rt, clock=lambda: NOW)
    r = mi.get_market_context("US:BAC")
    dg.validate_report(r, purpose="research")
    return r


def bac_thesis(**kw):
    ev = [Evidence(kind="market", source="tradingagents", reference="market report"),
          Evidence(kind="news", source="tradingagents", reference="news report")]
    base = dict(instrument_id="US:BAC", as_of=date(2026, 11, 9), rating="Buy",
                thesis="BAC is in a strong uptrend and trades above its 200-day average; buy the breakout.",
                claims=[Claim(text="BAC trades above its 200-day average", evidence_refs=[ev[0].evidence_id],
                              assertion={"metric": "close_vs_ma200", "op": ">", "value": 0}),
                        Claim(text="Net interest income will surge next year"),                       # no evidence
                        Claim(text="Analysts upgraded it", evidence_refs=["ev_does_not_exist"])],
                evidence=ev, failed_feeds=["sentiment"], proposed_action=Action.BUY)
    base.update(kw)
    return ResearchReport(**base)


def test_detects_unsupported_and_contradicted_claims(rt, ctx_report):
    rc = ResearchChallenger(rt).register()
    rep = rc.review_investment_thesis(bac_thesis(), ctx_report, use_llm=False)
    assert ctx_report.context["last_close"] < ctx_report.context["ma200"]          # BAC really is below MA200
    assert any("no evidence" in u for u in rep.unsupported_claims)
    assert any("does not exist" in u for u in rep.unsupported_claims)
    assert any(i.check == "contradiction" for i in rep.contradictory_evidence)
    assert any("uptrend" in i.message for i in rep.contradictory_evidence)
    assert "research feed failed: sentiment" in rep.missing_information
    assert any("fundamental" in m for m in rep.missing_information)
    assert rep.downside_scenarios and rep.confidence == "low" and rep.quant_review_recommended
    assert any("200-day" in c for c in rep.counterarguments)


def test_supported_thesis_gets_no_manufactured_dissent(rt, ctx_report):
    c = ctx_report.context
    ev = [Evidence(kind=k, source="ta", reference=k) for k in ("market", "news", "fundamentals", "sentiment")]
    r = ResearchReport(instrument_id="US:BAC", as_of=date(2026, 11, 9), rating="Hold",
                       thesis="BAC sits below its 200-day average; wait for a reclaim.",
                       claims=[Claim(text="below its 200-day average", evidence_refs=[ev[0].evidence_id],
                                     assertion={"metric": "close_vs_ma200", "op": "<", "value": 0})],
                       evidence=ev, proposed_action=Action.WAIT)
    rep = ResearchChallenger(rt).review_investment_thesis(r, ctx_report, use_llm=False)
    assert rep.contradictory_evidence == [] and rep.unsupported_claims == []


def test_paraphrase_llm_flagged_as_not_independent(rt, ctx_report):
    thesis = bac_thesis()
    rt.llm = MockLLM(reply=thesis.thesis)                     # an "LLM" that just repeats the thesis
    rep = ResearchChallenger(rt).review_investment_thesis(thesis, ctx_report)
    assert rep.independence_ok is False and not any("[MOCKED LLM]" in c for c in rep.counterarguments)


def test_llm_counterarguments_labelled_and_cached(rt, ctx_report):
    llm = MockLLM(reply="- Credit losses could rise if unemployment climbs\n- Rate cuts compress net interest margin")
    rt.llm = llm
    rc = ResearchChallenger(rt)
    a = rc.review_investment_thesis(bac_thesis(), ctx_report)
    b = rc.review_investment_thesis(bac_thesis(), ctx_report)
    assert any(c.startswith("[MOCKED LLM]") for c in a.counterarguments) and a.llm_used
    assert llm.calls == 1 and a.report_id == b.report_id      # equivalent request served from cache


def test_no_llm_falls_back_and_says_so(rt, ctx_report):
    rt.llm = NullLLM()
    rep = ResearchChallenger(rt).review_investment_thesis(bac_thesis(), ctx_report)
    assert not rep.llm_used and any("no LLM" in l for l in rep.limitations)


def test_without_market_data_it_does_not_invent(rt):
    rep = ResearchChallenger(rt).review_investment_thesis(bac_thesis(), None, use_llm=False)
    assert not [i for i in rep.contradictory_evidence if i.check == "contradiction"] and rep.downside_scenarios == []
    assert all(i.check == "assertion_unverifiable" for i in rep.contradictory_evidence)
    assert any("no independent market data" in m for m in rep.missing_information)
