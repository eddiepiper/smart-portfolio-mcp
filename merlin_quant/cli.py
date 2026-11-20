"""python -m merlin_quant <command> — the Merlin + Quant paper-trading CLI. PAPER ONLY.

State lives in --home (default $MERLIN_QUANT_HOME or ./var/merlin_quant, git-ignored).
Dates: --cutoff is the completed NYSE session whose close the research uses;
--now is the evaluation time (default: real now). Times print in ET and SGT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from merlin_quant.calendar import NY, fmt_times, last_completed_session
from merlin_quant.config import load_config
from merlin_quant.contracts import Mode
from merlin_quant.data import MarketData, synthetic_fixture
from merlin_quant.engine import EngineError

DEFAULT_HOME = os.environ.get("MERLIN_QUANT_HOME", "var/merlin_quant")
EPHEMERAL_PREFIXES = ("/tmp/", "/var/tmp/", "/private/tmp/")


def durability_warning(home: str | Path) -> str | None:
    """A warning when the ledger would live somewhere that does not survive the session."""
    p = str(Path(home).expanduser().resolve()) + "/"
    if p.startswith(EPHEMERAL_PREFIXES):
        return f"{home} is a temporary folder: the paper ledger will be lost. Set MERLIN_QUANT_HOME to a durable folder."
    if os.environ.get("CLAUDE_CODE_REMOTE") == "true":
        return (f"this is a Claude cloud container, which is deleted when the session ends: the ledger at {home} "
                "will be lost. Run the paper portfolio on your own machine (MERLIN_QUANT_HOME=~/merlin-quant-paper).")
    return None


def _config_path(args):
    """--config, else an owner config kept next to the ledger ($HOME/config.json), else the EXAMPLE config."""
    if args.config:
        return args.config
    owner = Path(args.home) / "config.json"
    return owner if owner.exists() else None


def _md(args) -> MarketData:
    if args.data == "synthetic":
        return synthetic_fixture()
    return MarketData.load(Path(args.data))


def _team(args):
    from merlin_quant.workflow import Team  # heavy import only when needed
    cfg = load_config(_config_path(args))
    return Team(args.home, cfg, _md(args), runtime_override=args.runtime)


def _now(args) -> datetime:
    return datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)


def _cutoff(args) -> date:
    return date.fromisoformat(args.cutoff) if args.cutoff else last_completed_session(_now(args))


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="merlin_quant", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=DEFAULT_HOME)
    ap.add_argument("--config", default=None,
                    help="portfolio config JSON (default: HOME/config.json if present, else the EXAMPLE config)")
    ap.add_argument("--data", default="synthetic", help="'synthetic' or a MarketData JSON file")
    ap.add_argument("--runtime", choices=["scripted", "claude_cli", "anthropic_api"], default=None,
                    help="override every lead's LLM runtime")
    ap.add_argument("--now", default=None, help="evaluation time, ISO 8601 with offset")
    ap.add_argument("--cutoff", default=None, help="data cutoff session YYYY-MM-DD")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the paper portfolio (refuses to reset an existing one)")
    for name in ("research", "merlin", "quant", "propose"):
        sp = sub.add_parser(name)
        sp.add_argument("symbol")
    sp = sub.add_parser("approve"); sp.add_argument("proposal_id"); sp.add_argument("--by", required=True)
    sp = sub.add_parser("reject"); sp.add_argument("proposal_id"); sp.add_argument("--by", required=True)
    sp.add_argument("--reason", required=True)
    sp = sub.add_parser("execute"); sp.add_argument("proposal_id")
    sp = sub.add_parser("portfolio"); sp.add_argument("--as-of", default=None)
    sub.add_parser("history")
    sp = sub.add_parser("backtest"); sp.add_argument("--start", default="2025-11-03"); sp.add_argument("--end", default="2026-11-13")
    sp.add_argument("--every", type=int, default=5)
    sp = sub.add_parser("performance"); sp.add_argument("--horizon", type=int, default=5)
    sp = sub.add_parser("halt"); sp.add_argument("--reason", required=True)
    sp = sub.add_parser("resume"); sp.add_argument("--by", required=True)
    sp = sub.add_parser("mode"); sp.add_argument("mode", choices=[m.value for m in Mode])
    sp.add_argument("--validation-passed", action="store_true")
    sub.add_parser("verify", help="verify the ledger hash chain and reconcile it with the broker book")
    sp = sub.add_parser("fetch-data", help="build a MarketData file from Yahoo + SEC EDGAR (needs internet access)")
    sp.add_argument("symbols", nargs="*", help="default: the config universe")
    sp.add_argument("--out", required=True, help="MarketData JSON to write (existing headlines are kept)")
    sp.add_argument("--start", default="2025-01-02", help="first session (calendar covers 2025-2027)")
    sp.add_argument("--end", default=None)
    sp.add_argument("--sec-user-agent", default=os.environ.get("SEC_USER_AGENT"),
                    help="'Your Name you@example.com' (SEC requires it; or set SEC_USER_AGENT)")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "fetch-data":
            from merlin_quant.live_data import Http, build_market_data
            cfg = load_config(_config_path(args))
            syms = [x.upper() for x in args.symbols] or list(cfg.portfolio.universe)
            missing = [x for x in syms if x not in cfg.portfolio.universe]
            if missing:
                print(f"NOTE: {', '.join(missing)} not in the config universe; add them (and their sectors) to "
                      "trade them.", file=sys.stderr)
            out = Path(args.out).expanduser()
            prev = MarketData.load(out) if out.exists() else None
            md = build_market_data(syms, cfg.portfolio.benchmark, date.fromisoformat(args.start),
                                   date.fromisoformat(args.end) if args.end else None,
                                   http=Http(args.sec_user_agent), sectors=cfg.portfolio.sectors, previous=prev,
                                   log=lambda m: print(m, file=sys.stderr))
            out.parent.mkdir(parents=True, exist_ok=True)
            md.save(out)
            last = min(b[-1]["date"] for b in md.bars.values())
            print(f"wrote {out} ({md.source_name}); downloaded {fmt_times(datetime.fromisoformat(md.fetched_at))}; "
                  f"last complete session {last}. Use: --data {out} --cutoff {last}")
            return 0
        if args.cmd == "backtest":
            from merlin_quant.backtest import run_backtest
            res = run_backtest(_md(args), load_config(_config_path(args)), date.fromisoformat(args.start),
                               date.fromisoformat(args.end), args.every)
            out = Path(args.home) / "backtests" / f"backtest-{args.start}-{args.end}-{args.every}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(res, indent=1, default=str))
            _print({k: res[k] for k in ("kind", "data_source", "metrics", "trade_stats", "limitations")} |
                   {"trades": len(res["trades"]), "saved": str(out)})
            return 0

        team = _team(args)
        eng = team.engine
        if args.cmd == "init":
            warn = durability_warning(args.home)
            if warn:
                print("WARNING:", warn, file=sys.stderr)
            eng.init()
            print(f"initialised paper portfolio at {eng.home} ({team.cfg.portfolio.initial_capital:,.0f} "
                  f"{team.cfg.portfolio.base_currency}, mode {team.cfg.mode.value}, config {team.cfg.version}, "
                  f"status {team.cfg.config_status})")
            return 0
        eng.open()
        for note in eng.recovery_notes:
            print("RECOVERY:", note)
        if args.cmd in ("merlin", "quant"):
            m, q = team.stage_a(args.symbol.upper(), _cutoff(args), only=args.cmd)
            _print((m or q).model_dump(mode="json"))
        elif args.cmd in ("research", "propose"):
            m, q, c, j, p = team.research(args.symbol.upper(), _cutoff(args), now=_now(args))
            _print({"merlin": [m.report_id, m.stance.value], "quant": [q.report_id, q.stance.value,
                    q.tradingagents_rating], "challenge": [c.report_id, c.verdict.value],
                    "disagreements": j.disagreements, "proposal": p.model_dump(mode="json", include={
                        "proposal_id", "action", "quantity", "reference_price", "target_weight", "status",
                        "eligibility", "estimated_costs"}),
                    "cost": {"merlin_usd": m.run.cost_usd, "quant_usd": q.run.cost_usd, "challenge_usd": c.run.cost_usd,
                             "model_calls": m.run.model_calls + q.run.model_calls + c.run.model_calls}})
        elif args.cmd == "approve":
            p = team.approve(args.proposal_id, args.by)
            out = p.model_dump(mode="json", include={"proposal_id", "status", "action", "quantity", "instrument"})
            # Fees at TODAY's schedule, so an approval of an older proposal (made before fees were modelled) still
            # shows what it will cost; the risk gate re-checks with the same schedule at execution.
            out["estimated_costs"] = team.cfg.costs.fee_estimate(p.quantity, p.reference_price)
            out["fee_schedule"] = team.engine.costs.describe()
            _print(out)
        elif args.cmd == "reject":
            _print(team.reject(args.proposal_id, args.by, args.reason).model_dump(mode="json", include={"proposal_id", "status"}))
        elif args.cmd == "execute":
            rec, r = team.execute(args.proposal_id, now=_now(args))
            _print({"risk": r.model_dump(mode="json") if r else None,
                    "execution": rec.model_dump(mode="json") if rec else None})
        elif args.cmd == "portfolio":
            as_of = date.fromisoformat(args.as_of) if args.as_of else _cutoff(args)
            eng.apply_corporate_actions(as_of)
            nav = eng.record_nav(as_of)
            snap = eng.snapshot(as_of)
            eng.records.put("snapshots", f"{as_of}-{snap.ledger_head}", snap)
            _print(snap.model_dump(mode="json"))
        elif args.cmd == "history":
            for e in eng.ledger.events():
                print(f"{e['seq']:>4} {e['ts'][:19]} {e['type']:<16} {json.dumps(e['payload'], default=str)[:160]}")
        elif args.cmd == "performance":
            from merlin_quant.performance import LIMITATIONS, score_research, series_metrics, trade_stats
            st = eng.ledger.state()
            navs = [n["nav"] for n in st.nav_history]
            bench = [n["benchmark_close"] for n in st.nav_history]
            props = [p for p in eng.records.list("proposals") if p["action"] not in ("HOLD",)]
            merl = {r["report_id"]: r for r in eng.records.list("merlin")}
            qnt = {r["report_id"]: r for r in eng.records.list("quant")}
            through = _cutoff(args)
            _print({"kind": "FORWARD_PAPER_TRADING", "nav_points": st.nav_history[-10:],
                    "metrics": series_metrics(navs, bench), "trade_stats": trade_stats(st.closed_trades),
                    "realized_pnl": round(st.realized_pnl, 2), "fees": round(st.fees, 2),
                    "fee_schedule": eng.costs.describe(),
                    "research_scoring": score_research(props, merl, qnt, team.md, team.cfg.portfolio.benchmark,
                                                       args.horizon, through),
                    "limitations": LIMITATIONS})
        elif args.cmd == "halt":
            eng.halt(args.reason); print("HALTED:", args.reason)
        elif args.cmd == "resume":
            eng.resume(args.by); print("RESUMED by", args.by)
        elif args.cmd == "mode":
            eng.set_mode(Mode(args.mode), validation_passed=args.validation_passed); print("mode", args.mode)
        elif args.cmd == "verify":
            evs = eng.ledger.verify()
            eng.reconcile()
            print(f"ledger OK: {len(evs)} events, head {eng.ledger.head()}; broker book reconciles")
        return 0
    except EngineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
