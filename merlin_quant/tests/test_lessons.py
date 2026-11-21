"""Lessons loop: proposals never reach a prompt until the owner approves them, and every step is reversible."""
from datetime import date, timedelta

import pytest

from merlin_quant import lessons as L
from merlin_quant.merlin import run_merlin, scripted_merlin
from merlin_quant.runtime import ScriptedRuntime

from .conftest import CUTOFF


def _md(n=120):
    """NVDA/SPY closes on weekdays; NVDA jumps 8% the session after each 'filed' date."""
    d, dates = date(2026, 5, 1), []
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    filed = [dates[i] for i in (10, 40, 70, 100)]
    nv, sp, bn, bs = 100.0, 500.0, [], []
    for i, x in enumerate(dates):
        nv *= 1.08 if i and dates[i - 1] in filed else (1.002 if i % 2 else 0.999)
        sp *= 1.001
        bn.append({"date": x, "close": nv}); bs.append({"date": x, "close": sp})
    return {"bars": {"NVDA": bn, "SPY": bs}, "filings": {"NVDA": [{"filed": f} for f in filed]}}, dates


def _row(rt, cutoff, action="BUY", quant="NEUTRAL", ch="PARTIALLY_AGREE", merlin="BULLISH", pid=None):
    return {"run_type": rt, "data_cutoff": cutoff, "action": action, "quant_stance": quant, "challenge": ch,
            "merlin_stance": merlin, "proposal_id": pid or f"p-{rt}-{cutoff}"}


def test_proposals_stay_out_of_prompts_until_approved(tmp_path):
    md, dates = _md()
    rows = [_row("run1", dates[38]), _row("run2", dates[39], "NO_ACTION", "BEARISH", "DISAGREE"),
            _row("run1", dates[60]), _row("run2", dates[61], "NO_ACTION", "BEARISH", "DISAGREE")]
    res = L.propose(tmp_path, rows, md)
    assert res["new"] and res["version"] == "none"
    assert all(l["status"] == "PROPOSED" for l in L.load(tmp_path)["lessons"])
    assert L.prompt_block(tmp_path, "merlin") == ("", "none")
    assert "Waiting for your decision" in (tmp_path / "lessons" / "lessons.md").read_text()

    lid = next(l["id"] for l in L.load(tmp_path)["lessons"] if l["key"] == "earnings_window")
    with pytest.raises(ValueError):
        L.set_status(tmp_path, lid, "APPROVED", by="")          # a decision needs a name
    L.set_status(tmp_path, lid, "APPROVED", by="Ed")
    text, v = L.prompt_block(tmp_path, "merlin")
    assert v.startswith("lv-") and "Earnings risk" in text
    assert L.prompt_block(tmp_path, "challenge")[1] == v

    # reversible: off/on, then retire
    L.set_enabled(tmp_path, False, "Ed")
    assert L.prompt_block(tmp_path, "merlin") == ("", "none")
    L.set_enabled(tmp_path, True, "Ed")
    L.set_status(tmp_path, lid, "RETIRED", by="Ed")
    assert L.prompt_block(tmp_path, "merlin") == ("", "none")
    hist = (tmp_path / "lessons" / "history.jsonl").read_text().splitlines()
    assert len(hist) >= 5 and '"by": "Ed"' in hist[-1]

    # a decided (here: retired) pattern is never re-proposed behind the owner's back
    assert lid not in L.propose(tmp_path, rows, md)["new"]
    assert sum(l["key"] == "earnings_window" for l in L.load(tmp_path)["lessons"]) == 1


def test_thin_evidence_is_watched_not_proposed(tmp_path):
    md, dates = _md()
    res = L.analyse([_row("run1", dates[20])], md)
    kinds = {c["kind"] for c in res["candidates"]}
    assert "disagreement" not in kinds and "noise" not in kinds      # one call is not a pattern
    assert {w["kind"] for w in res["watch"]} >= {"disagreement", "noise"}
    assert res["graded"][0]["excess"][1] is not None


def test_noise_counts_flips_on_adjacent_runs(tmp_path):
    md, dates = _md()
    rows = [_row("run1", dates[20]), _row("run2", dates[21], "NO_ACTION", "BEARISH"),
            _row("run1", dates[21], "BUY", "NEUTRAL"), _row("double", dates[21], "NO_ACTION", "BEARISH")]
    noise = next(c for c in L.analyse(rows, md)["candidates"] if c["kind"] == "noise")
    assert len(noise["evidence"]["flips"]) == 3


def test_approved_lessons_reach_merlin_prompt_and_record(tmp_path, md):
    rt = ScriptedRuntime(scripted_merlin)
    r0 = run_merlin(md.view(CUTOFF), "AAPL", ["AAPL", "MSFT", "NVDA"], CUTOFF, rt)
    assert "lessons_version=none" in r0.run.notes
    rt = ScriptedRuntime(scripted_merlin)
    r1 = run_merlin(md.view(CUTOFF), "AAPL", ["AAPL", "MSFT", "NVDA"], CUTOFF, rt, lessons=("LESSON TEXT X", "lv-abc"))
    assert "LESSON TEXT X" in rt.transcript[-1][1] and "lessons_version=lv-abc" in r1.run.notes


def test_team_records_lessons_version_and_quant_stays_blind(team):
    m, q, c, j, p = team.research("NVDA", CUTOFF)
    assert "lessons_version=none" in m.run.notes and "lessons_version=none" in c.run.notes
    assert not any("lessons" in n for n in q.run.notes)          # Quant's independent run never sees lessons
