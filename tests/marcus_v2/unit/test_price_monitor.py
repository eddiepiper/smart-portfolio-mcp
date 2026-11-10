import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from marcus_v2.agents.data_guardian.agent import DataGuardian
from marcus_v2.agents.market_intelligence.agent import MarketIntelligence
from marcus_v2.agents.price_monitor.agent import PriceMonitor, WatchlistError
from marcus_v2.integrations.openbb_adapter import FixtureProvider

FIX = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 11, 10, 7, 30, tzinfo=timezone.utc)


@pytest.fixture
def pm(rt):
    mi = MarketIntelligence(rt, providers={"fixture": FixtureProvider(FIX / "bars.json")}, clock=lambda: NOW)
    dg = DataGuardian(rt, clock=lambda: NOW)
    return PriceMonitor(rt, mi, dg, clock=lambda: NOW).register()


def test_watchlist_changes_need_owner(pm):
    with pytest.raises(WatchlistError):
        pm.add_rule({"instrument_id": "US:BAC", "type": "stop", "level": "48.50"}, "marcus")
    with pytest.raises(KeyError):
        pm.add_rule({"instrument_id": "US:NOPE", "type": "stop", "level": "1"}, "owner:edward")
    with pytest.raises(WatchlistError):
        pm.add_rule({"instrument_id": "US:BAC", "type": "magic", "level": "1"}, "owner:edward")


def test_rules_fire_on_final_validated_bars_only(pm):
    # BAC Nov 9 (last final bar at NOW): H 51.01 L 49.73 C 50.55; previous close 49.42
    for r in [{"type": "stop", "level": "48.50"}, {"type": "target", "level": "51.00"},
              {"type": "daily_close_above", "level": "50.71"}, {"type": "daily_close_above", "level": "50.00"},
              {"type": "pct_move", "pct": "2"}]:
        pm.add_rule({"instrument_id": "US:BAC", **r}, "owner:edward")
    pm.add_rule({"instrument_id": "COMEX:GC", "type": "daily_close_above", "level": "3994"}, "owner:edward")
    out = pm.scan()
    fired = {e["rule"] for e in out["events"]}
    assert "US:BAC:target:51.00" in fired                       # high 51.01 >= 51.00
    assert "US:BAC:daily_close_above:50.00" in fired            # close 50.55 > 50.00
    assert "US:BAC:pct_move:2" in fired                      # +2.3% vs 49.42
    assert "US:BAC:stop:48.50" not in fired and "US:BAC:daily_close_above:50.71" not in fired
    gc = [c for c in out["checked"] if c["instrument_id"] == "COMEX:GC"][0]
    assert gc["bar_date"] == "2026-11-09"                       # today's provisional gold bar ignored
    assert all(e["event_id"].endswith(e["bar_date"]) for e in out["events"])


def test_scan_events_feed_workflow_a_without_duplicates(rt, pm):
    from marcus_v2.integrations.openbb_adapter import FixtureProvider
    from marcus_v2.marcus.team import build_team
    pm.add_rule({"instrument_id": "US:BAC", "type": "target", "level": "51.00"}, "owner:edward")
    marcus = build_team(rt, providers={"fixture": FixtureProvider(FIX / "bars.json")}, clock=lambda: NOW)
    first = marcus.workflow_a_daily_monitoring(pm.scan()["events"], {"US:BAC"})
    second = marcus.workflow_a_daily_monitoring(pm.scan()["events"], {"US:BAC"})
    assert first["new"] == 1 and second["new"] == 0 and len(second["duplicates_ignored"]) == 1


def test_import_trader_alerts(pm, tmp_path):
    p = tmp_path / "alerts.json"
    p.write_text(json.dumps({"rules": [
        {"id": "bac-stop-48.50", "symbol": "BAC", "type": "stop", "price": 48.5, "note": "exit on a daily CLOSE below 48.50"},
        {"id": "bac-target-54.50", "symbol": "BAC", "type": "target", "price": 54.5, "note": "target"}]}))
    rules = pm.import_trader_alerts(p, "owner:edward")
    assert {r["type"] for r in rules} == {"daily_close_below", "target"}
    assert len(pm.import_trader_alerts(p, "owner:edward")) == 2 and len(pm.list_rules()) == 2    # idempotent


def test_bad_data_means_no_events(rt, pm):
    pm.add_rule({"instrument_id": "US:BAC", "type": "stop", "level": "48.50"}, "owner:edward")
    late = PriceMonitor(rt, pm.mi, pm.dg, clock=lambda: NOW)
    out = late.scan(as_of=date(2026, 9, 30))                    # stale window -> rejected for decisions
    assert out["events"] == [] and out["problems"]
