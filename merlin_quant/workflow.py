"""Research Coordinator: sequences the workflow; draws no investment conclusions.

    Stage A  Merlin and Quant run independently, in separate execution contexts,
             on the same instrument, cutoff and verified data. Quant's input bundle
             contains nothing from Merlin (asserted, and recorded by digest).
    Stage B  Quant receives Merlin's finished report and challenges it.
    Stage C  Joint assessment (both leads quoted verbatim, disagreements kept),
             then a typed paper-trade proposal sized by deterministic code.
    Then     risk gate -> (approval in PAPER_MANUAL) -> paper execution -> ledger
             -> portfolio snapshot -> performance record.

The coordinator never edits a lead's report. Research records and portfolio
actions are stored separately (records/ vs ledger events) and linked by id.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from merlin_quant.calendar import NY, fmt_times, last_completed_session, next_trading_day
from merlin_quant.config import Config
from merlin_quant.contracts import (Action, ChallengeReport, JointAssessment, MerlinReport, Mode, QuantReport,
                                    RiskGateResult, TradeProposal)
from merlin_quant.data import MarketData
from merlin_quant.decision import build_proposal, synthesize
from merlin_quant.engine import EngineError, PaperEngine
from merlin_quant import calls as call_log
from merlin_quant import guards
from merlin_quant.merlin import FSI_PLUGINS, run_merlin, scripted_merlin
from merlin_quant.quant import challenge, run_quant, scripted_challenge, scripted_ta_responder
from merlin_quant import lessons as lessons_loop
from merlin_quant import risk as risk_gate
from merlin_quant.runtime import ScriptedRuntime, make_runtime

logging.getLogger("tradingagents").setLevel(logging.ERROR)
FSI_HOME = Path.home() / ".cache" / "merlin_quant" / "financial-services"


class Team:
    def __init__(self, home: str | Path, cfg: Config, md: MarketData, runtime_override: str | None = None):
        self.cfg, self.md = cfg, md
        self.engine = PaperEngine(home, cfg, md)
        self.override = runtime_override
        self.events: list[str] = []
        self.last_call: dict | None = None  # the pre-registered call of the latest stage C (merlin_quant.calls)

    # ------------------------------------------------------------ runtimes
    def _spec(self, lead: str) -> dict:
        spec = dict(self.cfg.runtimes.get(lead, {}))
        if self.override:
            spec["runtime"] = self.override
        return spec

    def _merlin_rt(self, view, symbol):
        spec = self._spec("merlin")
        dirs = [str(FSI_HOME / "plugins" / p) for p in FSI_PLUGINS] if FSI_HOME.exists() else []
        return make_runtime(spec, responder=scripted_merlin, plugin_dirs=dirs, tools="Skill,Read" if dirs else "")

    def _quant_rts(self, view, symbol):
        spec = self._spec("quant")
        if spec.get("runtime", "scripted") == "scripted":
            resp = scripted_ta_responder(view, symbol)
            return ScriptedRuntime(resp), ScriptedRuntime(resp), "scripted-quick", "scripted-deep"
        q = make_runtime(spec, model_key="quick_model")
        d = make_runtime(spec, model_key="deep_model")
        return q, d, spec.get("quick_model"), spec.get("deep_model")

    # ------------------------------------------------------------ stages
    def stage_a(self, symbol: str, cutoff: date, merlin_overrides: dict | None = None,
                only: str | None = None, now: datetime | None = None) -> tuple[MerlinReport | None, QuantReport | None]:
        view = self.md.view(cutoff)
        fresh = guards.check_data_freshness(view, symbol, self.cfg.guards, guards.known_by_time(cutoff, now))
        if fresh.details.get("stale"):  # Merlin is told, and capped at NEUTRAL when the check blocks
            view.context["data_freshness"] = {**fresh.details, "plain": fresh.plain,
                                              "cap_stance": fresh.outcome == "BLOCK" and self.cfg.guards.enabled}
        st = self.engine.ledger.state()
        portfolio = {"cash": round(st.cash, 2), "currency": st.base_currency,
                     "positions": [{"ticker": s, "quantity": h.quantity, "average_price": round(h.avg_cost, 4)}
                                   for s, h in st.holdings.items() if h.quantity]} if st.initialized else None
        qspec = self._spec("quant")

        def do_merlin():
            return run_merlin(view, symbol, self.cfg.portfolio.universe, cutoff, self._merlin_rt(view, symbol),
                              overrides=merlin_overrides, lessons=lessons_loop.prompt_block(self.engine.home, "merlin"))

        def do_quant():
            q, d, qm, dm = self._quant_rts(view, symbol)
            return run_quant(view, symbol, self.cfg.portfolio.benchmark, q, d, quick_model=qm, deep_model=dm,
                             portfolio=portfolio, analysts=tuple(qspec.get("analysts", ("market", "social", "news"))),
                             debate_rounds=qspec.get("max_debate_rounds", 1),
                             risk_rounds=qspec.get("max_risk_rounds", 1))

        m = q = None
        with ThreadPoolExecutor(max_workers=2) as ex:  # separate contexts, neither sees the other
            fm = ex.submit(do_merlin) if only in (None, "merlin") else None
            fq = ex.submit(do_quant) if only in (None, "quant") else None
            m = fm.result() if fm else None
            q = fq.result() if fq else None
        if m:
            self.engine.records.put("merlin", m.report_id, m)
            self.engine.ledger.append("RESEARCH", f"research:{m.report_id}", {"record": "merlin", "id": m.report_id,
                                      "instrument": symbol, "stance": m.stance.value, "inputs_digest": m.run.inputs_digest})
        if q:
            self.engine.records.put("quant", q.report_id, q)
            self.engine.ledger.append("RESEARCH", f"research:{q.report_id}", {"record": "quant", "id": q.report_id,
                                      "instrument": symbol, "stance": q.stance.value, "inputs_digest": q.run.inputs_digest,
                                      "saw_merlin_report": False})
        return m, q

    def stage_b(self, m: MerlinReport, q: QuantReport) -> ChallengeReport:
        spec = self._spec("challenge")
        rt = make_runtime(spec, responder=scripted_challenge)
        c = challenge(m, q, rt, lessons=lessons_loop.prompt_block(self.engine.home, "challenge"))
        self.engine.records.put("challenge", c.report_id, c)
        self.engine.ledger.append("RESEARCH", f"research:{c.report_id}", {"record": "challenge", "id": c.report_id,
                                  "verdict": c.verdict.value})
        return c

    def stage_c(self, m, q, c, now: datetime | None = None) -> tuple[JointAssessment, TradeProposal]:
        st = self.engine.ledger.state()
        snap = self.engine.snapshot(q.data_cutoff)
        held_w = next((p.weight or 0.0 for p in snap.positions if p.instrument == m.instrument), 0.0)
        j = synthesize(m, q, c, self.cfg, held_w)
        gr = guards.evaluate(self.md.view(q.data_cutoff), m.instrument, self.cfg, now=now)
        would_have = j.proposed_action
        if j.proposed_action == Action.BUY and gr.blocking:  # checks before buying: the BUY becomes WAIT
            j = j.model_copy(update={
                "proposed_action": Action.HOLD if held_w > 0 else Action.NO_ACTION,
                "proposed_target_weight": round(held_w, 6),
                "rationale": f"WAIT (the policy alone would BUY): {gr.wait_reason()} Policy: {j.rationale}",
                "unresolved_risks": [r.plain for r in gr.blocking] + list(j.unresolved_risks)})
        self.engine.records.put("joint", j.assessment_id, j)
        p = build_proposal(j, m, q, c, st, self.md, self.cfg, now=now)
        p = p.model_copy(update={"guard_checks": {**gr.as_dict(), "policy_action": would_have.value,
                                                  "held_back": would_have != j.proposed_action}})
        if p.guard_checks["held_back"]:
            p.eligibility_reasons.append(gr.wait_reason())
        self.engine.records.put("proposals", p.proposal_id, p)
        self.engine.ledger.append("PROPOSAL", f"proposal:{p.proposal_id}", {"proposal_id": p.proposal_id,
                                  "instrument": p.instrument, "action": p.action.value, "quantity": p.quantity,
                                  "assessment_id": j.assessment_id})
        self._report(m, q, c, j, p)
        self.last_call = call_log.register_team_call(self.engine.home, m, q, c, p, self.md, self.cfg, now=now)
        return j, p

    def research(self, symbol: str, cutoff: date, merlin_overrides: dict | None = None, now=None):
        m, q = self.stage_a(symbol, cutoff, merlin_overrides, now=now)
        c = self.stage_b(m, q)
        j, p = self.stage_c(m, q, c, now=now)
        mode = Mode(self.engine.ledger.state().mode)
        if mode == Mode.PAPER_AUTO and p.eligibility == "ELIGIBLE":
            self.approve(p.proposal_id, "PAPER_AUTO")
            self.execute(p.proposal_id, now=now)
        return m, q, c, j, self.proposal(p.proposal_id)

    # ------------------------------------------------------------ decisions
    def proposal(self, pid: str) -> TradeProposal:
        return TradeProposal.model_validate(self.engine.records.get("proposals", pid))

    def _set_status(self, p: TradeProposal, status: str):
        p.status = status
        (self.engine.records.root / "proposals" / f"{p.proposal_id}.json").write_text(p.model_dump_json(indent=2))

    def approve(self, pid: str, by: str) -> TradeProposal:
        p = self.proposal(pid)
        if p.eligibility != "ELIGIBLE":
            raise EngineError(f"{pid} is {p.action.value}: nothing to approve")
        if Mode(self.engine.ledger.state().mode) == Mode.RESEARCH_ONLY:
            raise EngineError("RESEARCH_ONLY mode: proposals cannot be approved for execution")
        self.engine.ledger.append("APPROVAL", f"approval:{pid}", {"proposal_id": pid, "approved_by": by})
        if p.status == "PROPOSED":
            self._set_status(p, "APPROVED")
        return p

    def reject(self, pid: str, by: str, reason: str) -> TradeProposal:
        p = self.proposal(pid)
        self.engine.ledger.append("REJECTION", f"rejection:{pid}", {"proposal_id": pid, "by": by, "reason": reason})
        self._set_status(p, "REJECTED")
        return p

    def execute(self, pid: str, now: datetime | None = None, crash_after_intent: bool = False):
        p = self.proposal(pid)
        now = now or datetime.now(timezone.utc)
        st = self.engine.ledger.state()
        fill_session = next_trading_day(p.data_cutoff)
        orders = sum(1 for e in self.engine.ledger.events() if e["type"] == "FILL"
                     and e["payload"]["fill_session"] == fill_session.isoformat())
        if pid in st.filled:  # rerun of an executed proposal: return the one recorded execution
            r = RiskGateResult.model_validate(self._risk_for(pid))
            rec = self.engine.execute(p, r)
            if p.status != "EXECUTED":
                self._set_status(p, "EXECUTED")
            return rec, r
        r = risk_gate.evaluate(p, st, self.cfg, self.md, last_completed_session=last_completed_session(now),
                               executed_proposals=st.filled, orders_this_session=orders, now=now)
        self.engine.records.put("risk", r.result_id, r)
        self.engine.ledger.append("RISK_RESULT", f"risk:{r.result_id}", {"proposal_id": pid, "outcome": r.outcome,
                                  "reasons": r.reasons, "config_version": r.config_version})
        if r.outcome != "PASS":
            # REJECT is final for this proposal. BLOCKED is a system state (halt, stale data, calendar): the
            # approval stands and the gate re-evaluates on the next attempt; stale data cannot pass later anyway.
            if r.outcome == "REJECT":
                self._set_status(p, "REJECTED")
            return None, r
        rec = self.engine.execute(p, r, crash_after_intent=crash_after_intent)
        self._set_status(p, "EXECUTED")
        return rec, r

    def _risk_for(self, pid: str) -> dict:
        for r in self.engine.records.list("risk"):
            if r["proposal_id"] == pid and r["outcome"] == "PASS":
                return r
        raise EngineError(f"no passing risk result for {pid}")

    # ------------------------------------------------------------ reports
    def _report(self, m, q, c, j, p) -> Path:
        lines = [
            f"# {m.instrument}: joint assessment {j.assessment_id}",
            f"Data cutoff {q.data_cutoff} (close: {fmt_times(datetime.combine(q.data_cutoff, datetime.min.time().replace(hour=16), tzinfo=NY))}). "
            f"Source: {self.md.source_name}. PAPER ONLY, research not advice.", "",
            "## Checks before buying",
            *[f"- {r['name']}: **{r['outcome']}**. {r['plain']}" for r in p.guard_checks.get("results", [])],
            *([f"- The policy alone said {p.guard_checks['policy_action']}; held back to {p.action.value} (WAIT)."]
              if p.guard_checks.get("held_back") else []), "",
            f"## Merlin ({m.stance.value}, conviction {m.conviction:.2f}) `{m.report_id}`",
            f"Fair value (base) {m.fair_value_base} vs price {m.reference_price} on {m.reference_price_date}.",
            *[f"- {s.name}: CAGR {s.revenue_cagr:.1%}, terminal margin {s.terminal_margin:.1%}, WACC {s.wacc:.1%}, "
              f"g {s.terminal_growth:.1%} -> {s.value_per_share} ({s.upside_pct:+.1%})" for s in m.scenarios],
            f"Thesis: {m.thesis}", f"FSI skills invoked: {', '.join(m.fsi_skills_used) or 'none'}", "",
            f"## Quant independent ({q.stance.value}) `{q.report_id}`",
            f"TradingAgents rating: **{q.tradingagents_rating}**. {q.momentum_assessment}",
            f"{q.volatility_liquidity_risks}", "",
            f"## Quant challenge: **{c.verdict.value}** `{c.report_id}`",
            *[f"- [{x.severity}] {x.topic}: {x.finding}" for x in c.challenges], "", c.reasoning, "",
            "## Agreement", *([f"- {a}" for a in j.agreements] or ["- none"]),
            "## Disagreement (preserved)", *([f"- {d}" for d in j.disagreements] or ["- none"]), "",
            f"## Proposal `{p.proposal_id}`: {p.action.value} {p.quantity} @ ref {p.reference_price} "
            f"(target weight {p.target_weight:.0%})", j.rationale,
        ]
        path = self.engine.home / "reports" / f"{q.data_cutoff}-{m.instrument}-{j.assessment_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
        return path
