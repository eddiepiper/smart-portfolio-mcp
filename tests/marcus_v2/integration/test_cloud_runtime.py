"""Cloud runtime: git-backed state persistence across ephemeral sessions, and Marcus's daily/weekly runs."""
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from marcus_v2.core import capital, state_sync
from marcus_v2.core.contracts import LedgerEvent, LedgerEventType
from marcus_v2.core.ledger import Ledger
from marcus_v2.core.mandate import default_v2_mandate
from marcus_v2.core.runtime import build_runtime
from marcus_v2.core.llm import MockLLM

FIX = Path(__file__).resolve().parents[1] / "fixtures"
ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def remote(tmp_path):
    r = tmp_path / "marcus-state.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(r)], check=True)
    return str(r)


def _contribute(home, key, amt):
    Ledger(Path(home) / "ledger" / "marcus-paper.jsonl").append(LedgerEvent(
        event_type=LedgerEventType.CONTRIBUTION, portfolio_id="marcus-paper", currency="SGD", amount=Decimal(amt),
        event_at=NOW, idempotency_key=key))


def test_state_survives_between_sessions(tmp_path, remote):
    a = tmp_path / "session_a"
    assert state_sync.pull(a, remote).action == "initialised"
    _contribute(a, "c1", "500")
    assert state_sync.push(a, "session A").action == "pushed"
    b = tmp_path / "session_b"                                     # a brand-new cloud container
    assert state_sync.pull(b, remote).action == "cloned"
    assert (b / "ledger" / "marcus-paper.jsonl").read_text() == (a / "ledger" / "marcus-paper.jsonl").read_text()
    _contribute(b, "c2", "19500")
    state_sync.push(b, "session B")
    state_sync.pull(a, remote)
    assert len(Ledger(a / "ledger" / "marcus-paper.jsonl").events()) == 2
    assert state_sync.push(a, "noop").action == "unchanged"


def test_diverged_state_is_never_auto_merged(tmp_path, remote):
    a, b = tmp_path / "a", tmp_path / "b"
    state_sync.pull(a, remote); _contribute(a, "c1", "500"); state_sync.push(a, "a1")
    state_sync.pull(b, remote)
    _contribute(a, "c2", "1"); state_sync.push(a, "a2")
    _contribute(b, "c3", "2")
    with pytest.raises(state_sync.StateSyncError):
        state_sync.push(b, "b-conflict")                           # remote moved on: refused, nothing overwritten
    with pytest.raises(state_sync.StateSyncError):
        state_sync.pull(b, remote)                                 # diverged: manual resolution required


def test_tampered_ledger_blocks_sync(tmp_path, remote):
    a = tmp_path / "a"
    state_sync.pull(a, remote); _contribute(a, "c1", "500"); _contribute(a, "c2", "100")
    p = a / "ledger" / "marcus-paper.jsonl"
    p.write_text(p.read_text().replace('"amount":"100"', '"amount":"900"'))
    with pytest.raises(state_sync.StateSyncError):
        state_sync.push(a, "tampered")


def test_refuses_to_overwrite_unsynced_local_state(tmp_path, remote):
    a = tmp_path / "a"
    a.mkdir(); (a / "something.json").write_text("{}")
    with pytest.raises(state_sync.StateSyncError):
        state_sync.pull(a, remote)


def test_unreachable_repo_never_starts_blank_state(tmp_path):
    a = tmp_path / "a"
    missing = str(tmp_path / "no-such-repo.git")                   # stands in for a 403 / network failure
    with pytest.raises(state_sync.StateSyncError, match="cannot reach state repo"):
        state_sync.pull(a, missing)
    assert not a.exists() or not any(a.iterdir())                  # nothing initialised


def test_unreachable_repo_blocks_fast_forward(tmp_path, remote):
    a = tmp_path / "a"
    state_sync.pull(a, remote); _contribute(a, "c1", "500"); state_sync.push(a, "a1")
    subprocess.run(["git", "-C", str(a), "remote", "set-url", "origin", str(tmp_path / "gone.git")], check=True)
    with pytest.raises(state_sync.StateSyncError, match="cannot reach state repo"):
        state_sync.pull(a, remote)                                 # never reported as "fast-forwarded"


def test_daily_and_weekly_run(tmp_path):
    from marcus_v2.agents.price_monitor.agent import PriceMonitor
    from marcus_v2.integrations.openbb_adapter import FixtureProvider
    from marcus_v2.marcus import runner
    from marcus_v2.marcus.team import build_team
    rt = build_runtime(tmp_path / "home", llm=MockLLM(), environ={})
    rt.mandates.propose(default_v2_mandate(1)); rt.mandates.approve("marcus-paper", 1, "owner:edward")
    team = build_team(rt, providers={"fixture": FixtureProvider(FIX / "bars.json")}, clock=lambda: NOW)
    fx = team.fx_rates(date(2026, 10, 1), date(2026, 11, 9))
    events, _ = capital.plan_legacy_import(FIX / "legacy_trader", rt.portfolio_id, capital_currency="SGD", fx_rates=fx["rates"])
    capital.apply_events(rt.ledger_writer(), events, "owner:edward")
    pm = PriceMonitor(rt, team.mi, team.dg, clock=lambda: NOW).register()
    pm.add_rule({"instrument_id": "US:BAC", "type": "daily_close_below", "level": "48.50", "note": "paper stop"}, "owner:edward")
    pm.add_rule({"instrument_id": "US:BAC", "type": "target", "level": "51.00"}, "owner:edward")
    r = runner.daily_run(team, pm, date(2026, 11, 9))
    assert [e["rule"] for e in r["price_monitor"]["events"]] == ["US:BAC:target:51.00"]
    assert r["workflow_a"]["new"] == 1 and r["portfolio"]["equity"] is not None
    md = Path(r["report_path"]).read_text()
    assert "**TRIGGER** US:BAC:target:51.00" in md and "Equity: S$" in md
    assert any("TradingAgents unavailable" in x for x in r["limitations"]) or team.ta.available()[0]
    again = runner.daily_run(team, pm, date(2026, 11, 9))
    assert again["workflow_a"]["new"] == 0                          # re-running the same day creates no new decisions
    w = runner.weekly_review(team, date(2026, 11, 9))
    assert w["portfolio"]["equity"] is not None and "price_monitor" in w["telemetry"] or w["telemetry"]


@pytest.mark.skipif(os.environ.get("MARCUS_V2_LIVE") != "1", reason="live network")
def test_cli_daily_run_with_state_repo_live(tmp_path, remote):
    env = {**os.environ, "MARCUS_V2_STATE_REPO": remote}
    env.pop("ANTHROPIC_API_KEY", None)
    py = sys.executable

    def cli(home, *args):
        return subprocess.run([py, "-m", "marcus_v2", *args, "--home", str(home)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    s1 = tmp_path / "s1"
    assert cli(s1, "mandate_propose", "--owner", "owner:qa").returncode == 0
    assert cli(s1, "watch_add", "--owner", "owner:qa", "--payload", '{"instrument_id":"US:BAC","type":"daily_close_below","level":"48.50"}').returncode == 0
    r = cli(s1, "daily_run")
    assert r.returncode == 0, r.stdout + r.stderr
    s2 = tmp_path / "s2"                                           # next day's fresh cloud session
    out = cli(s2, "watch_list")
    assert out.returncode == 0 and "US:BAC:daily_close_below:48.50" in out.stdout
    assert list((s2 / "reports").glob("daily-*.md"))


def test_dry_run_never_pushes_state(tmp_path, remote, monkeypatch):
    from marcus_v2 import __main__ as cli
    monkeypatch.setenv("MARCUS_V2_STATE_REPO", remote)
    pushed = []
    monkeypatch.setattr(state_sync, "push", lambda *a, **k: pushed.append(a) or state_sync.SyncResult("pushed", "test"))
    monkeypatch.setattr(cli, "_run", lambda a, p, rt: 0)
    cli.main(["import_legacy", "--home", str(tmp_path / "h"), "--payload", '{"dry_run": true}'])
    assert pushed == []
    cli.main(["import_legacy", "--home", str(tmp_path / "h"), "--owner", "owner:edward", "--payload", "{}"])
    assert len(pushed) == 1
