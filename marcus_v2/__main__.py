"""JSON in / JSON out entry point for Marcus V2 (the integration surface Claude drives via the marcus-v2 skill).

    python -m marcus_v2 manifest
    python -m marcus_v2 telemetry
    python -m marcus_v2 status
    python -m marcus_v2 daily_monitoring   --payload '{"events": [...], "watchlist": ["US:BAC"]}'
    python -m marcus_v2 new_opportunity    --payload '{"instrument_id": "US:PFE", "as_of": "2026-11-09", "plan": {...}}'
    python -m marcus_v2 portfolio_review   --payload '{"instruments": ["US:BAC"], "start": "2026-10-01"}'
    # owner-only actions require --owner owner:<name>
    python -m marcus_v2 mandate_propose    --owner owner:edward
    python -m marcus_v2 mandate_approve    --owner owner:edward --payload '{"version": 1}'
    python -m marcus_v2 import_legacy      --owner owner:edward --payload '{"trader_dir": "trader"}'
    python -m marcus_v2 plan_topup         --payload '{"target": "20000", "currency": "SGD", "stated_original": ["500", "SGD"]}'
    python -m marcus_v2 approve            --owner owner:edward --payload '{"recommendation_id": "rec_..."}'

There is no command that trades real money, and none that takes broker credentials.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .core import capital
from .core.mandate import default_v2_mandate
from .core.runtime import build_runtime
from .integrations.existing import tool_manifest


def _out(obj) -> None:
    print(json.dumps(obj, default=lambda o: o.model_dump(mode="json") if hasattr(o, "model_dump") else str(o), indent=2))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="marcus_v2")
    ap.add_argument("command")
    ap.add_argument("--payload", default="{}")
    ap.add_argument("--owner")
    ap.add_argument("--home")
    a = ap.parse_args(argv)
    p = json.loads(a.payload)
    from .core import state_sync
    from .core.runtime import DEFAULT_HOME
    home = Path(a.home) if a.home else DEFAULT_HOME
    sync = state_sync.configured()
    if sync and a.command not in ("manifest",):
        state_sync.pull(home, *sync)                         # fresh cloud session: restore state first
    rt = build_runtime(home)
    code = _run(a, p, rt)
    if sync and code == 0 and a.command in WRITE_COMMANDS and not p.get("dry_run"):   # previews never push
        res = state_sync.push(home, f"marcus_v2 {a.command} ({datetime.now(ZoneInfo('Asia/Singapore')):%Y-%m-%d %H:%M} SGT)", sync[1])
        print(json.dumps({"state_sync": res.__dict__}), file=sys.stderr)
    return code


WRITE_COMMANDS = {"mandate_propose", "mandate_approve", "import_legacy", "apply_topup", "approve", "daily_run",
                  "weekly_review", "watch_add", "watch_import", "watch_deactivate", "new_opportunity",
                  "daily_monitoring", "portfolio_review", "state_push"}


def _run(a, p, rt) -> int:
    owner_only = {"mandate_propose", "mandate_approve", "import_legacy", "approve", "apply_topup",
                  "watch_add", "watch_import", "watch_deactivate"}
    dry_run = bool(p.get("dry_run"))                     # previews write nothing to the ledger
    if a.command in owner_only and not dry_run and not (a.owner or "").startswith("owner:"):
        _out({"error": f"{a.command} needs --owner owner:<name> (a human approval)"})
        return 2
    if a.command == "manifest":
        _out(tool_manifest()); return 0
    if a.command == "telemetry":
        _out(rt.audit.telemetry()); return 0
    if a.command == "mandate_propose":
        _out(rt.mandates.propose(default_v2_mandate(p.get("version", 1)))); return 0
    if a.command == "mandate_approve":
        _out(rt.mandates.approve("marcus-paper", p["version"], a.owner)); return 0
    if a.command == "import_legacy":
        ccy = p.get("capital_currency", "SGD")     # owner decision 2026-11-10: original capital was S$500
        fx = None
        if ccy != "USD":
            from .marcus.team import build_team
            fx = build_team(rt).fx_rates(date(2026, 10, 1), date.today())
        events, notes = capital.plan_legacy_import(p.get("trader_dir", "trader"), rt.portfolio_id, capital_currency=ccy,
                                                   fx_rates=fx["rates"] if fx else None,
                                                   fx_source=f"{fx['source']} validated ({fx['dq_report']})" if fx else "")
        if p.get("dry_run", False):
            _out({"events": events, "notes": notes}); return 0
        _out({"written": capital.apply_events(rt.ledger_writer(), events, a.owner), "notes": notes}); return 0
    if a.command in ("plan_topup", "apply_topup"):
        orig = p.get("stated_original")
        plan = capital.plan_top_up(rt.ledger_writer(), rt.portfolio_id, Decimal(p["target"]), p["currency"],
                                   datetime.now(ZoneInfo("Asia/Singapore")),
                                   owner_stated_original=(Decimal(orig[0]), orig[1]) if orig else None)
        if a.command == "apply_topup":
            _out({"written": capital.apply_plan(rt.ledger_writer(), plan, a.owner)}); return 0
        _out(plan.summary()); return 0
    from .marcus.team import build_team
    team = build_team(rt)
    if a.command == "status":
        led = rt.ledger_reader()
        _out({"ledger_events": len(led.events()), "ledger_integrity": led.verify() or "ok",
              "tradingagents": team.ta.available(), "telemetry": rt.audit.telemetry()}); return 0
    if a.command == "daily_monitoring":
        _out(team.workflow_a_daily_monitoring(p["events"], set(p.get("watchlist", [])))); return 0
    if a.command == "new_opportunity":
        _out(team.workflow_b_new_opportunity(p["instrument_id"], date.fromisoformat(p["as_of"]), plan=p.get("plan"),
                                             strategy=p.get("strategy"))); return 0
    if a.command == "portfolio_review":
        end = date.fromisoformat(p.get("end", date.today().isoformat()))
        start = date.fromisoformat(p.get("start", (end - timedelta(days=60)).isoformat()))
        obs = {}
        for iid in p.get("instruments", []):
            r = team.mi.get_historical_prices(iid, start, end, requesting_agent="portfolio_analyst")
            team.dg.validate_report(r, purpose="research")
            obs[iid] = r.observations
        base = p.get("base_currency", "SGD")
        fx = {"USD": team.fx_rates(start - timedelta(days=30), end)["series"]} if base == "SGD" else None
        _out(team.workflow_c_portfolio_review(obs, base_currency=base, fx=fx)); return 0
    if a.command == "approve":
        _out(team.approve_recommendation(p["recommendation_id"], a.owner)); return 0
    from .agents.price_monitor.agent import PriceMonitor
    pm = PriceMonitor(rt, team.mi, team.dg).register()
    if a.command == "watch_list":
        _out(pm.list_rules()); return 0
    if a.command == "watch_add":
        _out(pm.add_rule(p, a.owner)); return 0
    if a.command == "watch_deactivate":
        pm.deactivate_rule(p["rule_id"], a.owner); _out({"deactivated": p["rule_id"]}); return 0
    if a.command == "watch_import":
        _out(pm.import_trader_alerts(p.get("alerts_json", "trader/alerts.json"), a.owner)); return 0
    from .marcus import runner
    if a.command == "daily_run":
        r = runner.daily_run(team, pm, date.fromisoformat(p["as_of"]) if p.get("as_of") else None)
        _out({"report_path": r["report_path"], "markdown": runner.render_markdown(r)}); return 0
    if a.command == "weekly_review":
        r = runner.weekly_review(team, date.fromisoformat(p["as_of"]) if p.get("as_of") else None)
        _out({"report_path": r["report_path"], "markdown": runner.render_markdown(r)}); return 0
    if a.command == "state_push":
        _out({"note": "state is pushed after this command when MARCUS_V2_STATE_REPO is set"}); return 0
    _out({"error": f"unknown command {a.command}"})
    return 2


def safe_main(argv=None) -> int:
    try:
        return main(argv)
    except Exception as exc:  # noqa: BLE001 - tool callers get a structured error, never a traceback
        _out({"error": type(exc).__name__, "detail": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(safe_main())
