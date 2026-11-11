"""Marcus's scheduled runs in the Claude cloud: daily monitoring and weekly review.

Deterministic: every number comes from the specialists. The report is written
as Markdown (for the owner) and JSON (for Claude / audit) under reports/.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from ..agents.price_monitor.agent import PriceMonitor
from ..core.contracts import utcnow
from .orchestrator import Marcus

SGT, NY = ZoneInfo("Asia/Singapore"), ZoneInfo("America/New_York")


def _pending_approvals(marcus: Marcus) -> list[dict]:
    out = []
    for p in sorted(marcus.rt.evidence.dir.glob("rec_*.json")):
        d = json.loads(p.read_text())["data"]
        if d.get("owner_approval") == "pending":
            out.append({"recommendation_id": d["recommendation_id"], "instrument_id": d["instrument_id"],
                        "action": d["action"], "quantity": d.get("proposed_quantity"), "entry_plan": d.get("entry_plan")})
    return out


def portfolio_snapshot(marcus: Marcus, as_of: date, base_currency: str = "SGD", lookback_days: int = 60) -> Optional[dict]:
    from ..core.ledger import balances
    events = marcus.rt.ledger_reader().events()
    if not events:
        return None
    held = list(balances(events)["positions"])
    start = as_of - timedelta(days=lookback_days)
    obs = {}
    for iid in held:
        r = marcus.mi.get_historical_prices(iid, start, as_of, requesting_agent="portfolio_analyst")
        marcus.dg.validate_report(r, purpose="research")
        obs[iid] = r.observations
    fx = None
    if base_currency == "SGD":
        fx = {"USD": marcus.fx_rates(start - timedelta(days=10), as_of)["series"]}
    rep = marcus.workflow_c_portfolio_review(obs, base_currency=base_currency, fx=fx)
    return rep


def daily_run(marcus: Marcus, pm: PriceMonitor, as_of: Optional[date] = None) -> dict:
    now = utcnow()
    as_of = as_of or now.astimezone(NY).date()
    scan = pm.scan(as_of)
    watch = {r["instrument_id"] for r in pm.list_rules() if r.get("active")}
    wa = marcus.workflow_a_daily_monitoring(scan["events"], watch)
    snap = portfolio_snapshot(marcus, as_of)
    ta_ok, ta_why = marcus.ta.available()
    report = {"kind": "daily", "as_of": str(as_of), "generated_sgt": now.astimezone(SGT).strftime("%Y-%m-%d %H:%M"),
              "generated_ny": now.astimezone(NY).strftime("%Y-%m-%d %H:%M"), "price_monitor": scan,
              "workflow_a": wa, "portfolio": snap, "pending_approvals": _pending_approvals(marcus),
              "limitations": ([] if ta_ok else [f"TradingAgents unavailable: {ta_why} (no new entries without primary research)"])
              + [f"{p['instrument_id']}: {p['detail']}" for p in scan["problems"]]}
    _write(marcus, report)
    return report


def weekly_review(marcus: Marcus, as_of: Optional[date] = None) -> dict:
    now = utcnow()
    as_of = as_of or now.astimezone(NY).date()
    snap = portfolio_snapshot(marcus, as_of, lookback_days=120)
    report = {"kind": "weekly", "as_of": str(as_of), "generated_sgt": now.astimezone(SGT).strftime("%Y-%m-%d %H:%M"),
              "portfolio": snap, "pending_approvals": _pending_approvals(marcus), "telemetry": marcus.rt.audit.telemetry()}
    _write(marcus, report)
    return report


def _write(marcus: Marcus, report: dict) -> None:
    d = marcus.rt.home / "reports"
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{report['kind']}-{report['as_of']}"
    (d / f"{stem}.json").write_text(json.dumps(report, indent=2, default=str))
    (d / f"{stem}.md").write_text(render_markdown(report))
    report["report_path"] = str(d / f"{stem}.md")


def render_markdown(r: dict) -> str:
    L = [f"# Marcus {r['kind']} report — {r['as_of']} (US session)",
         f"_Generated {r['generated_sgt']} SGT. Paper portfolio only; research, not advice._", ""]
    if "price_monitor" in r:
        pm = r["price_monitor"]
        L += ["## Price Monitor", f"{pm['rules_active']} active rules."]
        for c in pm["checked"]:
            L.append(f"- {c['instrument_id']}: close {c['close']} on {c['bar_date']}")
        for e in pm["events"]:
            L.append(f"- **TRIGGER** {e['rule']}: {e['detail']} {('— ' + e['note']) if e.get('note') else ''}")
        for p in pm["problems"]:
            L.append(f"- DATA PROBLEM {p['instrument_id']}: {p['detail']} ({p['data_quality']})")
        if not pm["events"]:
            L.append("- No triggers fired.")
        L.append("")
    s = r.get("portfolio")
    if s:
        L += ["## Portfolio (SGD)", f"- Equity: S${s['equity']}" if s.get("equity") else "- Equity: not computed (see exceptions)",
              f"- Returns: {s.get('returns')}"]
        for x in s.get("exceptions", []):
            L.append(f"- EXCEPTION: {x}")
        for w in s.get("warnings", []):
            L.append(f"- note: {w}")
        for p in s.get("proposals", []):
            L.append(f"- PROPOSAL {p['action']} {p['instrument_id']}: {p['reason']} (owner approval pending)")
        L.append("")
    if r.get("pending_approvals"):
        L += ["## Waiting for your approval"] + [f"- {a['action']} {a['instrument_id']} qty {a['quantity']} ({a['recommendation_id']})" for a in r["pending_approvals"]] + [""]
    if r.get("limitations"):
        L += ["## Limitations"] + [f"- {x}" for x in r["limitations"]] + [""]
    return "\n".join(L)
