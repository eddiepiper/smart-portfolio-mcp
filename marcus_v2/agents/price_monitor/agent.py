"""Price Monitor agent (rebuilt in the Claude cloud).

Watches an owner-approved watchlist of trigger rules and emits MarketEvents for
Workflow A. Rules are evaluated only on Data-Guardian-accepted FINAL daily bars:
close rules on the official close, touch rules (above/below/stop/target) on the
bar's high/low. Event ids are deterministic (`pm:<rule>:<bar date>`), so a rule
firing on the same bar can never create a second decision. Changing the
watchlist needs the owner's approval; the agent itself only reads it.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ...core.contracts import PriceType, QualityStatus, new_id, utcnow
from ..base import Agent

RULE_TYPES = {"above", "below", "daily_close_above", "daily_close_below", "stop", "target", "pct_move"}


class WatchlistError(Exception):
    pass


class PriceMonitor(Agent):
    agent_id = "price_monitor"

    def __init__(self, rt, mi, dg, clock=utcnow):
        super().__init__(rt)
        self.mi, self.dg, self.clock = mi, dg, clock
        self.dir = rt.home / "price_monitor"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.rules_path = self.dir / "rules.json"

    def register(self):
        self.tool("scan", self.scan, "read_market_data", timeout_s=300)
        self.tool("list_rules", self.list_rules, "read_market_data")
        return self

    # --------------------------------------------------------------- watchlist (owner-controlled)
    def list_rules(self) -> list[dict]:
        return json.loads(self.rules_path.read_text())["rules"] if self.rules_path.exists() else []

    def _save(self, rules: list[dict]) -> None:
        tmp = self.rules_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"rules": rules, "updated_at": utcnow().isoformat()}, indent=2))
        tmp.replace(self.rules_path)

    def add_rule(self, rule: dict, approver: str) -> dict:
        if not approver.startswith("owner:"):
            raise WatchlistError("watchlist changes need the owner's approval")
        if rule.get("type") not in RULE_TYPES:
            raise WatchlistError(f"rule type must be one of {sorted(RULE_TYPES)}")
        self.mi.get_instrument_metadata(rule["instrument_id"])          # unknown instruments refused
        if rule["type"] != "pct_move" and "level" not in rule:
            raise WatchlistError("rule needs a level")
        rules = self.list_rules()
        rid = rule.get("rule_id") or f"{rule['instrument_id']}:{rule['type']}:{rule.get('level', rule.get('pct'))}"
        if any(r["rule_id"] == rid for r in rules):
            return next(r for r in rules if r["rule_id"] == rid)
        r = {"rule_id": rid, "active": True, "approved_by": approver, "added_at": utcnow().isoformat(), **rule}
        rules.append(r)
        self._save(rules)
        return r

    def deactivate_rule(self, rule_id: str, approver: str) -> None:
        if not approver.startswith("owner:"):
            raise WatchlistError("watchlist changes need the owner's approval")
        rules = self.list_rules()
        for r in rules:
            if r["rule_id"] == rule_id:
                r["active"] = False
                r["deactivated_by"] = approver
        self._save(rules)

    def import_trader_alerts(self, alerts_json: str | Path, approver: str) -> list[dict]:
        """Bring the day-trader skill's alert rules (trader/alerts.json) under Price Monitor."""
        d = json.loads(Path(alerts_json).read_text())
        mapping = {"stop": "daily_close_below", "target": "target", "above": "above", "below": "below"}
        out = []
        for a in d.get("rules", []):
            t = mapping.get(a.get("type"))
            if not t or "price" not in a:
                continue
            out.append(self.add_rule({"instrument_id": f"US:{a['symbol']}", "type": t, "level": str(a["price"]),
                                      "note": a.get("note", ""), "source": f"trader-alerts:{a.get('id')}"}, approver))
        return out

    # ------------------------------------------------------------------------ scan
    def scan(self, as_of: Optional[date] = None) -> dict:
        now = self.clock()
        as_of = as_of or now.date()
        rules = [r for r in self.list_rules() if r.get("active")]
        events, problems, checked = [], [], []
        by_inst: dict[str, list[dict]] = {}
        for r in rules:
            by_inst.setdefault(r["instrument_id"], []).append(r)
        for iid, rs in by_inst.items():
            rep = self.mi.get_historical_prices(iid, as_of - timedelta(days=10), as_of, requesting_agent="price_monitor")
            dq = self.dg.validate_report(rep, purpose="decision")
            final = [o for o in rep.observations if o.validation_status != QualityStatus.REJECTED
                     and o.price_type in (PriceType.OFFICIAL_CLOSE, PriceType.HISTORICAL)
                     and None not in (o.high, o.low, o.close)]
            if dq.status == QualityStatus.REJECTED or not final:
                problems.append({"instrument_id": iid, "data_quality": dq.status.value, "dq_report": dq.report_id,
                                 "detail": "no validated final bar: rules not evaluated (no event emitted)"})
                continue
            final.sort(key=lambda o: o.bar_date)
            bar, prev = final[-1], (final[-2] if len(final) > 1 else None)
            checked.append({"instrument_id": iid, "bar_date": str(bar.bar_date), "close": str(bar.close), "dq_report": dq.report_id})
            for r in rs:
                hit, detail = self._evaluate(r, bar, prev)
                if hit:
                    events.append({"event_id": f"pm:{r['rule_id']}:{bar.bar_date}", "instrument_id": iid,
                                   "type": "trigger_hit", "rule": r["rule_id"], "rule_type": r["type"],
                                   "level": r.get("level"), "bar_date": str(bar.bar_date), "close": str(bar.close),
                                   "high": str(bar.high), "low": str(bar.low), "detail": detail, "note": r.get("note", ""),
                                   "dq_report": dq.report_id,
                                   "occurred_at": datetime.combine(bar.bar_date, datetime.min.time(), timezone.utc).replace(hour=21).isoformat()})
        out = {"scan_id": new_id("pms"), "as_of": str(as_of), "rules_active": len(rules), "checked": checked,
               "events": events, "problems": problems}
        self.rt.evidence.put(out, "price_monitor_scan", obj_id=out["scan_id"])
        return out

    @staticmethod
    def _evaluate(r: dict, bar, prev) -> tuple[bool, str]:
        t = r["type"]
        if t == "pct_move":
            if prev is None:
                return False, ""
            mv = (bar.close / prev.close - 1) * 100
            lim = Decimal(str(r["pct"]))
            return (abs(mv) >= lim, f"moved {mv:.2f}% (threshold {lim}%)")
        lv = Decimal(str(r["level"]))
        if t == "daily_close_above":
            return bar.close > lv, f"close {bar.close} > {lv}"
        if t == "daily_close_below":
            return bar.close < lv, f"close {bar.close} < {lv}"
        if t in ("above", "target"):
            return bar.high >= lv, f"high {bar.high} >= {lv}"
        if t in ("below", "stop"):
            return bar.low <= lv, f"low {bar.low} <= {lv}"
        return False, ""
