"""Adapters to existing components. Real TradingAgents / Quant / Price Monitor are NOT reachable
from the build environment: these tests exercise the contracts with MOCKED transports."""
from datetime import date
from types import SimpleNamespace

from marcus_v2.core.contracts import Action, RetrievalStatus
from marcus_v2.integrations.existing import (FileQuantTransport, QuantAdapter, TradingAgentsAdapter, tool_manifest,
                                             parse_price_monitor_event)

TA_STDOUT_REPORT = """# TradingAgents: PFE on 2026-11-09

Rating: **Hold**

## Market Report

PFE above 50/200-day averages.

## News Report

Obesity trial pipeline headlines.

## Fundamentals Report

Dividend covered.

## Final Trade Decision

HOLD: trend intact, wait for a pullback.
"""


def test_tradingagents_output_parsed(tmp_path):
    md = tmp_path / "PFE-2026-11-09.md"
    md.write_text(TA_STDOUT_REPORT)
    out = f"RATING: Hold\nREPORT: {md}\n"
    ta = TradingAgentsAdapter(tmp_path, runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout=out, stderr=""))
    r = ta.parse_output("US:PFE", "PFE", date(2026, 11, 9), out)
    assert r.rating == "Hold" and r.proposed_action == Action.HOLD and "HOLD" in r.thesis
    assert {e.kind for e in r.evidence} == {"market", "news", "fundamentals"} and r.failed_feeds == ["sentiment"]


def test_tradingagents_unavailable_is_explicit(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    r = TradingAgentsAdapter(tmp_path).research("US:PFE", "PFE", date(2026, 11, 9))
    assert r.status == RetrievalStatus.FAILED and r.failed_feeds and r.thesis == ""


def test_quant_file_transport_round_trip(tmp_path):
    t = FileQuantTransport(tmp_path / "in", tmp_path / "out")
    q = QuantAdapter(t)
    first = q.request_review(["rr_1"], {"x": 1})
    assert first.verdict == "UNAVAILABLE"                       # Quant has not answered: said, not assumed
    req = next((tmp_path / "in").glob("*.json"))
    (tmp_path / "out" / req.name).write_text('{"verdict": "CHALLENGED", "findings": ["n=4"]}')

    class Replay(FileQuantTransport):
        def review(self, request):
            request["request_id"] = req.stem
            return super().review(request)
    again = QuantAdapter(Replay(tmp_path / "in", tmp_path / "out")).request_review(["rr_1"], {"x": 1})
    assert again.verdict == "CHALLENGED" and again.findings == ["n=4"]


def test_price_monitor_contracts():
    e = parse_price_monitor_event({"event_id": "pm-9", "instrument_id": "US:BAC", "type": "trigger_hit", "level": "50.71",
                                   "occurred_at": "2026-11-09T21:10:00+00:00"})
    assert e.payload == {"level": "50.71"} and e.source == "price_monitor"
    a = parse_price_monitor_event({"line": "ALERT BAC last $49.41: at or below your stop $48.50 (exit)",
                                   "occurred_at": "2026-11-09T21:10:00+00:00"})
    assert a.instrument_id == "US:BAC" and a.payload["price"] == "49.41"


def test_tool_manifest_is_paper_only():
    names = [t["name"] for t in tool_manifest()]
    assert "marcus_new_opportunity" in names and not any("live" in n or "broker" in n for n in names)


def test_tradingagents_accepts_alias_key(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MARCUS_ANTHROPIC_API_KEY", "test-not-a-real-key")
    import marcus_v2.integrations.existing as ex
    monkeypatch.setattr(ex, "TA_PYTHON", tmp_path)          # pretend the venv exists
    ok, why = TradingAgentsAdapter(tmp_path).available()
    assert ok, why
