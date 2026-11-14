"""Quant: Trading Analysis & Independent Challenge Lead.

Stage A (independent research) runs the real TauricResearch/TradingAgents
LangGraph engine — Market, Social/Sentiment and News analysts, Bull/Bear
researchers and their debate, Research Manager, Trader, the three risk debaters
and the Portfolio Manager — on Quant's own LLM runtime. Quant's fundamentals
analyst is off by design: Merlin owns fundamental research. Quant keeps its own
deterministic evidence (price/volume indicators plus raw filed revenue and
margin history), sourced independently of Merlin's report, so it can challenge
Merlin's assumptions.

Data reaches TradingAgents through its own tool layer, with the vendor routed to
the run's point-in-time view (`_SyntheticVendor`), so no tool can return data
after the cutoff. The ledger's book is passed as TradingAgents' read-only
PortfolioContext; TradingAgents owns no cash or holdings here.

Stage B (challenge) takes Merlin's finished report and runs deterministic checks
against Quant's independent evidence; the verdict follows fixed rules, and the
runtime writes the reasoning.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from contextlib import ExitStack
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from merlin_quant.contracts import (Challenge, ChallengeReport, MerlinReport, QuantReport, RunMeta, Source, Stance,
                                    Verdict, stable_id)
from merlin_quant.data import DataUnavailable, PointInTimeView
from merlin_quant.merlin import dcf
from merlin_quant.runtime import Runtime, build_chat_model, extract_json

AGENT_DIR = Path(__file__).parent / "agents"
TA_COMMIT = "9fa1196fffbcfd9de5146d2dd2869df84ab99838"
RATING_TO_STANCE = {"Buy": Stance.BULLISH, "Overweight": Stance.BULLISH, "Hold": Stance.NEUTRAL,
                    "Underweight": Stance.BEARISH, "Sell": Stance.BEARISH}


# ------------------------------------------------------------------ independent evidence

def _sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def indicators(view: PointInTimeView, symbol: str, benchmark: str) -> dict:
    bars = view.bars(symbol)
    c = [b["close"] for b in bars]
    out = {"close": c[-1], "close_date": bars[-1]["date"]}
    for n in (20, 50, 200):
        out[f"sma{n}"] = _sma(c, n)
    out["sma50_slope_10d"] = (_sma(c, 50) / _sma(c[:-10], 50) - 1) if len(c) >= 60 else None
    gains = [max(c[i] - c[i - 1], 0) for i in range(len(c) - 14, len(c))]
    losses = [max(c[i - 1] - c[i], 0) for i in range(len(c) - 14, len(c))]
    al, ag = sum(losses) / 14, sum(gains) / 14
    out["rsi14"] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    trs = [max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
           for a, b in zip(bars[-15:-1], bars[-14:])]
    out["atr14"] = sum(trs) / 14
    out["ret_20d"] = c[-1] / c[-21] - 1
    out["ret_60d"] = c[-1] / c[-61] - 1
    rets = [math.log(c[i] / c[i - 1]) for i in range(len(c) - 20, len(c))]
    mu = sum(rets) / 20
    out["vol_20d_ann"] = math.sqrt(sum((r - mu) ** 2 for r in rets) / 19) * math.sqrt(252)
    out["adv20_usd"] = sum(b["volume"] * b["close"] for b in bars[-20:]) / 20
    hi = max(c[-252:])
    out["pct_from_52w_high"] = c[-1] / hi - 1
    peak, mdd = c[-60], 0.0
    for x in c[-60:]:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    out["max_drawdown_60d"] = mdd
    bc = [b["close"] for b in view.bars(benchmark)]
    out["rel_strength_60d_vs_benchmark"] = out["ret_60d"] - (bc[-1] / bc[-61] - 1)
    # independent fundamentals evidence: raw filings, by filing date (NOT Merlin's numbers)
    fs = view.filings(symbol)
    if len(fs) >= 8:
        cur, prev = sum(f["revenue"] for f in fs[-4:]), sum(f["revenue"] for f in fs[-8:-4])
        out["filed_ttm_revenue_growth"] = cur / prev - 1
        out["filed_max_fcf_margin_8q"] = max(f["fcf"] / f["revenue"] for f in fs[-8:])
        out["filed_last_q_yoy_growth"] = fs[-1]["revenue"] / fs[-5]["revenue"] - 1
        out["filed_ttm_net_income"] = sum(f["net_income"] for f in fs[-4:])
        out["filed_diluted_shares"] = fs[-1]["diluted_shares"]
        out["filed_pe_ttm"] = c[-1] * fs[-1]["diluted_shares"] / out["filed_ttm_net_income"]
    return out


def technical_stance(ind: dict) -> tuple[Stance, float]:
    up = ind["close"] > ind["sma50"] > ind["sma200"] and (ind["sma50_slope_10d"] or 0) > 0
    down = ind["close"] < ind["sma50"] and ind["close"] < ind["sma200"]
    if up and ind["rsi14"] < 78:
        return Stance.BULLISH, 0.65
    if down:
        return Stance.BEARISH, 0.65
    return Stance.NEUTRAL, 0.5


# ------------------------------------------------------------------ TradingAgents data vendor

class _SyntheticVendor:
    """Serves TradingAgents' tool calls from the run's point-in-time view."""

    def __init__(self, view: PointInTimeView):
        self.view = view

    def route(self, method, *args, **kwargs):
        v = self.view
        if method == "get_stock_data":
            sym, start, end = args[:3]
            rows = [b for b in v.bars(sym) if start <= b["date"] <= min(end, v.cutoff.isoformat())]
            head = f"# {sym} daily OHLCV {start}..{end} (split-adjusted as of {v.cutoff}); source: {v.source}\n"
            return head + "Date,Open,High,Low,Close,Volume\n" + "\n".join(
                f"{b['date']},{b['open']:.2f},{b['high']:.2f},{b['low']:.2f},{b['close']:.2f},{b['volume']}" for b in rows)
        if method == "get_indicators":
            sym, ind = args[0], args[1]
            i = indicators(v, sym, "SPY")
            key = {"close_50_sma": "sma50", "close_200_sma": "sma200", "close_10_ema": "sma20", "rsi": "rsi14",
                   "atr": "atr14"}.get(ind.lower())
            if key is None:
                return f"{ind}: not provided by this vendor (available: close_50_sma, close_200_sma, rsi, atr)"
            return f"{ind} for {sym} as of {v.cutoff}: {i[key]:.4f} (source: {v.source})"
        if method == "get_news":
            sym = args[0]
            items = v.news(sym, days=30)
            return "\n".join(f"- {n['published']} {n['headline']}" for n in items) or f"No {sym} news in window."
        if method == "get_global_news":
            return f"No global/macro news in the dataset ({v.source})."
        if method in ("get_macro_indicators", "get_prediction_markets", "get_insider_transactions"):
            return f"{method}: not available in dataset {v.source}."
        raise DataUnavailable(f"{method}: not served for Quant (fundamentals belong to Merlin)")

    def snapshot(self, symbol, as_of_date, look_back_days=30, indicators_=None):
        i = indicators(self.view, symbol, "SPY")
        rows = self.view.bars(symbol)[-min(look_back_days, 30):]
        return (f"VERIFIED SNAPSHOT {symbol} as of {self.view.cutoff} (source: {self.view.source})\n"
                + json.dumps({k: (round(x, 4) if isinstance(x, float) else x) for k, x in i.items()
                              if not k.startswith("filed_")}) + "\nRecent closes: "
                + ", ".join(f"{b['date']}:{b['close']:.2f}" for b in rows))


def run_tradingagents(view: PointInTimeView, symbol: str, quick_rt: Runtime, deep_rt: Runtime, *,
                      quick_model: str, deep_model: str, analysts=("market", "social", "news"),
                      portfolio: dict | None = None, debate_rounds: int = 1, risk_rounds: int = 1,
                      workdir: str | None = None) -> dict:
    """Run the actual TradingAgentsGraph and return its final state and rating."""
    from tradingagents.default_config import build_default_config
    from tradingagents.graph import trading_graph as tg
    from tradingagents.portfolio import PortfolioContext
    import tradingagents.agents.tools as ta_tools
    import tradingagents.agents.analysts.sentiment_analyst as sa

    vendor = _SyntheticVendor(view)
    work = workdir or tempfile.mkdtemp(prefix="mq-ta-")
    cfg = build_default_config()
    cfg.update({"results_dir": f"{work}/results", "data_cache_dir": f"{work}/cache",
                "memory_log_path": f"{work}/memory/trading_memory.md", "max_debate_rounds": debate_rounds,
                "max_risk_discuss_rounds": risk_rounds, "checkpoint_enabled": False, "max_tool_rounds": 3,
                "llm_provider": "anthropic", "quick_think_llm": quick_model, "deep_think_llm": deep_model})

    class _Tier:
        def __init__(self, llm):
            self.llm = llm

        def get_llm(self):
            return self.llm

    quick = build_chat_model(quick_rt, quick_model)
    deep = build_chat_model(deep_rt, deep_model)
    profile = view.profile(symbol)
    identity = {"name": profile.get("name"), "sector": profile.get("sector"), "exchange": profile.get("exchange"),
                "currency": profile.get("currency")}

    class _Settled:
        failed = []

    with ExitStack() as st:
        st.enter_context(mock.patch.object(tg, "create_tier_client",
                                           lambda config, tier, **kw: _Tier(deep if tier == "deep" else quick)))
        st.enter_context(mock.patch.object(tg, "resolve_instrument_identity", lambda t: identity))
        st.enter_context(mock.patch.object(ta_tools, "route_to_vendor", vendor.route))
        st.enter_context(mock.patch.object(ta_tools, "build_verified_market_snapshot", vendor.snapshot))
        st.enter_context(mock.patch.object(sa, "fetch_stocktwits_messages",
                                           lambda *a, **k: f"StockTwits: not available in dataset {view.source}."))
        st.enter_context(mock.patch.object(sa, "fetch_reddit_posts",
                                           lambda *a, **k: f"Reddit: not available in dataset {view.source}."))
        st.enter_context(mock.patch.object(tg.TradingAgentsGraph, "settle_all_pending",
                                           lambda self, wait=True: _Settled()))
        graph = tg.TradingAgentsGraph(selected_analysts=list(analysts), debug=False, config=cfg)
        pc = PortfolioContext.model_validate(portfolio) if portfolio else None
        t0 = time.time()
        state, rating = graph.propagate(symbol, view.cutoff.isoformat(), portfolio=pc)
    return {"state": state, "rating": str(rating), "seconds": time.time() - t0}


# ------------------------------------------------------------------ Stage A report

def run_quant(view: PointInTimeView, symbol: str, benchmark: str, quick_rt: Runtime, deep_rt: Runtime, *,
              quick_model: str, deep_model: str, portfolio: dict | None = None,
              analysts=("market", "social", "news"), debate_rounds: int = 1, risk_rounds: int = 1) -> QuantReport:
    ind = indicators(view, symbol, benchmark)
    tech, conv = technical_stance(ind)
    bundle = {"instrument": symbol, "cutoff": view.cutoff.isoformat(), "data_source": view.source,
              "indicators": ind, "portfolio": portfolio}
    digest = hashlib.sha256(json.dumps(bundle, sort_keys=True, default=str).encode()).hexdigest()
    notes, rating, reports, seconds = [], None, {}, 0.0
    calls0 = quick_rt.meter.calls + deep_rt.meter.calls
    try:
        res = run_tradingagents(view, symbol, quick_rt, deep_rt, quick_model=quick_model, deep_model=deep_model,
                                analysts=analysts, portfolio=portfolio, debate_rounds=debate_rounds,
                                risk_rounds=risk_rounds)
        s, rating, seconds = res["state"], res["rating"], res["seconds"]
        for k in ("market_report", "sentiment_report", "news_report", "investment_plan",
                  "trader_investment_plan", "final_trade_decision"):
            if s.get(k):
                reports[k] = str(s[k])
        debate = s.get("investment_debate_state") or {}
        reports["bull_history"] = str(debate.get("bull_history", ""))
        reports["bear_history"] = str(debate.get("bear_history", ""))
        risk = s.get("risk_debate_state") or {}
        reports["risk_debate"] = str(risk.get("history", ""))
    except Exception as exc:  # unavailable runtime / API: recorded, never papered over
        notes.append(f"TradingAgents run failed: {type(exc).__name__}: {exc}")
    stance = RATING_TO_STANCE.get(rating, Stance.INSUFFICIENT_EVIDENCE) if rating else Stance.INSUFFICIENT_EVIDENCE
    if rating and stance == Stance.INSUFFICIENT_EVIDENCE:
        notes.append(f"TradingAgents rating {rating!r} is not a tradeable rating")
    if stance != Stance.INSUFFICIENT_EVIDENCE and stance != tech:
        notes.append(f"TradingAgents rating {rating} differs from the deterministic technical read {tech.value}")
    m = quick_rt.meter
    d = deep_rt.meter
    cost = (m.cost_usd + d.cost_usd) if (m.cost_known and d.cost_known) else None
    synthetic = "SYNTHETIC" in view.source.upper()
    f = lambda k: ind.get(k)  # noqa: E731
    return QuantReport(
        report_id=stable_id("quant", {"bundle": digest, "runtime": quick_rt.name}),
        instrument=symbol, market=view.profile(symbol).get("exchange", "US"), analysis_date=view.cutoff,
        data_cutoff=view.cutoff,
        indicators={k: (round(v, 6) if isinstance(v, float) else v) for k, v in ind.items() if not isinstance(v, str)},
        technical_assessment=(f"Close {f('close'):.2f}; SMA50 {f('sma50'):.2f}; SMA200 {f('sma200'):.2f}; "
                              f"RSI14 {f('rsi14'):.1f}; deterministic technical read {tech.value}. "
                              + reports.get("market_report", "")[:1500]),
        momentum_assessment=(f"20d {f('ret_20d'):+.1%}, 60d {f('ret_60d'):+.1%}, 60d relative to {benchmark} "
                             f"{f('rel_strength_60d_vs_benchmark'):+.1%}, {f('pct_from_52w_high'):+.1%} from 52w high."),
        news_sentiment_assessment=(reports.get("news_report", "") + "\n" + reports.get("sentiment_report", ""))[:2000]
        or "No news/sentiment report (TradingAgents did not complete).",
        volatility_liquidity_risks=(f"20d realised vol {f('vol_20d_ann'):.1%} annualised; ATR14 {f('atr14'):.2f}; "
                                    f"60d max drawdown {f('max_drawdown_60d'):.1%}; ADV20 ${f('adv20_usd') / 1e9:.2f}bn."),
        bull_case=reports.get("bull_history", "")[:2000] or "n/a",
        bear_case=reports.get("bear_history", "")[:2000] or "n/a",
        risk_reward=reports.get("investment_plan", "")[:2000] or "n/a",
        entry_exit_considerations=reports.get("trader_investment_plan", "")[:2000] or "n/a",
        tradingagents_rating=rating, tradingagents_reports=reports, stance=stance,
        conviction=conv if stance == tech else 0.5,
        sources=[Source(name=view.source, kind="synthetic_fixture" if synthetic else "market_data",
                        as_of=view.cutoff.isoformat(), reference="bars, news, filings by filing date"),
                 Source(name=f"TauricResearch/TradingAgents@{TA_COMMIT[:7]}", kind="model_output",
                        as_of=view.cutoff.isoformat(), reference="TradingAgentsGraph.propagate")],
        run=RunMeta(runtime=quick_rt.name, models=sorted(m.models | d.models),
                    model_calls=m.calls + d.calls - calls0, failed_calls=m.failed + d.failed,
                    input_tokens=m.input_tokens + d.input_tokens, output_tokens=m.output_tokens + d.output_tokens,
                    cost_usd=cost, wall_seconds=round(seconds, 2),
                    context_id=f"quant-{os.getpid()}-{digest[:8]}", inputs_digest=digest, notes=notes))


# ------------------------------------------------------------------ Stage B challenge

def challenge(merlin: MerlinReport, quant: QuantReport, rt: Runtime) -> ChallengeReport:
    ind = quant.indicators
    ch: list[Challenge] = []
    base = next(s for s in merlin.scenarios if s.name == "base")
    bear = next(s for s in merlin.scenarios if s.name == "bear")
    obs_g = ind.get("filed_ttm_revenue_growth")
    if obs_g is not None and base.revenue_cagr > obs_g + 0.10:
        ch.append(Challenge(topic="growth_expectations", severity="high",
                            finding=f"Base revenue CAGR {base.revenue_cagr:.1%} exceeds filed TTM growth {obs_g:.1%} by "
                                    f"{base.revenue_cagr - obs_g:.1%}: unsupported by reported results.",
                            supported_by=["filings: filed_ttm_revenue_growth"]))
    elif obs_g is not None and base.revenue_cagr > obs_g:
        ch.append(Challenge(topic="growth_expectations", severity="low",
                            finding=f"Base CAGR {base.revenue_cagr:.1%} is above filed TTM growth {obs_g:.1%}.",
                            supported_by=["filings: filed_ttm_revenue_growth"]))
    if base.terminal_growth > 0.035:
        ch.append(Challenge(topic="financial_assumptions", severity="high",
                            finding=f"Terminal growth {base.terminal_growth:.1%} is above a long-run nominal GDP proxy (3.5%).",
                            supported_by=["policy: terminal growth ceiling 3.5%"]))
    max_m = ind.get("filed_max_fcf_margin_8q")
    if max_m is not None and base.terminal_margin > max_m + 0.05:
        ch.append(Challenge(topic="financial_assumptions", severity="high",
                            finding=f"Terminal FCF margin {base.terminal_margin:.1%} is above the best filed margin in 8 "
                                    f"quarters ({max_m:.1%}) by more than 5pp.",
                            supported_by=["filings: filed_max_fcf_margin_8q"]))
    spread = base.wacc - base.terminal_growth
    tv_share = merlin.financial_metrics.get("pv_terminal_share_base") or 0
    if spread < 0.04 or tv_share > 0.75:
        ch.append(Challenge(topic="valuation_sensitivity", severity="high" if spread < 0.03 else "medium",
                            finding=f"WACC-g spread {spread:.1%}; terminal value {tv_share:.0%} of EV: value is highly "
                                    "sensitive to discount-rate and terminal assumptions.",
                            supported_by=["merlin valuation assumptions (as published)"]))
    # re-run Merlin's own base case at WACC +1pp with Quant's observed inputs
    m = merlin.financial_metrics
    try:
        shocked = dcf(m["ttm_revenue"], m["fcf_margin"], base.revenue_cagr, base.terminal_margin, base.wacc + 0.01,
                      base.terminal_growth, m["net_cash"], m["diluted_shares"])["value_per_share"]
        if (base.value_per_share > merlin.reference_price) != (shocked > merlin.reference_price):
            ch.append(Challenge(topic="valuation_sensitivity", severity="high",
                                finding=f"At WACC +1pp the base value falls to {shocked:.2f}, below the price "
                                        f"{merlin.reference_price:.2f}: the upside does not survive a 1pp rate shock.",
                                supported_by=["recomputed dcf() at wacc+0.01"]))
    except (KeyError, TypeError, ValueError):
        ch.append(Challenge(topic="evidence_quality", severity="medium",
                            finding="Could not recompute Merlin's DCF from the published metrics.", supported_by=[]))
    below200 = ind.get("sma200") is not None and ind["close"] < ind["sma200"]
    if merlin.stance == Stance.BULLISH and (quant.stance == Stance.BEARISH or below200):
        ch.append(Challenge(topic="contrary_market_evidence", severity="high" if quant.stance == Stance.BEARISH else "medium",
                            finding=f"Fundamental BULLISH call against a market that is {quant.stance.value} "
                                    f"(close {ind['close']:.2f} vs SMA200 {ind.get('sma200') or float('nan'):.2f}, "
                                    f"60d {ind.get('ret_60d', 0):+.1%}).",
                            supported_by=["indicators: close, sma200, ret_60d", "TradingAgents rating"]))
        ch.append(Challenge(topic="market_timing", severity="medium",
                            finding="Entry now buys into a falling trend; fundamentals may be right and early.",
                            supported_by=["indicators: sma50_slope_10d, max_drawdown_60d"]))
    if bear.upside_pct < -0.30:
        ch.append(Challenge(topic="downside_risks", severity="medium",
                            finding=f"Bear case {bear.upside_pct:+.0%}: a large drawdown if the bear assumptions hold.",
                            supported_by=["merlin bear scenario (as published)"]))
    if any("SYNTHETIC" in u.upper() for u in merlin.unknowns) or not merlin.sources:
        ch.append(Challenge(topic="evidence_quality", severity="low",
                            finding="Merlin's evidence is synthetic fixture data; conclusions do not describe a real company.",
                            supported_by=["merlin sources/unknowns"]))

    highs = [c for c in ch if c.severity == "high"]
    opposed = {merlin.stance, quant.stance} == {Stance.BULLISH, Stance.BEARISH}
    if Stance.INSUFFICIENT_EVIDENCE in (merlin.stance, quant.stance):
        verdict = Verdict.INCONCLUSIVE
    elif opposed or len(highs) >= 2:
        verdict = Verdict.DISAGREE
    elif merlin.stance == quant.stance and not highs:
        verdict = Verdict.AGREE
    else:
        verdict = Verdict.PARTIALLY_AGREE
    unsupported = [c.finding for c in ch if c.topic in ("growth_expectations", "financial_assumptions")
                   and c.severity == "high"]

    payload = {"merlin": {"stance": merlin.stance.value, "thesis": merlin.thesis,
                          "scenarios": [s.model_dump() for s in merlin.scenarios]},
               "quant_independent": {"stance": quant.stance.value, "rating": quant.tradingagents_rating,
                                     "indicators": ind},
               "deterministic_challenges": [c.model_dump() for c in ch], "verdict": verdict.value}
    before = rt.meter.calls
    r = rt.complete((AGENT_DIR / "quant_challenge.md").read_text(),
                    "CHALLENGE INPUTS:\n```json\n" + json.dumps(payload, indent=1, default=str) + "\n```")
    try:
        reasoning = extract_json(r.text).get("reasoning", r.text)
    except ValueError:
        reasoning = r.text
    return ChallengeReport(
        report_id=stable_id("challenge", {"m": merlin.report_id, "q": quant.report_id}),
        merlin_report_id=merlin.report_id, quant_report_id=quant.report_id, instrument=merlin.instrument,
        verdict=verdict, challenges=ch, unsupported_assumptions=unsupported, reasoning=str(reasoning),
        run=RunMeta(runtime=rt.name, models=sorted(rt.meter.models), model_calls=rt.meter.calls - before,
                    failed_calls=rt.meter.failed, input_tokens=r.input_tokens, output_tokens=r.output_tokens,
                    cost_usd=r.cost_usd, wall_seconds=round(r.seconds, 2), context_id=r.context_id,
                    inputs_digest=hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest(),
                    notes=["verdict by fixed rules over the listed challenges; reasoning by the runtime"]))


# ------------------------------------------------------------------ scripted test doubles

def scripted_ta_responder(view: PointInTimeView, symbol: str):
    """TEST DOUBLE for TradingAgents' LLM calls. Analysts call their tools once, then
    report the tool output; decision agents answer from the deterministic technical read."""
    tech, _ = technical_stance(indicators(view, symbol, "SPY"))
    rating = {Stance.BULLISH: "Overweight", Stance.BEARISH: "Underweight", Stance.NEUTRAL: "Hold"}[tech]

    def respond(system: str, prompt: str) -> str:
        if "TOOLS. You may call" in system:
            if "[TOOL RESULT" not in prompt:
                if "get_verified_market_snapshot" in system:
                    return json.dumps({"tool_calls": [{"name": "get_verified_market_snapshot",
                                                       "args": {"curr_date": view.cutoff.isoformat()}}]})
                if "get_news" in system:
                    start = (view.cutoff - timedelta(days=30)).isoformat()
                    return json.dumps({"tool_calls": [{"name": "get_news", "args": {
                        "start_date": start, "end_date": view.cutoff.isoformat()}}]})
            tool_text = prompt.split("[TOOL RESULT", 1)[-1][:1200]
            return f"[scripted analyst] Report from tool data:\n{tool_text}\nTechnical read: {tech.value}."
        if "Portfolio Manager" in system or "portfolio manager" in prompt.lower()[:4000]:
            return f"**Rating**: {rating}\n\n[scripted] Final decision follows the technical read ({tech.value})."
        if "trader" in system.lower()[:400]:
            return f"[scripted] Trader plan. FINAL TRANSACTION PROPOSAL: **{ {'BULLISH': 'BUY', 'BEARISH': 'SELL'}.get(tech.value, 'HOLD') }**"
        return f"[scripted] Rating: {rating}. Argument consistent with the technical read {tech.value}."
    return respond


def scripted_challenge(system: str, prompt: str) -> str:
    inp = extract_json(prompt)
    lines = [f"- {c['topic']} ({c['severity']}): {c['finding']}" for c in inp["deterministic_challenges"]]
    return json.dumps({"reasoning": f"[scripted] Verdict {inp['verdict']}. Merlin {inp['merlin']['stance']} vs "
                                    f"Quant {inp['quant_independent']['stance']}.\n" + "\n".join(lines)})
