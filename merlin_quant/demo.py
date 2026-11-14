"""Reproducible end-to-end demonstration on synthetic data.

    python -m merlin_quant.demo [--runtime scripted|claude_cli] [--home DIR] [--out FILE]

Shows: Merlin research, Quant independent analysis, Quant challenge, joint
assessment, proposal, risk gate, a simulated execution (AAPL), a rejected trade
(an oversized AAPL order), an explicit Merlin/Quant disagreement (NVDA), the
updated fictional portfolio, a performance snapshot and the audit trail.
AAPL/MSFT/NVDA here are synthetic test instruments, not recommendations.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime
from pathlib import Path

from merlin_quant.calendar import NY, fmt_times
from merlin_quant.config import load_config
from merlin_quant.data import synthetic_fixture
from merlin_quant.performance import LIMITATIONS, score_research, series_metrics, trade_stats
from merlin_quant.workflow import Team

CUTOFF = date(2026, 11, 6)
NOW = datetime(2026, 11, 6, 21, 0, tzinfo=NY)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", default="scripted", choices=["scripted", "claude_cli", "anthropic_api"])
    ap.add_argument("--home", default="var/merlin_quant_demo")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    home = Path(a.home)
    if home.exists():
        shutil.rmtree(home)  # the demo home only; never the real paper portfolio
    md, cfg = synthetic_fixture(), load_config()
    team = Team(home, cfg, md, runtime_override=a.runtime)
    team.engine.init()
    team.engine.open()
    L = [f"# Merlin + Quant example run ({a.runtime} runtime)", "",
         f"Data: {md.source_name}. Decision cutoff {CUTOFF} close; evaluated {fmt_times(NOW)}. "
         "PAPER ONLY: synthetic capital, simulated fills, synthetic instruments. Research, not advice.", ""]

    def section(title, sym, approve=True):
        m, q, c, j, p = team.research(sym, CUTOFF, now=NOW)
        L.extend([f"## {title}", "",
                  f"**1. Merlin** `{m.report_id}`: {m.stance.value} (conviction {m.conviction:.2f}); fair value base "
                  f"{m.fair_value_base} vs price {m.reference_price}; FSI skills invoked: "
                  f"{', '.join(m.fsi_skills_used) or 'none (runtime without plugins)'}.",
                  *[f"   - {s.name}: CAGR {s.revenue_cagr:.1%}, terminal FCF margin {s.terminal_margin:.1%}, WACC "
                    f"{s.wacc:.1%}, g {s.terminal_growth:.1%} -> {s.value_per_share} ({s.upside_pct:+.1%})"
                    for s in m.scenarios],
                  f"   - Thesis: {m.thesis[:600]}",
                  f"**2. Quant (independent, saw_merlin_report={q.saw_merlin_report})** `{q.report_id}`: "
                  f"{q.stance.value}; TradingAgents rating **{q.tradingagents_rating}** "
                  f"({q.run.model_calls} model calls). {q.momentum_assessment}",
                  f"**3. Quant challenge** `{c.report_id}`: **{c.verdict.value}**",
                  *[f"   - [{x.severity}] {x.topic}: {x.finding}" for x in c.challenges],
                  f"**4. Joint assessment** `{j.assessment_id}`: agreements {len(j.agreements)}, "
                  f"disagreements {len(j.disagreements)} (preserved).",
                  *[f"   - {d}" for d in j.disagreements],
                  f"**5. Proposal** `{p.proposal_id}`: {p.action.value} {p.quantity} @ ref {p.reference_price} "
                  f"({p.reference_price_date}), target weight {p.target_weight:.0%}. {j.rationale}"])
        if p.eligibility == "ELIGIBLE" and approve:
            team.approve(p.proposal_id, "demo-owner")
            rec, r = team.execute(p.proposal_id, now=NOW)
            L.append(f"**6. Risk gate** `{r.result_id}`: **{r.outcome}** ({len(r.checks)} checks)"
                     + (": " + "; ".join(r.reasons) if r.reasons else ""))
            if rec:
                L.append(f"**7. Simulated execution** `{rec.execution_id}`: {rec.side} {rec.quantity} {rec.instrument} "
                         f"at {rec.fill_price:.4f} on the {rec.fill_session} open, commission {rec.commission}, "
                         f"slippage cost {rec.slippage_cost}. {rec.note}")
        L.append("")
        return m, q, c, j, p

    _, _, _, _, p_aapl = section("A. AAPL: agreement, approved, executed", "AAPL")
    # B. a rejected trade: the same research, but an oversized order
    big = p_aapl.model_copy(update={"proposal_id": "prop-demo-oversized", "quantity": 120, "status": "PROPOSED"})
    team.engine.records.put("proposals", big.proposal_id, big)
    team.approve(big.proposal_id, "demo-owner")
    _, r = team.execute(big.proposal_id, now=NOW)
    L.extend(["## B. Rejected trade: an oversized AAPL order (120 shares)", "",
              f"Risk gate `{r.result_id}`: **{r.outcome}**", *[f"- {x}" for x in r.reasons], ""])
    section("C. NVDA: explicit disagreement (no trade)", "NVDA")
    section("D. MSFT: neutral fundamentals", "MSFT")

    for d in (date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)):
        team.engine.apply_corporate_actions(d)
        team.engine.record_nav(d)
    snap = team.engine.snapshot(date(2026, 11, 13))
    team.engine.records.put("snapshots", f"2026-11-13-{snap.ledger_head}", snap)
    st = team.engine.ledger.state()
    perf = series_metrics([n["nav"] for n in st.nav_history], [n["benchmark_close"] for n in st.nav_history])
    props = [x for x in team.engine.records.list("proposals") if x["status"] == "EXECUTED"]
    scores = score_research(props, {r["report_id"]: r for r in team.engine.records.list("merlin")},
                            {r["report_id"]: r for r in team.engine.records.list("quant")}, md, "SPY", 5,
                            date(2026, 11, 13))
    L.extend(["## 8. Portfolio (2026-11-13 close, fictional)", "",
              f"Cash {snap.cash:,.2f} | NAV {snap.nav:,.2f} | realised {snap.realized_pnl:,.2f} | unrealised "
              f"{snap.unrealized_pnl:,.2f} | fees {snap.fees_paid:,.2f} | gross exposure {snap.gross_exposure:.1%} | "
              f"ledger head `{snap.ledger_head}`",
              *[f"- {x.instrument}: {x.quantity} @ avg {x.avg_cost} mark {x.mark} ({x.mark_date}) "
                f"P&L {x.unrealized_pnl} weight {x.weight:.1%}" for x in snap.positions], "",
              "## 9. Performance snapshot (forward paper trading)", "",
              "```json", json.dumps({"metrics": perf, "trades": trade_stats(st.closed_trades),
                                     "research_scoring": scores}, indent=1, default=str), "```",
              "Limitations: " + " ".join(LIMITATIONS), "",
              "## 10. Audit trail (ledger events)", "", "```"])
    for e in team.engine.ledger.events():
        L.append(f"{e['seq']:>3} {e['type']:<16} {e['hash'][:10]} {json.dumps(e['payload'], default=str)[:150]}")
    L.append("```")
    meters = [team.engine.records.list(k) for k in ("merlin", "quant", "challenge")]
    calls = sum(r["run"]["model_calls"] for rs in meters for r in rs)
    costs = [r["run"]["cost_usd"] for rs in meters for r in rs]
    L.extend(["", f"Model calls: {calls}; reported cost USD: "
              f"{sum(c for c in costs if c is not None):.4f}{' (some unknown)' if None in costs else ''}."])
    text = "\n".join(L) + "\n"
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
