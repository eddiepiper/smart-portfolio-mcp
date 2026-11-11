"""Research Challenger agent (reference: AI Hedge Fund's evidence-carrying signal pattern).

Challenges a TradingAgents thesis. It does not vote BUY/SELL, allocate capital or
trade. Deterministic checks find unsupported claims, contradictions with
validated market data, missing information and downside scenarios. An optional
LLM drafts counter-arguments from the same evidence only; that text is labelled
and checked for independence (a paraphrase of the thesis is flagged, not passed
off as a second opinion). Equivalent requests are served from cache.
"""
from __future__ import annotations

import hashlib
import json
import operator
import re
from typing import Any, Optional

from ...core.contracts import (Action, MarketIntelligenceReport, QualityStatus, ResearchChallengeReport, ResearchReport,
                               Severity, ValidationIssue)
from ..base import Agent

OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne}

# keyword claims that can be checked against deterministic context metrics
KEYWORD_CHECKS = [
    (r"\buptrend\b|\btrending (up|higher)\b", lambda c: c.get("uptrend") is True, "thesis says uptrend, but price/MA50/MA200 structure is not an uptrend"),
    (r"\bdowntrend\b", lambda c: not c.get("uptrend", False) and c.get("last_close", 0) < c.get("ma200", 0), "thesis says downtrend, but price is above its 200-day average"),
    (r"\boversold\b", lambda c: c.get("rsi14", 100) < 35, "thesis calls it oversold, but RSI(14) is {rsi14:.1f}"),
    (r"\boverbought\b", lambda c: c.get("rsi14", 0) > 65, "thesis calls it overbought, but RSI(14) is {rsi14:.1f}"),
    (r"above (its |the )?200[- ]day", lambda c: c.get("last_close", 0) > c.get("ma200", float("inf")), "thesis says above the 200-day average; close {last_close:.2f} vs MA200 {ma200:.2f}"),
    (r"below (its |the )?200[- ]day", lambda c: c.get("last_close", float("inf")) < c.get("ma200", 0), "thesis says below the 200-day average; close {last_close:.2f} vs MA200 {ma200:.2f}"),
    (r"(near|at) (its |a |the )?(52[- ]week|yearly) high", lambda c: c.get("last_close", 0) >= 0.95 * c.get("high_52w", float("inf")), "thesis says near the 52-week high; close {last_close:.2f} vs high {high_52w:.2f}"),
]

REQUIRED_SECTIONS = {"market": "technical / market analysis", "news": "news analysis",
                     "fundamentals": "fundamental analysis", "sentiment": "sentiment analysis"}


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 3}


def similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


class ResearchChallenger(Agent):
    agent_id = "research_challenger"
    INDEPENDENCE_THRESHOLD = 0.6

    def register(self):
        self.tool("review_investment_thesis", self.review_investment_thesis, "write_research_challenge", timeout_s=180)
        self.tool("identify_unsupported_claims", self.identify_unsupported_claims, "read_research")
        self.tool("analyse_downside_scenarios", self.analyse_downside_scenarios, "read_research")
        self.tool("identify_missing_evidence", self.identify_missing_evidence, "read_research")
        return self

    # ---------------------------------------------------------- deterministic tools
    def identify_unsupported_claims(self, research: ResearchReport) -> list[str]:
        known = {e.evidence_id for e in research.evidence}
        out = []
        for c in research.claims:
            if not c.evidence_refs:
                out.append(f"no evidence cited: \"{c.text}\"")
            else:
                missing = [r for r in c.evidence_refs if r not in known and not self.rt.evidence.exists(r)]
                if missing:
                    out.append(f"cites evidence that does not exist ({', '.join(missing)}): \"{c.text}\"")
        return out

    def check_contradictions(self, research: ResearchReport, ctx: dict) -> list[ValidationIssue]:
        issues = []
        for c in research.claims:
            a = c.assertion
            if not a:
                continue
            metric = ctx.get(a.get("metric")) if a.get("metric") in ctx else _derived(ctx, a.get("metric"))
            if metric is None:
                issues.append(ValidationIssue(check="assertion_unverifiable", severity=Severity.WARNING,
                                              message=f"cannot verify \"{c.text}\": metric {a.get('metric')} unavailable"))
                continue
            if not OPS[a["op"]](metric, a["value"]):
                issues.append(ValidationIssue(check="contradiction", severity=Severity.ERROR,
                                              message=f"\"{c.text}\" is contradicted: {a['metric']} = {metric:.4g}, claim needs {a['op']} {a['value']}",
                                              evidence={"metric": a["metric"], "actual": metric}))
        text = " ".join([research.thesis] + [c.text for c in research.claims])
        if ctx.get("last_close") is not None:
            for pattern, holds, msg in KEYWORD_CHECKS:
                if re.search(pattern, text, re.IGNORECASE) and not holds(ctx):
                    try:
                        m = msg.format(**ctx)
                    except (KeyError, ValueError):
                        m = msg
                    issues.append(ValidationIssue(check="contradiction", severity=Severity.ERROR, message=m))
        return issues

    def identify_missing_evidence(self, research: ResearchReport, market: Optional[MarketIntelligenceReport]) -> list[str]:
        out = [f"research feed failed: {f}" for f in research.failed_feeds]
        kinds = {e.kind for e in research.evidence}
        for k, label in REQUIRED_SECTIONS.items():
            if k not in kinds:
                out.append(f"no {label} evidence attached")
        if market is None:
            out.append("no independent market data supplied to check the thesis")
        else:
            out += market.missing_information
            if market.data_quality_status != QualityStatus.VALID:
                out.append(f"market data quality is {market.data_quality_status.value if market.data_quality_status else 'unvalidated'}")
        return out

    def analyse_downside_scenarios(self, ctx: dict) -> list[dict[str, Any]]:
        p = ctx.get("last_close")
        if not p:
            return []
        sc = []
        for name, level in (("retest 10-day low", ctx.get("low_10d")), ("fall to 200-day average", ctx.get("ma200")),
                            ("revisit 52-week low", ctx.get("low_52w"))):
            if level and level < p:
                sc.append({"scenario": name, "price": round(level, 2), "change_pct": round((level / p - 1) * 100, 2)})
        if ctx.get("atr14"):
            lv = p - 2 * ctx["atr14"]
            sc.append({"scenario": "two-ATR adverse day sequence", "price": round(lv, 2), "change_pct": round((lv / p - 1) * 100, 2)})
        return sorted(sc, key=lambda s: s["change_pct"], reverse=True)

    def _deterministic_counters(self, ctx: dict, research: ResearchReport) -> list[str]:
        out = []
        if not ctx:
            return ["no validated market context: the thesis cannot be checked against price data"]
        if ctx.get("last_close", 0) < ctx.get("ma200", 0):
            out.append(f"price {ctx['last_close']:.2f} is below its 200-day average {ctx['ma200']:.2f}: the long-term trend is against a long position")
        if ctx.get("rsi14", 50) > 70:
            out.append(f"RSI(14) {ctx['rsi14']:.1f} is overbought: entry risk after a strong run")
        if ctx.get("atr14") and ctx.get("ma20") and ctx["last_close"] - ctx["ma20"] > 2 * ctx["atr14"]:
            out.append("price is more than 2 ATR above its 20-day average: extended, pullback risk")
        if ctx.get("high_52w") and ctx["last_close"] < 0.8 * ctx["high_52w"]:
            out.append(f"price is {100 * (1 - ctx['last_close'] / ctx['high_52w']):.0f}% below its 52-week high: overhead supply")
        if research.proposed_action == Action.BUY and not ctx.get("uptrend"):
            out.append("a BUY is proposed while the trend filter (price > MA50 > MA200, MA50 rising) is not met")
        return out

    def _supporting(self, ctx: dict) -> list[str]:
        out = []
        if ctx.get("uptrend"):
            out.append("price above a rising 50-day average, which is above the 200-day average")
        if 35 <= ctx.get("rsi14", 0) <= 65:
            out.append(f"RSI(14) {ctx['rsi14']:.1f} is neutral: not stretched")
        return out

    # -------------------------------------------------------------------- main tool
    def review_investment_thesis(self, research: ResearchReport, market: Optional[MarketIntelligenceReport] = None,
                                 portfolio_context: Optional[dict] = None, use_llm: bool = True) -> ResearchChallengeReport:
        ctx = (market.context if market and market.data_quality_status in (QualityStatus.VALID, QualityStatus.WARNING) else {}) or {}
        key = hashlib.sha256(json.dumps([_content(research),
                                         market.report_id if market else None, ctx, portfolio_context], sort_keys=True, default=str).encode()).hexdigest()[:24]
        cache_id = f"rcr_cache_{key}"
        if self.rt.evidence.exists(cache_id):
            return ResearchChallengeReport(**self.rt.evidence.get(cache_id))

        unsupported = self.identify_unsupported_claims(research)
        contradictions = self.check_contradictions(research, ctx)
        missing = self.identify_missing_evidence(research, market)
        downside = self.analyse_downside_scenarios(ctx)
        counters = self._deterministic_counters(ctx, research) + [i.message for i in contradictions if i.severity == Severity.ERROR]
        limitations = ["deterministic checks cover price/trend facts only; fundamentals and news claims are checked for citations, not truth"]
        llm_used, independence_ok = False, True
        if use_llm:
            evidence_txt = json.dumps({"context": ctx, "downside": downside, "failed_feeds": research.failed_feeds}, default=str)
            prompt = (f"Thesis under review ({research.instrument_id}, rating {research.rating}):\n{research.thesis}\n\n"
                      f"Validated evidence (use ONLY this; do not add facts):\n{evidence_txt}\n\n"
                      "List the strongest counter-arguments and alternative explanations, one per line.")
            resp = self.rt.llm.complete(self.instructions or "You are an independent research challenger.", prompt)
            if resp.text:
                llm_used = True
                sim = similarity(resp.text, research.thesis)
                if sim >= self.INDEPENDENCE_THRESHOLD:
                    independence_ok = False
                    limitations.append(f"LLM counter-thesis is {sim:.0%} similar to the thesis: not an independent view; discarded")
                else:
                    counters += [f"[{resp.label} LLM] {line.strip('-• ').strip()}" for line in resp.text.splitlines() if line.strip()]
            else:
                limitations.append("no LLM available: counter-arguments are deterministic only")
        n_claims = max(len(research.claims), 1)
        if not missing and not unsupported and not contradictions:
            confidence = "high"
        elif len(unsupported) / n_claims <= 0.25 and not any(i.severity == Severity.ERROR for i in contradictions):
            confidence = "medium"
        else:
            confidence = "low"
        rep = ResearchChallengeReport(
            challenged_report_id=research.report_id, instrument_id=research.instrument_id, thesis=research.thesis,
            supporting_arguments=self._supporting(ctx), counterarguments=counters,
            contradictory_evidence=contradictions, unsupported_claims=unsupported, missing_information=missing,
            downside_scenarios=downside,
            assumptions_to_verify=[c.text for c in research.claims if not c.assertion],
            confidence=confidence, limitations=limitations,
            quant_review_recommended=bool(contradictions) or research.proposed_action in (Action.BUY, Action.EXIT) or bool(unsupported),
            independence_ok=independence_ok, llm_used=llm_used)
        self.rt.evidence.put(rep, "research_challenge_report")
        self.rt.evidence.put(rep, "research_challenge_report", obj_id=cache_id)
        return rep


def _content(research: ResearchReport) -> dict:
    """Research content without generated ids/timestamps, so equivalent requests share a cache key."""
    d = research.model_dump(mode="json", exclude={"report_id"})
    ids = {e["evidence_id"]: f"e{i}" for i, e in enumerate(d["evidence"])}
    for e in d["evidence"]:
        e.pop("captured_at", None)
        e["evidence_id"] = ids[e["evidence_id"]]
    for c in d["claims"]:
        c.pop("claim_id", None)
        c["evidence_refs"] = [ids.get(r, r) for r in c["evidence_refs"]]
    return d


def _derived(ctx: dict, metric: Optional[str]) -> Optional[float]:
    if not metric or not ctx.get("last_close"):
        return None
    base = {"close_vs_ma200": "ma200", "close_vs_ma50": "ma50", "close_vs_ma20": "ma20"}
    if metric in base and ctx.get(base[metric]):
        return (ctx["last_close"] / ctx[base[metric]] - 1) * 100
    if metric == "pct_below_52w_high" and ctx.get("high_52w"):
        return (1 - ctx["last_close"] / ctx["high_52w"]) * 100
    return None
