"""Lessons loop: learn from graded calls, but only with the owner's say-so.

    1. `mq lessons propose` grades the assessment log (HOME/runs.csv) against the
       closes in the data file and writes HOME/lessons/lessons.md: lessons the
       evidence supports (status PROPOSED) and patterns still being watched.
    2. The owner reads it and runs `mq lessons approve <id> --by NAME` (or reject).
    3. Only APPROVED lessons reach the Merlin research prompt and the Quant
       challenge prompt. Quant's independent TradingAgents run is never touched.
    4. Every research record notes the `lessons_version` it was shown, so the
       scorecard can compare calls made before and after a lesson set.
    5. Undo: `mq lessons retire <id>` drops one lesson, `mq lessons off` drops all.

Every change is appended to HOME/lessons/history.jsonl with who and when.
Lessons are context for the LLM narrative only: they never change a risk limit,
the decision policy, the challenge verdict rules or the operating mode.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

ROLES = ("merlin", "challenge")
HORIZONS = (1, 5, 10)
MIN_EVIDENCE = 2  # a pattern must show up at least this many times before it is proposed


# ------------------------------------------------------------------ store

def _dir(home) -> Path:
    return Path(home).expanduser() / "lessons"


def load(home) -> dict:
    p = _dir(home) / "lessons.json"
    if not p.exists():
        return {"enabled": True, "lessons": []}
    return json.loads(p.read_text())


def _save(home, store: dict, action: str, by: str, lesson_id: str | None = None, reason: str = "") -> None:
    d = _dir(home)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "lessons.json.tmp"
    tmp.write_text(json.dumps(store, indent=1))
    tmp.replace(d / "lessons.json")
    with open(d / "history.jsonl", "a") as f:
        f.write(json.dumps({"ts": _now(), "action": action, "by": by, "lesson_id": lesson_id, "reason": reason,
                            "lessons_version": version(store)}) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def approved(store: dict) -> list[dict]:
    if not store.get("enabled", True):
        return []
    return [l for l in store["lessons"] if l["status"] == "APPROVED"]


def version(store: dict) -> str:
    """'none' when no lesson is active; otherwise a hash of exactly the active lesson texts."""
    act = approved(store)
    if not act:
        return "none"
    blob = json.dumps(sorted((l["id"], l["prompt_text"], l["applies_to"]) for l in act)).encode()
    return "lv-" + hashlib.sha256(blob).hexdigest()[:10]


def prompt_block(home, role: str) -> tuple[str, str]:
    """(text to append to a lead's prompt, lessons_version). Empty text when nothing applies."""
    if home is None:
        return "", "none"
    store = load(home)
    v = version(store)
    act = [l for l in approved(store) if role in l["applies_to"]]
    if not act:
        return "", v
    lines = [f"OWNER-APPROVED LESSONS FROM PAST GRADED CALLS (lessons_version {v}).",
             "These come from a very small sample of past calls. Weigh them as context about where earlier calls went "
             "wrong. They never override your rules, the evidence cutoff, the verdict rules or any risk limit, and "
             "they are not new data.", ""]
    lines += [f"- {l['prompt_text']}" for l in act]
    return "\n".join(lines), v


def set_status(home, lesson_id: str, status: str, by: str, reason: str = "") -> dict:
    if not by.strip():
        raise ValueError("a decision needs a name (--by)")
    store = load(home)
    l = next((x for x in store["lessons"] if x["id"] == lesson_id), None)
    if l is None:
        raise ValueError(f"no lesson {lesson_id}")
    allowed = {"APPROVED": ("PROPOSED", "RETIRED", "REJECTED"), "REJECTED": ("PROPOSED",), "RETIRED": ("APPROVED",)}
    if l["status"] not in allowed[status]:
        raise ValueError(f"{lesson_id} is {l['status']}; cannot move it to {status}")
    l.update(status=status, decided_by=by, decided_at=_now(), decision_reason=reason)
    _save(home, store, status.lower(), by, lesson_id, reason)
    return l


def set_enabled(home, enabled: bool, by: str) -> str:
    store = load(home)
    store["enabled"] = enabled
    _save(home, store, "on" if enabled else "off", by)
    return version(store)


# ------------------------------------------------------------------ grading

def read_runs(path) -> list[dict]:
    p = Path(path).expanduser()
    return list(csv.DictReader(open(p))) if p.exists() else []


def _direction(action: str) -> int:
    a = (action or "").upper()
    return 1 if a in ("BUY", "ADD", "INCREASE") else -1 if a in ("SELL", "REDUCE", "EXIT", "TRIM") else 0


def grade(rows: list[dict], bars: dict, symbol: str = "NVDA", bench: str = "SPY") -> list[dict]:
    """run1 rows with excess return vs the benchmark at +1/+5/+10 sessions (None while pending)."""
    c = {s: {b["date"]: b["close"] for b in bars[s]} for s in (symbol, bench)}
    dates = sorted(c[symbol])
    out = []
    for r in rows:
        if r.get("run_type") != "run1" or r["data_cutoff"] not in c[symbol]:
            continue
        i = dates.index(r["data_cutoff"])
        g = dict(r, direction=_direction(r["action"]), excess={}, right={})
        for h in HORIZONS:
            if i + h >= len(dates):
                g["excess"][h] = g["right"][h] = None
                continue
            d0, d1 = dates[i], dates[i + h]
            ex = c[symbol][d1] / c[symbol][d0] - c[bench][d1] / c[bench][d0]
            g["excess"][h] = ex
            g["right"][h] = None if g["direction"] == 0 else g["direction"] * ex > 0
        g["window_end"] = {h: dates[min(i + h, len(dates) - 1)] for h in HORIZONS}
        out.append(g)
    return out


# ------------------------------------------------------------------ rules

def _earnings_sessions(filings: list[dict], dates: list[str], extra: list[str]) -> list[str]:
    """First session on/after each earnings date. Filing dates stand in for report dates (NVDA files its 10-Q
    within a day of reporting); owner-entered upcoming dates come from HOME/lessons/earnings_dates.txt."""
    raw = sorted({f["filed"] for f in filings if f.get("filed")} | set(extra))
    out = []
    for e in raw:
        nxt = [d for d in dates if d > e]  # results land after the close: the reaction is the next session
        if nxt:
            out.append(nxt[0])
        elif e >= dates[-1]:
            out.append(e)  # upcoming: keep the calendar date so open windows can be flagged
    return out


def _fmt(x):
    return "pending" if x is None else f"{x:+.1%}"


def analyse(rows: list[dict], md: dict, symbol: str = "NVDA", bench: str = "SPY", extra_earnings=(),
            min_evidence: int = MIN_EVIDENCE) -> dict:
    """Candidate lessons (enough evidence) and watch items (not yet). Pure function of its inputs."""
    graded = grade(rows, md["bars"], symbol, bench)
    c = {s: {b["date"]: b["close"] for b in md["bars"][s]} for s in (symbol, bench)}
    dates = sorted(c[symbol])
    earn = _earnings_sessions(md.get("filings", {}).get(symbol, []), dates, list(extra_earnings))
    candidates, watch = [], []

    # 1. earnings inside the grading window
    hist = []
    for e in earn:
        if e in c[symbol] and dates.index(e) > 0:
            p = dates[dates.index(e) - 1]
            hist.append(c[symbol][e] / c[symbol][p] - c[bench][e] / c[bench][p])
    rets = [c[symbol][dates[i]] / c[symbol][dates[i - 1]] - c[bench][dates[i]] / c[bench][dates[i - 1]]
            for i in range(1, len(dates))]
    typical = sorted(abs(x) for x in rets)[len(rets) // 2] if rets else None
    hit = [g for g in graded if any(g["data_cutoff"] < e <= g["window_end"][10] or
                                    (e > dates[-1] and g["data_cutoff"] < e) for e in earn)]
    earn_avg = sum(abs(x) for x in hist) / len(hist) if hist else None
    if hit and earn_avg is not None:
        item = {"kind": "earnings_window", "key": "earnings_window",
                "plain": (f"{len(hit)} graded call(s) have an earnings report inside their 1-10 day window. In the last "
                          f"{len(hist)} reports NVDA's next-day move vs SPY averaged {earn_avg:.1%} (either way), "
                          f"against a typical day of {typical:.1%}. One earnings reaction can decide the grade."),
                "prompt_text": (f"Earnings risk: in the last {len(hist)} quarterly reports NVDA's next-session move "
                                f"versus SPY averaged {earn_avg:.1%} in absolute terms, about "
                                f"{earn_avg / typical:.0f}x a typical day ({typical:.1%}). When a report falls within "
                                "10 sessions of the cutoff, say so explicitly, treat the short-term outcome as "
                                "largely a bet on the report, and keep conviction moderate unless the evidence is "
                                "specifically about the coming quarter."),
                "applies_to": ["merlin", "challenge"],
                "evidence": {"calls": [g["proposal_id"] for g in hit], "earnings_sessions": earn[-6:],
                             "past_reactions_vs_spy": [round(x, 4) for x in hist[-8:]],
                             "typical_abs_daily_excess": round(typical, 4)}}
        # backed by the stock's own history (many reports), so it does not wait for min_evidence calls
        (candidates if len(hist) >= 4 else watch).append(item)

    # 2. BUY calls where Quant did not fully agree
    dis = [g for g in graded if g["direction"] > 0 and g.get("challenge") != "AGREE" and g["right"][1] is not None]
    wrong = [g for g in dis if g["right"][5] is False or (g["right"][5] is None and g["right"][1] is False)]
    item = {"kind": "disagreement", "key": "buy_against_quant",
            "plain": (f"BUY calls made while Quant did not fully agree: {len(dis)} graded so far, {len(wrong)} wrong "
                      f"(+5d, or +1d while +5d is pending). Excess: "
                      + (", ".join(f"{g['data_cutoff']} {_fmt(g['excess'][1])}/{_fmt(g['excess'][5])}" for g in dis)
                         or "none") + "."),
            "prompt_text": (f"In {len(wrong)} of {len(dis)} graded BUY calls made while Quant only partly agreed or "
                            "disagreed, NVDA then lagged SPY. When Quant's market read is neutral or bearish, state "
                            "concretely what in the fundamentals outweighs it right now, or lower conviction."),
            "applies_to": ["merlin"],
            "evidence": {"calls": [{"proposal_id": g["proposal_id"], "cutoff": g["data_cutoff"],
                                    "quant": g.get("quant_stance"), "challenge": g.get("challenge"),
                                    "excess": {h: (None if v is None else round(v, 4)) for h, v in g["excess"].items()}}
                                   for g in dis]}}
    (candidates if len(dis) >= min_evidence and len(wrong) * 2 > len(dis) else watch).append(item)

    # 3. run-to-run noise: consecutive real runs (or a back-to-back double) that flip with little new data
    real = [r for r in rows if r.get("run_type") in ("run1", "run2", "double") and r["data_cutoff"] in c[symbol]]
    gap = lambda a, b: dates.index(b["data_cutoff"]) - dates.index(a["data_cutoff"])  # in sessions
    flips = []
    for a, b in zip(real, real[1:]):
        gap_ = gap(a, b)
        changed = [f for f in ("action", "quant_stance", "merlin_stance") if a.get(f) != b.get(f)]
        if changed and gap_ <= 1:
            flips.append({"from": a["proposal_id"], "to": b["proposal_id"], "cutoffs": [a["data_cutoff"], b["data_cutoff"]],
                          "changed": {f: [a[f], b[f]] for f in changed}, "same_data": gap_ == 0})
    pairs = sum(1 for a, b in zip(real, real[1:]) if gap(a, b) <= 1)
    item = {"kind": "noise", "key": "run_to_run_noise",
            "plain": (f"{len(flips)} of {pairs} back-to-back runs (same or next day's data) changed the call or a "
                      "lead's stance: " + ("; ".join(f"{f['cutoffs'][0]}→{f['cutoffs'][1]} "
                                                      + ", ".join(f"{k} {v[0]}→{v[1]}" for k, v in f['changed'].items())
                                                      for f in flips) or "none") + "."),
            "prompt_text": ("Run-to-run noise: on consecutive runs with at most one extra day of data, the team's call "
                            f"or a lead's stance changed {len(flips)} of {pairs} times. Anchor the stance on evidence "
                            "that would survive a rerun, and say which inputs, if any, changed since the last session."),
            "applies_to": ["merlin", "challenge"], "evidence": {"flips": flips, "pairs": pairs}}
    (candidates if len(flips) >= min_evidence else watch).append(item)

    return {"graded": graded, "candidates": candidates, "watch": watch}


# ------------------------------------------------------------------ propose + report

def propose(home, rows: list[dict], md: dict, symbol: str = "NVDA", bench: str = "SPY",
            min_evidence: int = MIN_EVIDENCE) -> dict:
    """Add new PROPOSED lessons (never approves), refresh evidence on open proposals, write lessons.md."""
    d = _dir(home)
    extra = []
    ef = d / "earnings_dates.txt"
    if ef.exists():
        extra = [x.strip() for x in ef.read_text().splitlines() if x.strip() and not x.startswith("#")]
    res = analyse(rows, md, symbol, bench, extra, min_evidence)
    store = load(home)
    today = date.today().isoformat()
    new = []
    for cand in res["candidates"]:
        same = [l for l in store["lessons"] if l["key"] == cand["key"]]
        live = next((l for l in same if l["status"] == "PROPOSED"), None)
        if live:  # refresh the numbers; the owner has not decided yet
            live.update(prompt_text=cand["prompt_text"], plain=cand["plain"], evidence=cand["evidence"],
                        refreshed=today)
            continue
        if any(l["status"] in ("APPROVED", "REJECTED", "RETIRED") for l in same):
            continue  # decided: never re-propose the same pattern behind the owner's back
        lid = f"L-{today.replace('-', '')}-{cand['key']}"
        l = {"id": lid, "key": cand["key"], "kind": cand["kind"], "status": "PROPOSED", "created": today,
             "applies_to": cand["applies_to"], "plain": cand["plain"], "prompt_text": cand["prompt_text"],
             "evidence": cand["evidence"]}
        store["lessons"].append(l)
        new.append(lid)
    _save(home, store, "propose", "lessons-loop", ",".join(new) or None)
    report = render(store, res)
    (d / "lessons.md").write_text(report)
    return {"new": new, "report": str(d / "lessons.md"), "version": version(store), "text": report}


def render(store: dict, res: dict) -> str:
    v = version(store)
    out = ["# Lessons from the NVDA calls", "",
           f"_Updated {datetime.now().strftime('%Y-%m-%d %H:%M')}. Active lesson set: **{v}**"
           + ("" if store.get("enabled", True) else " (lessons switched OFF)") + "._", "",
           "Nothing below reaches the team until you approve it. The sample is tiny (one stock, a few weeks), "
           "so treat every line as a hunch to test, not a rule.", ""]
    groups = [("Waiting for your decision", "PROPOSED"), ("In use (approved)", "APPROVED"),
              ("Retired", "RETIRED"), ("Rejected", "REJECTED")]
    for title, st in groups:
        ls = [l for l in store["lessons"] if l["status"] == st]
        if not ls:
            continue
        out += [f"## {title}", ""]
        for l in ls:
            out += [f"**{l['id']}** ({l['kind']}, for {' + '.join(l['applies_to'])}, proposed {l['created']})", "",
                    f"- What we saw: {l['plain']}",
                    f"- What the team would be told: \"{l['prompt_text']}\""]
            if st == "PROPOSED":
                out.append(f"- Approve: `mq lessons approve {l['id']} --by Ed` · reject: "
                           f"`mq lessons reject {l['id']} --by Ed --reason \"...\"`")
            else:
                out.append(f"- Decided by {l.get('decided_by')} on {(l.get('decided_at') or '')[:10]}"
                           + (f": {l['decision_reason']}" if l.get("decision_reason") else ""))
            if st == "APPROVED":
                out.append(f"- Undo: `mq lessons retire {l['id']} --by Ed`")
            out.append("")
    if res["watch"]:
        out += ["## Watching (not enough evidence yet)", ""]
        out += [f"- {w['plain']}" for w in res["watch"]]
        out.append("")
    g = res["graded"]
    if g:
        out += ["## Graded decision calls (run 1)", "", "| Cutoff | Call | Quant | +1d | +5d | +10d |",
                "|---|---|---|---|---|---|"]
        mark = lambda r: "" if r is None else (" ✓" if r else " ✗")
        out += [f"| {x['data_cutoff']} | {x['action']} | {x.get('quant_stance')} | "
                + " | ".join(_fmt(x['excess'][h]) + mark(x['right'][h]) for h in HORIZONS) + " |" for x in g]
        out.append("")
    out += ["Switch every lesson off at once: `mq lessons off --by Ed` (back on: `mq lessons on --by Ed`).", ""]
    return "\n".join(out)
