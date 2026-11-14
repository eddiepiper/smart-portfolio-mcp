"""The 17 required tests (spec §18), on synthetic fixtures with scripted LLM runtimes.

Scripted runtimes are test doubles: they make the LLM layer deterministic so the
plumbing (TradingAgents graph, contracts, ledger, risk gate) can be verified
exactly. Live-runtime checks are in test_live.py (opt-in).
"""

import json
import math
import re
import socket
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from merlin_quant.calendar import NY
from merlin_quant.contracts import Action, Mode, Stance, TradeProposal, Verdict
from merlin_quant.data import DataUnavailable, LookAheadError, synthetic_fixture
from merlin_quant.engine import ALLOWED_BROKERS, EngineError, PaperEngine
from merlin_quant.ledger import LedgerError
from merlin_quant.merlin import dcf, run_merlin, scripted_merlin
from merlin_quant.quant import (TA_COMMIT, _SyntheticVendor, challenge, run_quant, scripted_challenge,
                                scripted_ta_responder)
from merlin_quant.runtime import Runtime, RuntimeUnavailable, ScriptedRuntime
from merlin_quant import risk as risk_gate
from merlin_quant.tests.conftest import CUTOFF, NOW, cfg_with

PKG = Path(__file__).resolve().parents[1]
UNIVERSE = ["AAPL", "MSFT", "NVDA"]


def _buy_aapl(team):
    m, q, c, j, p = team.research("AAPL", CUTOFF, now=NOW)
    assert p.action == Action.BUY, p
    team.approve(p.proposal_id, "test-owner")
    return p


# 1 ---------------------------------------------------------------------------------------
def test_01_existing_environment():
    import tradingagents
    from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: F401
    assert tradingagents.__version__ == "0.7.1"
    setup = (PKG.parent / ".claude/skills/stock-analysis/setup_tradingagents.sh").read_text()
    assert TA_COMMIT in setup, "Quant must use the same pinned TradingAgents as the existing stock-analysis skill"
    assert (PKG.parent / ".claude/skills/stock-analysis/run_tradingagents.py").exists()


# 2 ---------------------------------------------------------------------------------------
def test_02_merlin_research_is_verifiable(md):
    view = md.view(CUTOFF)
    r = run_merlin(view, "AAPL", UNIVERSE, CUTOFF, ScriptedRuntime(scripted_merlin))
    # independently recompute TTM and the base DCF from the raw fixture
    fs = [f for f in md.filings["AAPL"] if f["filed"] <= CUTOFF.isoformat()][-4:]
    rev = sum(f["revenue"] for f in fs)
    fcf_m = sum(f["fcf"] for f in fs) / rev
    assert r.financial_metrics["ttm_revenue"] == pytest.approx(rev)
    assert r.financial_metrics["fcf_margin"] == pytest.approx(fcf_m, abs=1e-6)
    b = next(s for s in r.scenarios if s.name == "base")
    pv, x = 0.0, rev
    for y in range(1, 6):
        x *= 1 + b.revenue_cagr
        f_last = x * (fcf_m + (b.terminal_margin - fcf_m) * y / 5)
        pv += f_last / (1 + b.wacc) ** y
    ev = pv + f_last * (1 + b.terminal_growth) / (b.wacc - b.terminal_growth) / (1 + b.wacc) ** 5
    vps = (ev + fs[-1]["cash"] - fs[-1]["debt"]) / fs[-1]["diluted_shares"]
    assert r.fair_value_base == pytest.approx(vps, abs=0.01)
    close = [b for b in md.bars["AAPL"] if b["date"] <= CUTOFF.isoformat()][-1]
    assert r.reference_price == close["close"] and r.reference_price_date == CUTOFF
    assert r.evidence_cutoff == CUTOFF and len(r.scenarios) == 3
    assert any(s.kind == "synthetic_fixture" for s in r.sources)
    assert "SYNTHETIC" in r.unknowns[0].upper()
    assert r.run.inputs_digest and r.run.model_calls == 1


# 3 ---------------------------------------------------------------------------------------
def test_03_quant_runs_actual_tradingagents(md, monkeypatch):
    from tradingagents.graph import trading_graph as tg
    calls = []
    real = tg.TradingAgentsGraph.propagate
    monkeypatch.setattr(tg.TradingAgentsGraph, "propagate",
                        lambda self, *a, **k: calls.append(a) or real(self, *a, **k))
    view = md.view(CUTOFF)
    resp = scripted_ta_responder(view, "NVDA")
    rq, rd = ScriptedRuntime(resp), ScriptedRuntime(resp)
    q = run_quant(view, "NVDA", "SPY", rq, rd, quick_model="q", deep_model="d")
    assert calls == [("NVDA", CUTOFF.isoformat())]
    assert q.tradingagents_rating in {"Buy", "Overweight", "Hold", "Underweight", "Sell"}
    for k in ("market_report", "sentiment_report", "news_report", "investment_plan", "trader_investment_plan",
              "final_trade_decision", "bull_history", "bear_history", "risk_debate"):
        assert q.tradingagents_reports.get(k), f"TradingAgents produced no {k}"
    # analysts really called TradingAgents' tools, which were served point-in-time data
    prompts = "\n".join(p for _, p, _ in rq.transcript)
    assert "[TOOL RESULT get_verified_market_snapshot]" in prompts and "VERIFIED SNAPSHOT NVDA" in prompts
    assert q.run.model_calls >= 10 and q.stance == Stance.BEARISH


# 4 ---------------------------------------------------------------------------------------
def test_04_independence(team):
    m, q = team.stage_a("NVDA", CUTOFF)
    quant_text = json.dumps(q.model_dump(mode="json"))
    for marker in (m.report_id, m.thesis[:40], str(m.fair_value_base)):
        assert marker not in quant_text
    assert q.saw_merlin_report is False and q.run.context_id != m.run.context_id
    # Quant's input bundle is identical whether or not a Merlin report exists
    _, q_alone = team.stage_a("NVDA", CUTOFF, only="quant")
    assert q_alone.run.inputs_digest == q.run.inputs_digest
    ev = [e for e in team.engine.ledger.events() if e["type"] == "RESEARCH" and e["payload"]["record"] == "quant"]
    assert ev and all(e["payload"]["saw_merlin_report"] is False for e in ev)


# 5 ---------------------------------------------------------------------------------------
def test_05_challenge_flags_optimistic_merlin(md):
    view = md.view(CUTOFF)
    hype = {"base": {"revenue_cagr": 0.45, "terminal_margin": 0.75, "terminal_growth": 0.05, "wacc": 0.075}}
    m = run_merlin(view, "MSFT", UNIVERSE, CUTOFF, ScriptedRuntime(scripted_merlin), overrides=hype)
    assert m.stance == Stance.BULLISH
    resp = scripted_ta_responder(view, "MSFT")
    q = run_quant(view, "MSFT", "SPY", ScriptedRuntime(resp), ScriptedRuntime(resp), quick_model="q", deep_model="d")
    c = challenge(m, q, ScriptedRuntime(scripted_challenge))
    topics = {(x.topic, x.severity) for x in c.challenges}
    assert ("growth_expectations", "high") in topics
    assert ("financial_assumptions", "high") in topics  # terminal growth and margin above evidence
    assert len(c.unsupported_assumptions) >= 2
    assert c.verdict == Verdict.DISAGREE
    assert all(x.supported_by for x in c.challenges)


# 6 ---------------------------------------------------------------------------------------
def test_06_disagreement_preserved(team):
    m, q, c, j, p = team.research("NVDA", CUTOFF, now=NOW)
    assert (m.stance, q.stance, c.verdict) == (Stance.BULLISH, Stance.BEARISH, Verdict.DISAGREE)
    assert j.merlin_thesis == m.thesis and j.merlin_stance == m.stance and j.quant_stance == q.stance
    assert any("Merlin is BULLISH" in d and "Quant is BEARISH" in d for d in j.disagreements)
    assert j.disagreement_preserved and p.action in (Action.NO_ACTION, Action.HOLD)
    report = next((team.engine.home / "reports").glob("*NVDA*.md")).read_text()
    assert "Disagreement (preserved)" in report and "DISAGREE" in report


# 7 ---------------------------------------------------------------------------------------
def test_07_decision_schema_rejects_malformed(team):
    _, _, _, _, p = team.research("AAPL", CUTOFF, now=NOW)
    good = p.model_dump(mode="json")
    TradeProposal.model_validate(good)
    bad = [
        {k: v for k, v in good.items() if k != "merlin_report_id"},           # missing reference
        {**good, "action": "SHORT"},                                         # unsupported action
        {**good, "quantity": 0},                                             # BUY with no size
        {**good, "action": "HOLD"},                                          # HOLD with a size
        {**good, "quantity": 12.5},                                          # fractional size
        {**good, "reference_price_date": "2026-11-09"},                      # price after cutoff
        {**good, "order_type": "market"},                                    # free-form extra field
        {**good, "thesis": ""},                                              # empty thesis
        {**good, "risk_considerations": []},                                 # no risk considerations
        {**good, "reference_price": -1},                                     # invalid price
    ]
    for b in bad:
        with pytest.raises(ValidationError):
            TradeProposal.model_validate(b)


# 8 ---------------------------------------------------------------------------------------
def test_08_oversized_position_rejected(team):
    p = _buy_aapl(team)
    big = p.model_copy(update={"quantity": 200, "proposal_id": "prop-oversized"})
    team.engine.records.put("proposals", big.proposal_id, big)
    rec, r = team.execute(big.proposal_id, now=NOW)
    assert rec is None and r.outcome == "REJECT"
    failed = {c.name for c in r.checks if not c.passed}
    assert {"max_position_exposure", "order_size_valid"} <= failed
    assert not any(e["type"] == "FILL" for e in team.engine.ledger.events())
    assert team.proposal(big.proposal_id).status == "REJECTED"


# 9 ---------------------------------------------------------------------------------------
def test_09_paper_execution_updates_cash_and_holdings(team, md):
    p = _buy_aapl(team)
    rec, r = team.execute(p.proposal_id, now=NOW)
    assert r.outcome == "PASS" and rec.simulated and rec.fill_session == date(2026, 11, 9)
    open_px = next(b["open"] for b in md.bars["AAPL"] if b["date"] == "2026-11-09")
    assert rec.fill_price > open_px  # a buy pays half-spread + impact above the open
    st = team.engine.ledger.state()
    assert st.holdings["AAPL"].quantity == p.quantity
    assert st.cash == pytest.approx(100_000 - p.quantity * rec.fill_price - rec.commission)
    assert team.proposal(p.proposal_id).status == "EXECUTED"


# 10 --------------------------------------------------------------------------------------
def test_10_transaction_accounting_reconciles(team, md):
    p = _buy_aapl(team)
    buy, _ = team.execute(p.proposal_id, now=NOW)
    sell_p = p.model_copy(update={"proposal_id": "prop-trim", "action": Action.REDUCE, "quantity": 10,
                                  "status": "APPROVED"})
    team.engine.records.put("proposals", sell_p.proposal_id, sell_p)
    sell, r = team.execute(sell_p.proposal_id, now=NOW)
    assert r.outcome == "PASS"
    st = team.engine.ledger.state()
    fills = [e["payload"] for e in team.engine.ledger.events() if e["type"] == "FILL"]
    assert [f["fill_price"] for f in fills] == pytest.approx([buy.fill_price, sell.fill_price], abs=1e-6)
    buy, sell = (type("F", (), {"quantity": f["quantity"], "fill_price": f["fill_price"], "commission": f["commission"]})
                 for f in fills)
    cash = 100_000 - buy.quantity * buy.fill_price - buy.commission + sell.quantity * sell.fill_price - sell.commission
    assert st.cash == pytest.approx(cash)
    assert st.fees == pytest.approx(buy.commission + sell.commission)
    basis_per = (buy.quantity * buy.fill_price + buy.commission) / buy.quantity
    assert st.realized_pnl == pytest.approx(sell.quantity * sell.fill_price - sell.commission - 10 * basis_per)
    assert st.holdings["AAPL"].quantity == buy.quantity - 10
    snap = team.engine.snapshot(date(2026, 11, 13))
    mark = md.raw_close("AAPL", date(2026, 11, 13))[0]
    assert snap.nav == pytest.approx(cash + (buy.quantity - 10) * mark, abs=0.01)
    team.engine.reconcile()  # broker book == ledger


# 11 --------------------------------------------------------------------------------------
def test_11_duplicate_execution_records_one_transaction(team):
    p = _buy_aapl(team)
    a, _ = team.execute(p.proposal_id, now=NOW)
    b, _ = team.execute(p.proposal_id, now=NOW)
    assert a.execution_id == b.execution_id
    fills = [e for e in team.engine.ledger.events() if e["type"] == "FILL"]
    assert len(fills) == 1
    st = team.engine.ledger.state()
    r = risk_gate.evaluate(team.proposal(p.proposal_id), st, team.cfg, team.md,
                           last_completed_session=CUTOFF, executed_proposals=st.filled, orders_this_session=1)
    assert not next(c for c in r.checks if c.name == "duplicate_order").passed
    # the ledger itself refuses a second, different event under the same idempotency key
    with pytest.raises(LedgerError):
        team.engine.ledger.append("FILL", fills[0]["idem_key"], {"different": True})


# 12 --------------------------------------------------------------------------------------
def test_12_restart_recovery(team, cfg, md):
    p = _buy_aapl(team)
    team.execute(p.proposal_id, now=NOW)
    before = team.engine.snapshot(date(2026, 11, 13))
    reopened = PaperEngine(team.engine.home, cfg, md).open()
    assert reopened.snapshot(date(2026, 11, 13)) == before
    with pytest.raises(EngineError):
        PaperEngine(team.engine.home, cfg, md).init()  # never silently reset
    # tampering with a ledger line is detected
    path = team.engine.ledger.path
    lines = path.read_text().splitlines()
    lines[0] = lines[0].replace("100000.0", "999999.0")
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(LedgerError):
        PaperEngine(team.engine.home, cfg, md).open()


def test_12b_crash_between_broker_fill_and_ledger_is_recovered(tmp_path, cfg, md):
    from merlin_quant.workflow import Team
    t = Team(tmp_path / "h", cfg, md, runtime_override="scripted")
    t.engine.init(); t.engine.open()
    p = _buy_aapl(t)
    with pytest.raises(EngineError, match="SIMULATED CRASH"):
        t.execute(p.proposal_id, now=NOW, crash_after_intent=True)
    with pytest.raises(EngineError, match="RECONCILIATION FAILED"):
        t.engine.reconcile()  # ledger has the intent, broker has the fill
    t2 = Team(tmp_path / "h", cfg, md, runtime_override="scripted")
    t2.engine.open()
    assert any("recovered interrupted fill" in n for n in t2.engine.recovery_notes)
    assert t2.engine.ledger.state().holdings["AAPL"].quantity == p.quantity
    rec, _ = t2.execute(p.proposal_id, now=NOW)  # rerun: no second fill
    assert rec.quantity == p.quantity and t2.proposal(p.proposal_id).status == "EXECUTED"
    assert sum(e["type"] == "FILL" for e in t2.engine.ledger.events()) == 1


# 13 --------------------------------------------------------------------------------------
def test_13_no_future_information(md):
    view = md.view(CUTOFF)
    assert max(b["date"] for b in view.bars("NVDA")) == CUTOFF.isoformat()
    assert all(f["filed"] <= CUTOFF.isoformat() for f in view.filings("AAPL"))
    assert len(view.filings("AAPL")) < len(md.filings["AAPL"]) or md.filings["AAPL"][-1]["filed"] <= CUTOFF.isoformat()
    assert not any("FUTURE ITEM" in n["headline"] for n in view.news("NVDA", days=60))
    assert any("FUTURE ITEM" in n["headline"] for n in md.news["NVDA"])  # it exists, but is not visible
    # TradingAgents' tool layer cannot reach past the cutoff even if the model asks
    out = _SyntheticVendor(view).route("get_stock_data", "NVDA", "2026-11-01", "2026-12-31")
    assert "2026-11-09" not in out and "2026-11-06" in out
    with pytest.raises(LookAheadError):
        md.execution_bar("AAPL", CUTOFF, decision_cutoff=CUTOFF)
    # a filing is invisible before its filing date, even after its period end
    q3 = next(f for f in md.filings["AAPL"] if f["period_end"] == "2026-09-30")
    assert q3 not in md.view(date(2026, 10, 30)).filings("AAPL") and q3 in md.view(date(2026, 11, 6)).filings("AAPL")
    # split adjustment is point-in-time: before the ex-date, no adjustment is applied
    pre = md.view(date(2026, 6, 9)).bars("NVDA")[-1]["close"]
    raw_pre = next(b for b in md.bars["NVDA"] if b["date"] == "2026-06-09")["close"]
    assert pre == raw_pre
    post_view_pre_bar = next(b for b in md.view(CUTOFF).bars("NVDA") if b["date"] == "2026-06-09")["close"]
    assert post_view_pre_bar == pytest.approx(raw_pre / 4)


# 14 --------------------------------------------------------------------------------------
def test_14a_stale_data_blocks(team):
    p = _buy_aapl(team)
    later = datetime(2026, 11, 12, 21, 0, tzinfo=NY)  # three sessions later
    rec, r = team.execute(p.proposal_id, now=later)
    assert rec is None and r.outcome == "BLOCKED"
    assert any(x.startswith("data_freshness") for x in r.reasons)


def test_14b_unavailable_data_api(team, cfg, tmp_path):
    p = _buy_aapl(team)
    team.md.unavailable = True
    rec, r = team.execute(p.proposal_id, now=NOW)
    assert rec is None and r.outcome == "BLOCKED" and any(x.startswith("data_available") for x in r.reasons)
    with pytest.raises(DataUnavailable):
        team.stage_a("AAPL", CUTOFF)


def test_14c_unavailable_llm_runtime(md):
    class Down(Runtime):
        name = "down"

        def _complete(self, system, prompt, model):
            raise RuntimeUnavailable("simulated provider outage")
    q = run_quant(md.view(CUTOFF), "AAPL", "SPY", Down("x"), Down("y"), quick_model="x", deep_model="y")
    assert q.stance == Stance.INSUFFICIENT_EVIDENCE and q.tradingagents_rating is None
    assert any("TradingAgents run failed" in n for n in q.run.notes) and q.run.failed_calls >= 1


def test_14d_interrupted_before_fill_is_abandoned(team, cfg, md):
    p = _buy_aapl(team)
    st = team.engine.ledger.state()
    team.engine.ledger.append("ORDER_INTENT", "intent:exe-x", {"execution_id": "exe-x", "proposal_id": p.proposal_id,
                              "instrument": "AAPL", "side": "buy", "quantity": 5, "fill_session": "2026-11-09",
                              "open_price": 228.46})
    e2 = PaperEngine(team.engine.home, cfg, md).open()
    assert any("abandoned" in n for n in e2.recovery_notes) and not e2.ledger.state().open_intents


def test_14e_halt_blocks_and_resume_needs_a_name(team):
    p = _buy_aapl(team)
    team.engine.halt("test halt")
    rec, r = team.execute(p.proposal_id, now=NOW)
    assert rec is None and r.outcome == "BLOCKED" and any("engine_not_halted" in x for x in r.reasons)
    with pytest.raises(EngineError):
        team.engine.resume("  ")
    team.engine.resume("test-owner")
    rec, r = team.execute(p.proposal_id, now=NOW)
    assert r.outcome == "PASS"


# 15 --------------------------------------------------------------------------------------
def test_15_backtest_verified_independently(cfg, md):
    from merlin_quant.backtest import run_backtest
    res = run_backtest(md, cfg, date(2026, 5, 1), date(2026, 11, 13), every=10)
    assert res["kind"] == "HISTORICAL_BACKTEST" and res["trades"]
    # rebuild the book from the trade list and raw closes, without the engine
    cash, shares, navs = cfg.portfolio.initial_capital, {}, {}
    by_session = {}
    for t in res["trades"]:
        by_session.setdefault(t["session"], []).append(t)
    split_ex = {a["ex_date"]: (s, a["ratio"]) for s, acts in md.actions.items() for a in acts if a["type"] == "split"}
    for d, nav in res["nav"]:
        for t in by_session.get(d, []):
            sign = 1 if t["side"] == "buy" else -1
            cash -= sign * t["quantity"] * t["fill_price"] + t["commission"]
            shares[t["instrument"]] = shares.get(t["instrument"], 0) + sign * t["quantity"]
        if d in split_ex and shares.get(split_ex[d][0]):
            s, ratio = split_ex[d]
            shares[s] = int(round(shares[s] * ratio))
        mv = sum(q * md.raw_close(s, date.fromisoformat(d))[0] for s, q in shares.items() if q)
        assert nav == pytest.approx(cash + mv, abs=0.02), d
        navs[d] = cash + mv
    vals = list(navs.values())
    assert res["metrics"]["total_return"] == pytest.approx(vals[-1] / vals[0] - 1, abs=1e-6)
    peak, mdd = vals[0], 0
    for v in vals:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    assert res["metrics"]["max_drawdown"] == pytest.approx(mdd, abs=1e-6)
    # every fill is strictly after the decision that produced it
    for t in res["trades"]:
        assert any(d["date"] < t["session"] for d in res["decisions"] if d["instrument"] == t["instrument"])
    assert res["limitations"]


# 16 --------------------------------------------------------------------------------------
def test_16_full_end_to_end(team, md):
    from merlin_quant.performance import score_research, series_metrics
    m, q, c, j, p = team.research("AAPL", CUTOFF, now=NOW)        # Merlin, Quant, challenge, joint, proposal
    assert (c.verdict, p.action) == (Verdict.AGREE, Action.BUY)
    team.approve(p.proposal_id, "test-owner")                     # PAPER_MANUAL: explicit approval
    rec, r = team.execute(p.proposal_id, now=NOW)                 # risk gate + simulated execution
    assert r.outcome == "PASS" and rec.quantity == p.quantity
    for d in (date(2026, 11, 9), date(2026, 11, 13)):
        team.engine.apply_corporate_actions(d)
        team.engine.record_nav(d)                                 # performance record
    snap = team.engine.snapshot(date(2026, 11, 13))
    assert snap.positions[0].instrument == "AAPL" and snap.nav > 0
    st = team.engine.ledger.state()
    perf = series_metrics([n["nav"] for n in st.nav_history], [n["benchmark_close"] for n in st.nav_history])
    assert "excess_return" in perf
    # audit trail: execution -> risk -> proposal -> joint -> each lead's report
    store = team.engine.records
    exe = store.get("executions", rec.execution_id)
    prop = store.get("proposals", exe["proposal_id"])
    assert store.get("risk", exe["risk_result_id"])["proposal_id"] == prop["proposal_id"]
    joint = store.get("joint", prop["assessment_id"])
    assert store.get("merlin", joint["merlin_report_id"])["report_id"] == m.report_id
    assert store.get("quant", joint["quant_report_id"])["report_id"] == q.report_id
    assert store.get("challenge", prop["challenge_report_id"])["verdict"] == "AGREE"
    types = [e["type"] for e in team.engine.ledger.events()]
    for t in ("INIT", "RESEARCH", "PROPOSAL", "APPROVAL", "RISK_RESULT", "ORDER_INTENT", "FILL", "NAV"):
        assert t in types
    rows = score_research([prop], {m.report_id: store.get("merlin", m.report_id)},
                          {q.report_id: store.get("quant", q.report_id)}, md, "SPY", 5, date(2026, 11, 13))
    assert rows[0]["status"] == "SCORED"


# 17 --------------------------------------------------------------------------------------
def test_17_no_real_trading(team, monkeypatch):
    src = "\n".join(p.read_text() for p in PKG.rglob("*.py") if "tests" not in p.parts)
    for forbidden in ("alpaca", "ib_insync", "ibapi", "place_stock_order", "tradier", "robin_stocks", "ccxt"):
        assert not re.search(forbidden, src, re.I), forbidden
    assert all(cls.__module__.startswith("merlin_quant.vendor.aihf_brokers") for cls in ALLOWED_BROKERS)
    p = _buy_aapl(team)

    def no_network(*a, **k):
        raise AssertionError("network access attempted during paper execution")
    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    rec, r = team.execute(p.proposal_id, now=NOW)
    assert rec.simulated is True and "No real order" in rec.note
    team.engine.broker = object()  # a non-paper broker is refused
    p2 = p.model_copy(update={"proposal_id": "prop-other", "quantity": 1, "status": "APPROVED"})
    with pytest.raises(EngineError, match="non-paper broker"):
        team.engine.execute(p2, r.model_copy(update={"proposal_id": "prop-other"}))


# extra: modes ------------------------------------------------------------------------------
def test_paper_auto_disabled_by_default(team, cfg):
    assert cfg.mode == Mode.PAPER_MANUAL and cfg.config_status == "EXAMPLE"
    with pytest.raises(EngineError, match="EXAMPLE"):
        team.engine.set_mode(Mode.PAPER_AUTO, validation_passed=True)
    team.engine.set_mode(Mode.RESEARCH_ONLY)
    _, _, _, _, p = team.research("AAPL", CUTOFF, now=NOW)
    with pytest.raises(EngineError, match="RESEARCH_ONLY"):
        team.approve(p.proposal_id, "x")


def test_manual_mode_never_executes_without_approval(team):
    _, _, _, _, p = team.research("AAPL", CUTOFF, now=NOW)
    assert p.status == "PROPOSED"
    with pytest.raises(EngineError, match="not APPROVED"):
        st = team.engine.ledger.state()
        rr = risk_gate.evaluate(p, st, team.cfg, team.md, last_completed_session=CUTOFF,
                                executed_proposals=st.filled, orders_this_session=0)
        team.engine.execute(p, rr)


def test_rerun_with_different_llm_output_gets_new_report_id(md):
    view = md.view(CUTOFF)
    a = run_merlin(view, "AAPL", UNIVERSE, CUTOFF, ScriptedRuntime(scripted_merlin))
    b = run_merlin(view, "AAPL", UNIVERSE, CUTOFF, ScriptedRuntime(scripted_merlin))
    assert a.report_id == b.report_id  # same inputs, same output: same id
    alt = ScriptedRuntime(lambda s, p: scripted_merlin(s, p).replace("[scripted] Valuation", "[scripted, run 2] Valuation"))
    c = run_merlin(view, "AAPL", UNIVERSE, CUTOFF, alt)
    assert c.report_id != a.report_id  # a different narrative is a different record
