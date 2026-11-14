"""Merlin CLI: JSON in / JSON out. Shared by Claude Cloud (the `merlin` skill) and Grokbot.

    python -m merlin schema                                   # merlin-research/v1 JSON Schema
    python -m merlin calc dcf --inputs dcf_inputs.json        # deterministic DCF (+ --sensitivity / --scenarios)
    python -m merlin calc ratios --inputs items.json
    python -m merlin calc compare --inputs '{"current": {...}, "prior": {...}, "keys": [...]}'
    python -m merlin calc tieout --inputs '{"revenue":..,"opex":..,"operating_income":..,"reported_gross_margin":..}'
    python -m merlin calc comps --inputs '{"NVDA": 30.1, "AMD": 25.0}'
    python -m merlin validate report.json                     # PASS / WARN / FAIL (exit 0 / 0 / 3)
    python -m merlin render report.json [--out report.md]
    python -m merlin reproduce report.json                    # re-run the stored DCF
    python -m merlin handoff report.json --to quant           # Quant challenge packet
    python -m merlin handoff report.json --to marcus [--marcus-home DIR]   # store in Marcus evidence
    python -m merlin run --task earnings_review --subject NVDA --question "..." --out runs/x   # headless (Grokbot)

No command trades, rates a security, or touches Marcus's mandate or ledger.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import calc
from .contracts import MerlinReport, json_schema
from .render import render_markdown
from .validate import validate


def _load(x: str):
    """JSON text or a path to a JSON file. Inline JSON is tried first: a long JSON string is not a
    valid file name (OSError 'File name too long'), found in testing."""
    s = x.strip()
    if s[:1] in "{[":
        return json.loads(s)
    return json.loads(Path(x).read_text())


def _out(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="merlin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("schema")
    c = sub.add_parser("calc")
    c.add_argument("kind", choices=["dcf", "ratios", "compare", "tieout", "comps", "wacc", "cagr", "reverse_dcf"])
    c.add_argument("--inputs", required=True, help="JSON string or path")
    c.add_argument("--sensitivity", help='JSON: {"row_key":..,"rows":[..],"col_key":..,"cols":[..]}')
    c.add_argument("--scenarios", help='JSON: {"bull": {"projection": {...}}, "bear": {...}}')
    for name in ("validate", "render", "reproduce"):
        s = sub.add_parser(name)
        s.add_argument("report")
        if name == "render":
            s.add_argument("--out")
    h = sub.add_parser("handoff")
    h.add_argument("report")
    h.add_argument("--to", choices=["marcus", "quant"], required=True)
    h.add_argument("--marcus-home")
    r = sub.add_parser("run")
    r.add_argument("--request", help="request JSON (string or path); else use the flags below")
    r.add_argument("--task", default="question")
    r.add_argument("--subject")
    r.add_argument("--question")
    r.add_argument("--as-of")
    r.add_argument("--instrument-id")
    r.add_argument("--requested-by", default="grokbot")
    r.add_argument("--context", default="", help="extra context text or path (treated as untrusted data)")
    r.add_argument("--out", required=True)
    r.add_argument("--model")
    r.add_argument("--max-budget-usd", type=float, default=5.0)
    r.add_argument("--mcp-config", help="MCP config with the ONLY servers Merlin may use (licensed data connectors)")
    a = ap.parse_args(argv)

    if a.cmd == "schema":
        _out(json_schema()); return 0
    if a.cmd == "calc":
        x = _load(a.inputs)
        if a.kind == "dcf":
            res = calc.run_dcf(x)
            if a.sensitivity:
                s = _load(a.sensitivity)
                res["sensitivity"] = calc.sensitivity(x, s["row_key"], s["rows"], s["col_key"], s["cols"],
                                                      s.get("output", "value_per_share"))
            if a.scenarios:
                res["scenarios"] = calc.scenarios(x, _load(a.scenarios))
            res["inputs"] = x
        elif a.kind == "ratios":
            res = calc.ratios(x)
        elif a.kind == "compare":
            res = calc.period_compare(x["current"], x["prior"], x["keys"])
        elif a.kind == "tieout":
            res = calc.tie_out(**x)
        elif a.kind == "comps":
            res = calc.comps_stats(x)
        elif a.kind == "wacc":
            ke = x.get("cost_of_equity") or calc.capm(x["risk_free"], x["beta"], x["equity_risk_premium"])
            res = {"cost_of_equity": ke, **calc.wacc(ke, x["pre_tax_cost_of_debt"], x["tax_rate"],
                                                     x["equity_value"], x["debt_value"])}
        elif a.kind == "cagr":
            res = {"cagr": calc.cagr(x["start"], x["end"], x["years"])}
        else:
            res = {"implied_constant_growth": calc.reverse_dcf_growth(x["inputs"], x["target_value_per_share"])}
        _out({"calc_version": calc.CALC_VERSION, "derived_by": f"merlin.calc.{a.kind}", "result": res}); return 0
    if a.cmd == "validate":
        v = validate(_load(a.report)); _out(v)
        return 3 if v["status"] == "FAIL" else 0
    if a.cmd == "render":
        rep = MerlinReport.model_validate(_load(a.report))
        md = render_markdown(rep)
        if a.out:
            Path(a.out).write_text(md); _out({"written": a.out})
        else:
            print(md)
        return 0
    if a.cmd == "reproduce":
        rep = MerlinReport.model_validate(_load(a.report))
        _out(calc.reproduce(rep.model.model_dump() if rep.model else {})); return 0
    if a.cmd == "handoff":
        from . import handoff
        rep = MerlinReport.model_validate(_load(a.report))
        try:
            if a.to == "quant":
                _out(handoff.quant_challenge_packet(rep)); return 0
            if a.marcus_home:
                from marcus_v2.core.runtime import build_runtime
                _out(handoff.ingest_into_marcus(rep, build_runtime(Path(a.marcus_home))))
            else:
                _out(handoff.to_marcus_research_report(rep).model_dump(mode="json"))
            return 0
        except handoff.HandoffRefused as exc:
            _out({"status": "REFUSED", "reason": str(exc)}); return 3
    if a.cmd == "run":
        from . import runner
        if a.request:
            from .contracts import Request
            req = Request.model_validate(_load(a.request))
        else:
            if not (a.subject and a.question):
                _out({"error": "give --request or --subject and --question"}); return 2
            req = runner.request_from_args(a.task, a.subject, a.question, a.as_of, a.requested_by, a.instrument_id)
        ctx = Path(a.context).read_text() if a.context and Path(a.context).exists() else a.context
        res = runner.run(req, Path(a.out), model=a.model, max_budget_usd=a.max_budget_usd, extra_context=ctx,
                         mcp_config=a.mcp_config)
        _out(res)
        return {"PASS": 0, "WARN": 0, "FAIL": 3}.get(res["status"], 4)
    return 2


if __name__ == "__main__":
    sys.exit(main())
