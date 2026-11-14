"""Merlin: Financial Research & Intelligence Lead.

Numbers come from deterministic, reproducible code in this module (metrics,
5-year FCF DCF with Gordon terminal value, bull/base/bear scenarios, a 5x5
WACC x terminal-growth sensitivity grid whose centre equals the base case, and
peer multiples). The research narrative (overview, thesis, catalysts, risks,
unknowns, implications, stance) comes from Merlin's LLM runtime, which is given
the computed numbers and told not to introduce any others.

On the `claude_cli` runtime Merlin runs with Anthropic's financial-services
plugins loaded (`financial-analysis`, `equity-research`, pinned upstream commit)
and the Skill tool enabled; the skills it actually invoked are captured from the
CLI's event stream and recorded in `fsi_skills_used`. Merlin never executes trades.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from merlin_quant.contracts import MerlinReport, RunMeta, Scenario, Source, Stance, stable_id
from merlin_quant.data import PointInTimeView
from merlin_quant.runtime import Runtime, extract_json

AGENT_DIR = Path(__file__).parent / "agents"
FSI_COMMIT = "3e32c2287dcb637c184c01cc0ad82d6af7e9bb0f"
FSI_PLUGINS = ["vertical-plugins/financial-analysis", "vertical-plugins/equity-research"]
FSI_SKILLS = ["equity-research:earnings-analysis", "financial-analysis:dcf-model", "financial-analysis:comps-analysis",
              "equity-research:thesis-tracker"]


# ------------------------------------------------------------------ calc engine

def metrics(view: PointInTimeView, symbol: str) -> dict:
    t = view.ttm(symbol)
    price, pdate = view.close(symbol, max_staleness_sessions=5)
    shares = t["diluted_shares"]
    mcap = price * shares
    ev = mcap + t["debt"] - t["cash"]
    growth = (t["revenue"] / t["revenue_prior_ttm"] - 1) if t.get("revenue_prior_ttm") else None
    return {
        "price": price, "price_date": pdate.isoformat(), "ttm_revenue": t["revenue"], "ttm_revenue_growth": growth,
        "gross_margin": t["gross_profit"] / t["revenue"], "operating_margin": t["operating_income"] / t["revenue"],
        "net_margin": t["net_income"] / t["revenue"], "fcf_margin": t["fcf"] / t["revenue"],
        "ttm_fcf": t["fcf"], "ttm_net_income": t["net_income"], "diluted_shares": shares,
        "eps_ttm": t["net_income"] / shares, "market_cap": mcap, "enterprise_value": ev,
        "net_cash": t["cash"] - t["debt"], "pe_ttm": mcap / t["net_income"], "ev_sales": ev / t["revenue"],
        "fcf_yield": t["fcf"] / mcap, "latest_period_end": t["latest_period_end"], "latest_filed": t["latest_filed"],
    }


def dcf(revenue0: float, fcf_margin0: float, cagr: float, terminal_margin: float, wacc: float, g: float,
        net_cash: float, shares: float, years: int = 5) -> dict:
    """Unlevered-FCF proxy DCF. FCF margin moves linearly from today's to the terminal margin."""
    if wacc <= g:
        raise ValueError("WACC must exceed terminal growth")
    pv, rev, fcf_last = 0.0, revenue0, 0.0
    for y in range(1, years + 1):
        rev *= 1 + cagr
        margin = fcf_margin0 + (terminal_margin - fcf_margin0) * y / years
        fcf_last = rev * margin
        pv += fcf_last / (1 + wacc) ** y
    tv = fcf_last * (1 + g) / (wacc - g)
    pv_tv = tv / (1 + wacc) ** years
    ev = pv + pv_tv
    equity = ev + net_cash
    return {"enterprise_value": ev, "equity_value": equity, "value_per_share": equity / shares,
            "pv_terminal_share": pv_tv / ev}


def default_assumptions(m: dict) -> dict:
    """History-anchored base case; bull/bear are fixed offsets from it (documented, reproducible)."""
    hist = m["ttm_revenue_growth"] if m["ttm_revenue_growth"] is not None else 0.05
    base = max(min(hist * 0.8, 0.30), -0.05)  # fade 20% of trailing growth, capped
    fm = m["fcf_margin"]
    return {
        "base": dict(revenue_cagr=base, terminal_margin=fm, wacc=0.09, terminal_growth=0.025),
        "bull": dict(revenue_cagr=base + 0.05, terminal_margin=fm + 0.03, wacc=0.085, terminal_growth=0.03),
        "bear": dict(revenue_cagr=max(base - 0.08, -0.10), terminal_margin=fm - 0.06, wacc=0.10, terminal_growth=0.02),
    }


def valuation(m: dict, assumptions: dict) -> dict:
    out = {}
    for name in ("bull", "base", "bear"):
        a = assumptions[name]
        r = dcf(m["ttm_revenue"], m["fcf_margin"], a["revenue_cagr"], a["terminal_margin"], a["wacc"],
                a["terminal_growth"], m["net_cash"], m["diluted_shares"])
        out[name] = {**a, **r, "upside_pct": r["value_per_share"] / m["price"] - 1}
    b = assumptions["base"]
    grid = []
    for dw in (-0.01, -0.005, 0.0, 0.005, 0.01):
        row = []
        for dg in (-0.01, -0.005, 0.0, 0.005, 0.01):
            try:
                row.append(round(dcf(m["ttm_revenue"], m["fcf_margin"], b["revenue_cagr"], b["terminal_margin"],
                                     b["wacc"] + dw, b["terminal_growth"] + dg, m["net_cash"],
                                     m["diluted_shares"])["value_per_share"], 2))
            except ValueError:
                row.append(None)
        grid.append(row)
    out["sensitivity"] = {"rows_wacc": [b["wacc"] + d for d in (-0.01, -0.005, 0, 0.005, 0.01)],
                          "cols_terminal_growth": [b["terminal_growth"] + d for d in (-0.01, -0.005, 0, 0.005, 0.01)],
                          "value_per_share": grid}
    return out


def peers(view: PointInTimeView, symbol: str, universe: list[str]) -> dict:
    rows = {}
    for s in universe:
        try:
            mm = metrics(view, s)
            rows[s] = {"pe_ttm": round(mm["pe_ttm"], 2), "ev_sales": round(mm["ev_sales"], 2),
                       "fcf_yield": round(mm["fcf_yield"], 4), "ttm_revenue_growth": mm["ttm_revenue_growth"]}
        except Exception as exc:  # a peer without data is reported, not invented
            rows[s] = {"error": str(exc)}
    return rows


def valuation_stance(upside_base: float, upside_bear: float) -> Stance:
    if upside_base > 0.15:
        return Stance.BULLISH
    if upside_base < -0.15:
        return Stance.BEARISH
    return Stance.NEUTRAL


# ------------------------------------------------------------------ agent

def merlin_system_prompt() -> str:
    return (AGENT_DIR / "merlin.md").read_text()


def build_inputs(view: PointInTimeView, symbol: str, universe: list[str], research_date: date,
                 overrides: dict | None = None) -> dict:
    m = metrics(view, symbol)
    a = default_assumptions(m)
    for scen, vals in (overrides or {}).items():
        a[scen].update(vals)
    val = valuation(m, a)
    return {
        "instrument": symbol, "profile": view.profile(symbol), "research_date": research_date.isoformat(),
        "evidence_cutoff": view.cutoff.isoformat(), "data_source": view.source, "metrics": m,
        "assumptions": a, "valuation": val, "peers": peers(view, symbol, universe),
        "filings_used": [{k: f[k] for k in ("period_end", "filed")} for f in view.filings(symbol)[-8:]],
        "valuation_implied_stance": valuation_stance(val["base"]["upside_pct"], val["bear"]["upside_pct"]).value,
        "assumption_overrides": overrides or {},
    }


def run_merlin(view: PointInTimeView, symbol: str, universe: list[str], research_date: date, rt: Runtime,
               overrides: dict | None = None) -> MerlinReport:
    inputs = build_inputs(view, symbol, universe, research_date, overrides)
    payload = json.dumps(inputs, indent=1, default=str)
    prompt = ("Research assignment for Merlin.\n\nVERIFIED INPUTS (the only numbers you may use):\n"
              f"```json\n{payload}\n```\n\nReturn the JSON object described in your instructions.")
    if getattr(rt, "plugin_dirs", None):
        prompt = ("FIRST, load each of these Anthropic financial-services skills with the Skill tool, one call each, "
                  "and follow their method: " + ", ".join(FSI_SKILLS) + ". Then answer.\n\n" + prompt)
    before = rt.meter.calls
    r = rt.complete(merlin_system_prompt(), prompt)
    out = extract_json(r.text)
    skills = sorted({t["input"].get("skill") or t["input"].get("command") or "?" for t in r.tools_used
                     if t["name"] == "Skill" and isinstance(t.get("input"), dict)})

    m, val = inputs["metrics"], inputs["valuation"]
    scen = [Scenario(name=n, revenue_cagr=val[n]["revenue_cagr"], terminal_margin=val[n]["terminal_margin"],
                     wacc=val[n]["wacc"], terminal_growth=val[n]["terminal_growth"],
                     value_per_share=round(val[n]["value_per_share"], 2), upside_pct=round(val[n]["upside_pct"], 4))
            for n in ("bull", "base", "bear")]
    synthetic = "SYNTHETIC" in view.source.upper()
    sources = [Source(name=view.source, kind="synthetic_fixture" if synthetic else "market_data",
                      as_of=view.cutoff.isoformat(), reference="daily bars, filings (by filing date)"),
               Source(name="merlin_quant.merlin calc engine", kind="computed", as_of=view.cutoff.isoformat(),
                      reference="metrics(), dcf(), valuation(), peers()")]
    sources += [Source(name=f"FSI skill {s}", kind="plugin_skill", as_of=research_date.isoformat(),
                       reference=f"anthropics/financial-services@{FSI_COMMIT[:7]}") for s in skills]
    unknowns = list(out.get("unknowns", []))
    if synthetic:
        unknowns.insert(0, "All inputs are SYNTHETIC test fixtures, not real company data.")
    fm = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in m.items()
          if isinstance(v, (int, float)) or v is None}
    fm["valuation_implied_upside_base"] = round(val["base"]["upside_pct"], 4)
    fm["pv_terminal_share_base"] = round(val["base"]["pv_terminal_share"], 4)
    rid = stable_id("merlin", {"inputs": inputs, "runtime": rt.name})
    return MerlinReport(
        report_id=rid, instrument=symbol, market=inputs["profile"].get("exchange", "US"),
        research_date=research_date, evidence_cutoff=view.cutoff, business_overview=out["business_overview"],
        financial_metrics=fm,
        valuation_assumptions={f"{n}_{k}": v for n in ("bull", "base", "bear")
                               for k, v in inputs["assumptions"][n].items()},
        scenarios=scen, reference_price=m["price"], reference_price_date=date.fromisoformat(m["price_date"]),
        fair_value_base=round(val["base"]["value_per_share"], 2), thesis=out["thesis"],
        catalysts=list(out.get("catalysts", [])), material_risks=list(out.get("material_risks", [])),
        sources=sources, unknowns=unknowns, research_implications=out["research_implications"],
        stance=Stance(out["stance"]), conviction=float(out["conviction"]), fsi_skills_used=skills,
        run=RunMeta(runtime=rt.name, models=sorted(rt.meter.models), model_calls=rt.meter.calls - before,
                    failed_calls=rt.meter.failed, input_tokens=r.input_tokens, output_tokens=r.output_tokens,
                    cost_usd=r.cost_usd, wall_seconds=round(r.seconds, 2), context_id=r.context_id,
                    inputs_digest=hashlib.sha256(payload.encode()).hexdigest(),
                    notes=["numbers computed by merlin_quant.merlin; narrative by the runtime"]),
    )


def scripted_merlin(system: str, prompt: str) -> str:
    """TEST DOUBLE for Merlin's narrative: restates the computed valuation, adds nothing."""
    inp = extract_json(prompt)
    v, m = inp["valuation"], inp["metrics"]
    stance = inp["valuation_implied_stance"]
    up = v["base"]["upside_pct"]
    return json.dumps({
        "business_overview": f"[scripted] {inp['profile'].get('name', inp['instrument'])}, "
                             f"{inp['profile'].get('sector', '')}. TTM revenue {m['ttm_revenue']:.1f}bn.",
        "thesis": f"[scripted] Base-case DCF value {v['base']['value_per_share']:.2f} vs price {m['price']:.2f} "
                  f"({up:+.1%}); bear {v['bear']['upside_pct']:+.1%}, bull {v['bull']['upside_pct']:+.1%}.",
        "catalysts": ["[scripted] next quarterly filing"],
        "material_risks": ["[scripted] growth below base-case CAGR",
                           f"[scripted] terminal value is {v['base']['pv_terminal_share']:.0%} of EV"],
        "unknowns": ["[scripted] no management guidance in the inputs"],
        "research_implications": f"[scripted] Valuation-implied stance {stance}.",
        "stance": stance, "conviction": 0.7 if stance != "NEUTRAL" else 0.5,
    })
