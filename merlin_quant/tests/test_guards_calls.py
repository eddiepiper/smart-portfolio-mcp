"""Checks before buying (stale data, earnings window, market sell-off) and pre-registered calls.

Built from the November 2026 NVDA calls: BUY on 17-18 Nov while Merlin's newest SEC quarter was July and the
company had just reported, then NVDA fell ~19% in a market sell-off. Synthetic fixtures only; no model runs.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from merlin_quant import calls as C
from merlin_quant import guards
from merlin_quant import risk as risk_gate
from merlin_quant.calendar import NY
from merlin_quant.cli import main as cli
from merlin_quant.config import EXAMPLE_CONFIG, Config
from merlin_quant.contracts import Action, Stance
from merlin_quant.data import synthetic_fixture
from merlin_quant.tests.conftest import CUTOFF, NOW


def cfg_guards(**g) -> Config:
    data = json.loads(Path(EXAMPLE_CONFIG).read_text())
    for k, v in g.items():
        if isinstance(v, dict):
            data["guards"][k].update(v)
        else:
            data["guards"][k] = v
    return Config.model_validate(data)


def results_8k(md, sym, when: str):
    md.news.setdefault(sym, []).append({"published": when, "headline": "SEC 8-K filed: 2.02 results of operations; 9.01",
                                        "source": "sec_edgar"})
    md.news[sym].sort(key=lambda n: n["published"])


def crash(md, sym, d: date, pct: float):
    """Make *sym* close *pct* lower on session d than the session before (and keep later bars consistent)."""
    bars = md.bars[sym]
    i = next(k for k, b in enumerate(bars) if b["date"] == d.isoformat())
    f = (bars[i - 1]["close"] * (1 - pct)) / bars[i]["close"]
    for b in bars[i:]:
        for k in ("open", "high", "low", "close"):
            b[k] = round(b[k] * f, 2)


def make_team(tmp_path, cfg, md, name="home"):
    from merlin_quant.workflow import Team
    t = Team(tmp_path / name, cfg, md, runtime_override="scripted")
    t.engine.init()
    t.engine.open()
    return t


def by_name(p, name):
    return next(r for r in p.guard_checks["results"] if r["name"] == name)


# ------------------------------------------------------------------ baseline

def test_baseline_buy_passes_every_check(team):
    m, q, c, j, p = team.research("AAPL", CUTOFF, now=NOW)
    assert p.action == Action.BUY and not p.guard_checks["held_back"]
    assert [r["outcome"] for r in p.guard_checks["results"]][:1] == ["PASS"]
    assert all(r["outcome"] != "BLOCK" for r in p.guard_checks["results"])


def test_older_config_without_guards_still_loads():
    data = json.loads(Path(EXAMPLE_CONFIG).read_text())
    data.pop("guards")
    cfg = Config.model_validate(data)
    assert cfg.guards.enabled and cfg.guards.market.index_1d_drop == 0.025 and cfg.guards.earnings_window.pre_sessions == 5


# ------------------------------------------------------------------ fix 1: stale fundamentals

def test_new_results_not_yet_in_sec_data_caps_merlin_and_blocks_buy(tmp_path, cfg):
    md = synthetic_fixture()
    results_8k(md, "AAPL", "2026-11-04T16:30:00-05:00")  # results released after the 10-K filed 2026-11-02
    t = make_team(tmp_path, cfg, md)
    m, q, c, j, p = t.research("AAPL", CUTOFF, now=NOW)
    fresh = by_name(p, "data_freshness")
    assert fresh["outcome"] == "BLOCK" and "04 Nov 2026" in fresh["plain"] and "30 Sep 2026" in fresh["plain"]
    assert m.stance == Stance.NEUTRAL and m.conviction <= 0.4                # Merlin capped, in code
    assert m.unknowns[0].startswith("Stance capped from BULLISH to NEUTRAL")
    assert p.action != Action.BUY and p.eligibility == "NOT_ELIGIBLE"


def test_quarter_too_old_counts_as_stale(md, cfg):
    g = cfg_guards(max_quarter_age_days=60).guards
    r = guards.check_data_freshness(md.view(date(2026, 12, 15)), "AAPL", g, datetime(2026, 12, 15, 16, tzinfo=NY))
    assert r.outcome == "BLOCK" and r.details["quarter_age_days"] == 76


def test_results_after_the_close_count_only_if_known_before_the_next_open(md, cfg):
    results_8k(md, "AAPL", "2026-11-06T16:30:00-05:00")  # after the cutoff close
    view = md.view(CUTOFF)
    assert guards.check_data_freshness(view, "AAPL", cfg.guards, guards.known_by_time(CUTOFF, None)).outcome == "PASS"
    assert guards.check_data_freshness(view, "AAPL", cfg.guards, guards.known_by_time(CUTOFF, NOW)).outcome == "BLOCK"


def test_same_day_10q_and_results_is_not_stale(md, cfg):
    results_8k(md, "AAPL", "2026-11-02T16:30:00-05:00")  # filed with the 10-K the same day
    r = guards.check_data_freshness(md.view(CUTOFF), "AAPL", cfg.guards, guards.known_by_time(CUTOFF, NOW))
    assert r.outcome == "PASS"


def test_flag_only_setting_warns_but_does_not_cap_or_block(tmp_path):
    md = synthetic_fixture()
    results_8k(md, "AAPL", "2026-11-04T16:30:00-05:00")
    t = make_team(tmp_path, cfg_guards(stale_data_action="flag", earnings_window={"action": "flag"}), md)
    m, q, c, j, p = t.research("AAPL", CUTOFF, now=NOW)
    assert by_name(p, "data_freshness")["outcome"] == "FLAG"
    assert m.stance == Stance.BULLISH and p.action == Action.BUY


# ------------------------------------------------------------------ fix 2: earnings window

def test_estimated_report_inside_window_turns_buy_into_wait(tmp_path, cfg):
    md = synthetic_fixture()
    results_8k(md, "AAPL", "2025-11-10T16:30:00-05:00")  # a year ago -> next report estimated 2026-11-09
    t = make_team(tmp_path, cfg, md)
    m, q, c, j, p = t.research("AAPL", CUTOFF, now=NOW)
    ew = by_name(p, "earnings_window")
    assert ew["outcome"] == "BLOCK" and "09 Nov 2026" in ew["plain"] and "estimated" in ew["plain"]
    assert p.guard_checks["policy_action"] == "BUY" and p.guard_checks["held_back"]
    assert p.action == Action.NO_ACTION and j.rationale.startswith("WAIT")
    assert t.last_call["call"] == "WAIT" and t.last_call["policy_action"] == "BUY"


def test_owner_date_wins_over_estimate_and_window_is_configurable(md):
    results_8k(md, "AAPL", "2025-11-10T16:30:00-05:00")
    view, kb = md.view(CUTOFF), guards.known_by_time(CUTOFF, NOW)
    owner = cfg_guards(earnings_dates={"AAPL": ["2026-11-30"]}).guards
    r = guards.check_earnings_window(view, "AAPL", owner, kb)
    assert r.outcome == "PASS" and r.details["next_report_source"] == "owner"
    short = cfg_guards(earnings_window={"pre_sessions": 0}).guards
    assert guards.check_earnings_window(view, "AAPL", short, kb).outcome == "PASS"


def test_just_reported_blocks_for_post_sessions(md, cfg):
    results_8k(md, "AAPL", "2026-11-04T16:30:00-05:00")  # first reaction session 2026-11-05
    kb = guards.known_by_time(CUTOFF, NOW)
    assert guards.check_earnings_window(md.view(CUTOFF), "AAPL", cfg.guards, kb).outcome == "BLOCK"   # 1 session on
    later = date(2026, 11, 10)                                                                         # 3 sessions on
    assert guards.check_earnings_window(md.view(later), "AAPL", cfg.guards, guards.known_by_time(later, None)).outcome == "PASS"


def test_nvda_november_dates_are_caught():
    """NVDA: 8-K results on 2025-11-19 -> the 2026 report is expected 2026-11-18, two sessions after a 16 Nov cutoff."""
    md = synthetic_fixture()
    for t in ("2025-11-19T16:20:44-05:00", "2026-02-25T16:31:25-05:00", "2026-05-20T16:21:19-04:00",
              "2026-08-26T16:21:19-04:00"):
        results_8k(md, "NVDA", t)
    view = md.view(date(2026, 11, 13))
    nxt, how = guards.next_report(view, "NVDA", guards.results_releases(view, "NVDA", datetime(2026, 11, 13, 16, tzinfo=NY)),
                                  [], 45)
    assert (nxt, how) == (date(2026, 11, 18), "estimated")


# ------------------------------------------------------------------ fix 3: market regime

def test_market_selloff_blocks_new_buys(tmp_path, cfg):
    md = synthetic_fixture()
    crash(md, "SPY", CUTOFF, 0.03)
    t = make_team(tmp_path, cfg, md)
    m, q, c, j, p = t.research("AAPL", CUTOFF, now=NOW)
    mr = by_name(p, "market_regime")
    assert mr["outcome"] == "BLOCK" and "SPY fell 3.0% in one session" in mr["plain"]
    assert p.action == Action.NO_ACTION


def test_stock_falling_much_faster_than_market(md, cfg):
    crash(md, "AAPL", CUTOFF, 0.12)
    r = guards.check_market_regime(md.view(CUTOFF), "AAPL", "SPY", cfg.guards)
    assert r.outcome == "BLOCK" and "falling much faster than the market" in r.plain
    flag = cfg_guards(market={"relative_action": "flag"}).guards
    assert guards.check_market_regime(md.view(CUTOFF), "AAPL", "SPY", flag).outcome == "FLAG"


def test_extra_index_checked_only_when_in_data(md, cfg):
    r = guards.check_market_regime(md.view(CUTOFF), "AAPL", "SPY", cfg.guards)
    assert r.details["indices_not_in_data"] == ["QQQ"]
    md.bars["QQQ"] = [dict(b) for b in md.bars["SPY"]]
    crash(md, "QQQ", CUTOFF, 0.04)
    assert guards.check_market_regime(md.view(CUTOFF), "AAPL", "SPY", cfg.guards).outcome == "BLOCK"


def test_switching_guards_off_only_flags(tmp_path):
    md = synthetic_fixture()
    crash(md, "SPY", CUTOFF, 0.03)
    t = make_team(tmp_path, cfg_guards(enabled=False), md)
    m, q, c, j, p = t.research("AAPL", CUTOFF, now=NOW)
    assert p.action == Action.BUY and by_name(p, "market_regime")["outcome"] == "FLAG"


# ------------------------------------------------------------------ the risk gate re-checks at execution

def test_risk_gate_blocks_an_older_buy_when_checks_fire(tmp_path, cfg):
    md = synthetic_fixture()
    t = make_team(tmp_path, cfg, md)
    m, q, c, j, p = t.research("AAPL", CUTOFF, now=NOW)           # clean BUY
    assert p.action == Action.BUY
    crash(md, "SPY", CUTOFF, 0.03)                                # e.g. the data was refreshed after a sell-off
    st = t.engine.ledger.state()
    r = risk_gate.evaluate(p, st, cfg, md, last_completed_session=CUTOFF, executed_proposals=set(),
                           orders_this_session=0, now=NOW)
    assert r.outcome == "BLOCKED" and any(x.startswith("checks_before_buying: WAIT") for x in r.reasons)


def test_sales_are_never_held_back(team, md):
    p = team.research("AAPL", CUTOFF, now=NOW)[-1]
    sell = p.model_copy(update={"action": Action.SELL})
    crash(md, "SPY", CUTOFF, 0.05)
    st = team.engine.ledger.state()
    r = risk_gate.evaluate(sell, st, team.cfg, md, last_completed_session=CUTOFF, executed_proposals=set(),
                           orders_this_session=0, now=NOW)
    assert not any(c.name == "checks_before_buying" for c in r.checks)


# ------------------------------------------------------------------ fix 4: pre-registered calls

def test_team_run_is_pre_registered_and_reruns_do_not_count(team):
    team.research("AAPL", CUTOFF, now=NOW)
    first = team.last_call
    assert first["pre_registered"] and first["official"] and first["call"] == "BUY"
    assert first["registered_at_local"].endswith("SGT") and first["grade_starts"].startswith("2026-11-09T09:30")
    team.research("AAPL", CUTOFF, now=NOW + timedelta(minutes=30))     # same data, run again
    assert team.last_call["pre_registered"] and not team.last_call["official"]
    late = datetime(2026, 11, 9, 10, 0, tzinfo=NY)                    # after the first graded session opened
    team.research("AAPL", CUTOFF, now=late)
    assert not team.last_call["pre_registered"] and not team.last_call["official"]
    assert len(C.verify(team.engine.home)) == 3


def test_call_log_tamper_is_detected(team):
    team.research("AAPL", CUTOFF, now=NOW)
    p = C.path(team.engine.home)
    p.write_text(p.read_text().replace('"call": "BUY"', '"call": "SELL"'))
    with pytest.raises(C.CallLogError):
        C.verify(team.engine.home)


def test_grading_runs_from_next_open_against_benchmark(team, md):
    team.research("AAPL", CUTOFF, now=NOW)
    g = C.grade(team.engine.home, md)[0]
    a = {b["date"]: b for b in md.bars["AAPL"]}
    s = {b["date"]: b for b in md.bars["SPY"]}
    exp = (a["2026-11-09"]["close"] / a["2026-11-09"]["open"] - 1) - (s["2026-11-09"]["close"] / s["2026-11-09"]["open"] - 1)
    assert g["excess"][1] == round(exp, 4) and g["right"][1] == (exp > 0)
    assert g["excess"][10] is None                                    # fixture ends 2026-11-13: pending
    text = C.scorecard(team.engine.home, md, now=NOW)
    assert "| AAPL | 2026-11-06 | BUY | FULL | yes |" in text and "Hit rate" in text


def ipo_data():
    md = synthetic_fixture()
    md.bars["ANT"] = [dict(b, open=b["open"] / 2, high=b["high"] / 2, low=b["low"] / 2, close=b["close"] / 2)
                      for b in md.bars["AAPL"] if b["date"] >= "2026-11-04"]
    md.profiles["ANT"] = {"name": "New listing (synthetic)", "exchange": "NasdaqGS"}
    return md


def test_new_ipo_gets_a_limited_mode_owner_call(tmp_path, cfg):
    md = ipo_data()
    r = C.register_owner_call(tmp_path, "ANT", "BUY", "Ed", md, cfg, CUTOFF, note="IPO", now=NOW)
    assert r["mode"] == "LIMITED" and r["official"] and r["source"] == "owner"
    assert r["limitations"][0].startswith("LIMITED MODE") and "3 session(s)" in r["limitations"][1]
    names = {c["name"]: c["outcome"] for c in r["checks"]}
    assert names["data_freshness"] == "FLAG" and names["earnings_window"] == "FLAG"
    g = C.grade(tmp_path, md)[0]
    assert g["mode"] == "LIMITED" and g["right"][1] in (True, False)
    assert "LIMITED = no SEC financial history" in C.scorecard(tmp_path, md, now=NOW)


def test_cli_call_and_calls_grade(tmp_path, capsys):
    data = tmp_path / "ipo.json"
    ipo_data().save(data)
    home = str(tmp_path / "h")
    rc = cli(["--home", home, "--data", str(data), "--now", NOW.isoformat(), "--cutoff", CUTOFF.isoformat(),
              "call", "ANT", "--call", "BUY", "--by", "Ed"])
    out = capsys.readouterr().out
    assert rc == 0 and "(LIMITED)" in out and "counts" in out and "NOTE: LIMITED MODE" in out
    assert cli(["--home", home, "--data", str(data), "calls", "verify"]) == 0
    assert cli(["--home", home, "--data", "synthetic", "calls", "grade", "--extra-data", str(data)]) == 0
    assert (Path(home) / "calls" / "scorecard.md").read_text().count("| ANT |") == 1
