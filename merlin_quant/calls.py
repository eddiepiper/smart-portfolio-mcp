"""Pre-registered calls: every call is written down, with a timestamp, before it can be graded.

Why: a scorecard is only honest if nobody can choose afterwards which runs to
count. So every team run (and every ad-hoc owner call, e.g. on a new IPO) is
appended to ``HOME/calls/calls.jsonl`` the moment it is made, with:

- ``registered_at`` (UTC, shown in ET and SGT) and the session the grade starts
  from (the first session after the data cutoff, entered at its OPEN, which is
  where the paper engine fills);
- ``pre_registered``: true only when the call was written before that open.
  Late calls are listed but never count toward a hit rate;
- ``official``: the FIRST pre-registered call for an instrument, cutoff and
  source. Re-running the team on the same data and keeping the run you like
  does not change the official call; reruns are kept and labelled.

The file is append-only and hash-chained like the ledger: editing, deleting or
reordering a line makes ``verify`` fail. Grades are computed from it on demand
and never written back into it.

Modes. ``FULL``: the team's own run (Merlin + Quant + challenge + checks).
``LIMITED``: an instrument with no SEC financial history (a new listing such as
ANT): there is no valuation; only price and market checks back the call, and
the record and the scorecard say so.

Scoring (plain): a BUY is right if the stock beats the benchmark (SPY) from the
next open to the close N sessions later; SELL/REDUCE/AVOID are right if it
lags. WAIT, HOLD and NO_ACTION are not right or wrong; their excess return is
shown, and for a WAIT that held back a BUY you can see what the BUY would have done.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

from merlin_quant.calendar import NY, OPEN, CalendarError, fmt_times, next_trading_day
from merlin_quant.data import DataUnavailable, MarketData

HORIZONS = (1, 5, 10)
SCORED_LONG = {"BUY"}
SCORED_SHORT = {"SELL", "REDUCE", "AVOID"}
OWNER_CALLS = ("BUY", "SELL", "AVOID", "WAIT", "HOLD")


class CallLogError(RuntimeError):
    pass


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def path(home: str | Path) -> Path:
    return Path(home) / "calls" / "calls.jsonl"


def read(home: str | Path) -> list[dict]:
    p = path(home)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def verify(home: str | Path) -> list[dict]:
    prev = "GENESIS"
    rows = read(home)
    for i, r in enumerate(rows):
        body = {k: v for k, v in r.items() if k != "hash"}
        if r.get("seq") != i or r.get("prev_hash") != prev or _hash(body) != r.get("hash"):
            raise CallLogError(f"call log broken at line {i + 1}: it was edited, removed or reordered")
        prev = r["hash"]
    return rows


def grade_start(cutoff: date) -> datetime:
    """The open of the first session after the cutoff: the call must be on file before this."""
    return datetime.combine(next_trading_day(cutoff), OPEN, tzinfo=NY)


def append(home: str | Path, call: dict, now: datetime | None = None) -> dict:
    rows = verify(home)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = date.fromisoformat(call["data_cutoff"])
    start = grade_start(cutoff)
    pre = now < start
    key = (call["instrument"], call["data_cutoff"], call["source"])
    official = pre and not any((r["instrument"], r["data_cutoff"], r["source"]) == key and r["pre_registered"]
                               for r in rows)
    body = {"seq": len(rows), "prev_hash": rows[-1]["hash"] if rows else "GENESIS",
            "registered_at": now.isoformat(timespec="seconds"), "registered_at_local": fmt_times(now),
            "grade_starts": start.isoformat(), "pre_registered": pre, "official": official, **call}
    body["call_id"] = "call-" + _hash(body)[:16]
    rec = {**body, "hash": _hash(body)}
    p = path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
        f.flush()
    return rec


def _close(md: MarketData, sym: str, d: date) -> float | None:
    for b in md.bars.get(sym, []):
        if b["date"] == d.isoformat():
            return b["close"]
    return None


def register_team_call(home, m, q, c, p, md: MarketData, cfg, now: datetime | None = None) -> dict:
    """Write the team's call for this run (stage C) to the call log."""
    gc = p.guard_checks or {}
    call = "WAIT" if gc.get("held_back") else p.action.value
    bench = cfg.portfolio.benchmark
    return append(home, {
        "source": "team", "mode": gc.get("mode", "FULL"), "instrument": p.instrument,
        "data_cutoff": p.data_cutoff.isoformat(), "call": call, "policy_action": gc.get("policy_action", call),
        "quantity": p.quantity, "target_weight": p.target_weight, "reference_price": p.reference_price,
        "benchmark": bench, "benchmark_close": _close(md, bench, p.data_cutoff),
        "merlin": {"stance": m.stance.value, "conviction": m.conviction, "fair_value_base": m.fair_value_base},
        "quant": {"stance": q.stance.value, "rating": q.tradingagents_rating},
        "challenge": c.verdict.value, "proposal_id": p.proposal_id,
        "checks": [{"name": r["name"], "outcome": r["outcome"], "plain": r["plain"]} for r in gc.get("results", [])],
        "data_source": md.source_name, "by": "team",
        "limitations": ["SYNTHETIC test data"] if "SYNTHETIC" in md.source_name.upper() else [],
    }, now=now)


def register_owner_call(home, symbol: str, call: str, by: str, md: MarketData, cfg, cutoff: date,
                        note: str = "", now: datetime | None = None) -> dict:
    """An ad-hoc call (e.g. a new IPO outside the team's universe), with the same checks the team gets."""
    from merlin_quant import guards
    call = call.upper()
    if call not in OWNER_CALLS:
        raise CallLogError(f"call must be one of {', '.join(OWNER_CALLS)}")
    if not by.strip():
        raise CallLogError("an owner call needs --by")
    view = md.view(cutoff)
    try:
        price = view.bars(symbol)[-1]
    except DataUnavailable as exc:
        raise CallLogError(str(exc)) from exc
    if price["date"] != cutoff.isoformat():
        raise CallLogError(f"{symbol}'s last close in the data is {price['date']}, not the cutoff {cutoff}")
    gr = guards.evaluate(view, symbol, cfg, now=now)
    limits = []
    if gr.mode == "LIMITED":
        limits = ["LIMITED MODE: no SEC financial history, so no valuation, no Merlin and no Quant run back this "
                  "call; only price and market checks.", f"{len(view.bars(symbol))} session(s) of price history"]
    bench = cfg.portfolio.benchmark
    return append(home, {
        "source": "owner", "mode": gr.mode, "instrument": symbol, "data_cutoff": cutoff.isoformat(), "call": call,
        "policy_action": call, "quantity": 0, "target_weight": 0.0, "reference_price": price["close"],
        "benchmark": bench, "benchmark_close": _close(md, bench, cutoff), "by": by, "note": note,
        "checks": [{"name": r.name, "outcome": r.outcome, "plain": r.plain} for r in gr.results],
        "checks_say_wait": bool(gr.blocking) and call == "BUY",
        "data_source": md.source_name, "limitations": limits,
    }, now=now)


# ------------------------------------------------------------------ grading

def _session_after(cutoff: date, n: int) -> date | None:
    try:
        d = cutoff
        for _ in range(n):
            d = next_trading_day(d)
        return d
    except CalendarError:
        return None


def _bar(md: MarketData, sym: str, d: date) -> dict | None:
    for b in md.bars.get(sym, []):
        if b["date"] == d.isoformat():
            return b
    return None


def excess_return(md: MarketData, sym: str, bench: str, cutoff: date, h: int) -> float | None:
    """Stock return minus benchmark return, from the open after the cutoff to the close h sessions after it.

    Uses split-adjusted bars as known at the exit session so a split inside the window cannot fake a move."""
    first, last = _session_after(cutoff, 1), _session_after(cutoff, h)
    if not first or not last:
        return None
    try:
        view = md.view(last)
        sb = {b["date"]: b for b in view.bars(sym)}
        bb = {b["date"]: b for b in view.bars(bench)}
    except DataUnavailable:
        return None
    k0, k1 = first.isoformat(), last.isoformat()
    if k0 not in sb or k1 not in sb or k0 not in bb or k1 not in bb:
        return None
    return (sb[k1]["close"] / sb[k0]["open"] - 1) - (bb[k1]["close"] / bb[k0]["open"] - 1)


def grade(home, md: MarketData, horizons=HORIZONS) -> list[dict]:
    out = []
    for r in verify(home):
        bench = r.get("benchmark") or "SPY"
        cutoff = date.fromisoformat(r["data_cutoff"])
        g = {"call_id": r["call_id"], "instrument": r["instrument"], "data_cutoff": r["data_cutoff"],
             "call": r["call"], "policy_action": r.get("policy_action"), "source": r["source"], "mode": r["mode"],
             "registered_at_local": r["registered_at_local"], "pre_registered": r["pre_registered"],
             "official": r["official"], "excess": {}, "right": {}}
        for h in horizons:
            x = excess_return(md, r["instrument"], bench, cutoff, h)
            g["excess"][h] = None if x is None else round(x, 4)
            if x is None or not r["official"]:
                g["right"][h] = None
            elif r["call"] in SCORED_LONG:
                g["right"][h] = x > 0
            elif r["call"] in SCORED_SHORT:
                g["right"][h] = x < 0
            else:
                g["right"][h] = None
        out.append(g)
    return out


def _pct(x):
    return "pending" if x is None else f"{x:+.1%}"


def scorecard(home, md: MarketData, horizons=HORIZONS, now: datetime | None = None) -> str:
    rows = grade(home, md, horizons)
    now = now or datetime.now(timezone.utc)
    lines = ["# Pre-registered calls scorecard", "",
             f"_Built {fmt_times(now)} from {path(home).name} (hash chain verified) and {md.source_name}._", "",
             "Each call was written down, timestamped, before the market opened on the first graded session. "
             "Grades run from that open (where a paper order would fill) to the close N sessions later, versus "
             "the benchmark. Only the first pre-registered call per stock, cutoff and source counts; reruns and "
             "late calls are shown but not scored.", ""]
    for source, title in (("team", "Team calls (FULL mode)"), ("owner", "Owner / ad-hoc calls")):
        sel = [g for g in rows if g["source"] == source]
        if not sel:
            continue
        lines += [f"## {title}", "",
                  "| Registered | Stock | Cutoff | Call | Mode | Counted | " + " | ".join(f"+{h}d excess" for h in horizons)
                  + " |", "|" + "---|" * (6 + len(horizons))]
        for g in sel:
            counted = "yes" if g["official"] else ("late" if not g["pre_registered"] else "rerun")
            call = g["call"] + (f" (policy: {g['policy_action']})" if g["call"] == "WAIT" and g["policy_action"] != "WAIT"
                                else "")
            cells = []
            for h in horizons:
                x, ok = g["excess"][h], g["right"][h]
                cells.append(_pct(x) + ("" if ok is None else (" ✓" if ok else " ✗")))
            lines.append(f"| {g['registered_at_local']} | {g['instrument']} | {g['data_cutoff']} | {call} | "
                         f"{g['mode']} | {counted} | " + " | ".join(cells) + " |")
        hit = []
        for h in horizons:
            sc = [g["right"][h] for g in sel if g["right"][h] is not None]
            hit.append(f"+{h}d {sum(sc)}/{len(sc)}" if sc else f"+{h}d n/a")
        held = [g for g in sel if g["official"] and g["call"] == "WAIT" and g["policy_action"] == "BUY"]
        lines += ["", f"**Hit rate (counted calls only):** {', '.join(hit)}"]
        if held:
            parts = []
            for g in held:
                x = g["excess"][horizons[-1]] if g["excess"][horizons[-1]] is not None else g["excess"][horizons[0]]
                parts.append(f"{g['instrument']} {g['data_cutoff']} {_pct(x)}")
            lines.append("**BUYs the checks held back (what the BUY would have done vs the benchmark):** "
                         + "; ".join(parts))
        if any(g["mode"] == "LIMITED" for g in sel):
            lines.append("_LIMITED = no SEC financial history behind the call (e.g. a new listing): price and market "
                         "checks only. Read those grades separately._")
        lines.append("")
    lines.append("_A handful of calls is noise: wait for at least 10 counted calls before reading anything into a "
                 "hit rate._")
    return "\n".join(lines) + "\n"
