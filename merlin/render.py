"""Render a merlin-research/v1 report as the standard markdown research report."""
from __future__ import annotations

from .contracts import MerlinReport


def _fmt(v, unit):
    if unit == "%":
        return f"{v * 100:.2f}%" if abs(v) <= 5 else f"{v:.2f}%"
    if isinstance(v, float) and abs(v) >= 1000:
        return f"{v:,.0f} {unit}"
    return f"{v:,.4g} {unit}"


def render_markdown(r: MerlinReport) -> str:
    q = r.request
    L = [f"# Merlin research: {q.subject}", "",
         f"*{q.task_type.replace('_', ' ')} · as of {q.as_of} · {r.environment} · report `{r.report_id}` · "
         f"quality **{r.quality_status.value}***", "", f"> {q.question}", "",
         "## Executive summary", "", r.executive_summary, "", "## Key findings", ""]
    for f in sorted(r.key_findings, key=lambda x: x.rank):
        refs = ", ".join(f.figure_ids + f.evidence_ids)
        L.append(f"{f.rank}. **[{f.significance}]** {f.finding}" + (f" ({refs})" if refs else ""))
    if r.figures:
        L += ["", "## Financial evidence", "", "| id | metric | period | value | status | sources | derived by |",
              "|---|---|---|---|---|---|---|"]
        for f in r.figures:
            L.append(f"| {f.figure_id} | {f.metric} | {f.period or '-'} | {_fmt(f.value, f.unit)} | {f.status.value} | "
                     f"{', '.join(f.evidence_ids)} | {f.derived_by or 'reported'} |")
    if r.model:
        m = r.model
        L += ["", f"## Valuation / modelling ({m.type})", ""]
        if m.assumptions:
            L += ["| assumption | value | status | rationale |", "|---|---|---|---|"]
            L += [f"| {a.name} | {a.value} | {a.status} | {a.rationale} |" for a in m.assumptions]
        o = m.outputs
        if o:
            keys = [k for k in ("sum_pv_fcff", "terminal_value", "pv_terminal_value", "enterprise_value",
                                "net_debt", "equity_value", "value_per_share", "tv_share_of_ev",
                                "implied_exit_multiple", "implied_terminal_growth") if o.get(k) is not None]
            L += ["", "| output | value |", "|---|---|"] + [f"| {k} | {o[k]:,.4f} |" for k in keys]
            if o.get("rows"):
                L += ["", "| year | revenue | EBIT | NOPAT | D&A | capex | ΔNWC | FCFF | DF | PV |",
                      "|---|---|---|---|---|---|---|---|---|---|"]
                for x in o["rows"]:
                    L.append(f"| {x['year']} | {x['revenue']:,.1f} | {x['ebit']:,.1f} | {x['nopat']:,.1f} | {x['da']:,.1f} | "
                             f"{x['capex']:,.1f} | {x['change_nwc']:,.1f} | {x['fcff']:,.1f} | {x['discount_factor']:.4f} | {x['pv_fcff']:,.1f} |")
            for w in o.get("warnings", []):
                L.append(f"- ⚠ {w}")
        s = m.sensitivity
        if s:
            L += ["", f"Sensitivity of {s['output']} ({s['row_key']} down, {s['col_key']} across); centre = base case "
                      f"({'checked' if s.get('centre_equals_base') else 'NOT the base case'})", "",
                  "| | " + " | ".join(str(c) for c in s["cols"]) + " |", "|---|" + "---|" * len(s["cols"])]
            for rv, line in zip(s["rows"], s["grid"]):
                L.append(f"| {rv} | " + " | ".join("n/a" if v is None else f"{v:,.2f}" for v in line) + " |")
        if m.limitations:
            L += ["", "Model limitations:"] + [f"- {x}" for x in m.limitations]
    if r.scenarios:
        L += ["", "## Bull, base and bear", "", "| case | value | key assumptions | narrative |", "|---|---|---|---|"]
        for s in r.scenarios:
            val = "-" if s.value is None else f"{s.value:,.2f} {s.unit or ''}"
            L.append(f"| {s.name} | {val} | {s.key_assumptions} | {s.narrative} |")
    L += ["", "## Risks and counterarguments", ""]
    L += [f"- {x.risk}" + (f" ({', '.join(x.evidence_ids)})" if x.evidence_ids else "") for x in r.risks]
    L += [f"- Counterargument: {c}" for c in r.counterarguments]
    L += ["", "## Unknowns and uncertainty", ""] + [f"- {u}" for u in r.unknowns]
    L += ["", "## Research implications", ""] + [f"- **{i.audience}**: {i.text}" for i in r.implications]
    L += ["", "## Evidence register", "", "| id | status | kind | title | publisher | document date | period | retrieved | url |",
          "|---|---|---|---|---|---|---|---|---|"]
    for e in r.evidence:
        L.append(f"| {e.evidence_id} | {e.status.value} | {e.kind} | {e.title} | {e.publisher} | {e.document_date or '-'} | "
                 f"{e.period or '-'} | {e.retrieved_at.isoformat(timespec='minutes') if e.retrieved_at else '-'} | {e.url or '-'} |")
    L += ["", "## Quality status", "", f"**{r.quality_status.value}**. {r.quality_notes}"]
    if r.limitations:
        L += ["", "Limitations:"] + [f"- {x}" for x in r.limitations]
    L += ["", "## Capabilities used", "", "| capability | kind | genuine execution | detail |", "|---|---|---|---|"]
    L += [f"| {c.name} | {c.kind} | {'yes' if c.genuine_execution else 'no'} | {c.detail} |" for c in r.capabilities_used]
    L += ["", "---", "*Research only. Merlin does not trade, rate or make binding portfolio decisions; "
                 "Marcus integrates the evidence and Quant challenges it.*", ""]
    return "\n".join(L)
