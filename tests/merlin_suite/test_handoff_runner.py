import json
import subprocess
from pathlib import Path

import pytest

from merlin import handoff, runner
from merlin.contracts import MerlinReport, Request
from merlin_fixtures import make_report

ROOT = Path(__file__).resolve().parents[2]


def test_marcus_report_has_no_rating_or_action():
    rr = handoff.to_marcus_research_report(MerlinReport.model_validate(make_report()))
    assert rr.source == "merlin" and rr.rating is None and rr.proposed_action is None
    assert rr.instrument_id == "US:SYNTH" and rr.claims and rr.claims[0].evidence_refs


def test_handoff_refuses_failed_report():
    bad = MerlinReport.model_validate(make_report(executive_summary="Buy now."))
    with pytest.raises(handoff.HandoffRefused):
        handoff.to_marcus_research_report(bad)
    with pytest.raises(handoff.HandoffRefused):
        handoff.quant_challenge_packet(bad)


def test_quant_packet_contents():
    p = handoff.quant_challenge_packet(MerlinReport.model_validate(make_report()))
    assert p["packet"] == "merlin-quant-challenge/v1"
    assert p["model_inputs"]["valuation"]["discount_rate"] == 0.10
    assert "rows" not in p["model_outputs"] and p["sensitivity"]["centre_equals_base"]


def test_ingest_into_marcus(tmp_path):
    from marcus_v2.core.llm import MockLLM
    from marcus_v2.core.runtime import build_runtime
    rt = build_runtime(tmp_path / "home", llm=MockLLM(cache_dir=tmp_path / "llm"), environ={})
    out = handoff.ingest_into_marcus(MerlinReport.model_validate(make_report()), rt)
    assert rt.evidence.exists(out["research_report"]) and rt.evidence.exists(out["quant_packet"])
    assert rt.evidence.get(out["research_report"])["rating"] is None


def _fake_cli(raw, rc=0):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        lines = [{"type": "system", "subtype": "init", "plugins": [{"name": "equity-research"}]},
                 {"type": "assistant", "message": {"content": [
                     {"type": "tool_use", "name": "Skill", "input": {"skill": "equity-research:earnings-analysis"}},
                     {"type": "tool_use", "name": "WebFetch", "input": {"url": "https://example.com"}},
                     {"type": "tool_use", "name": "Bash", "input": {"command": "python -m merlin calc dcf --inputs '{}'"}}]}},
                 {"type": "result", "is_error": False, "subtype": "success", "structured_output": raw,
                  "total_cost_usd": 0.5, "num_turns": 7}]
        return subprocess.CompletedProcess(cmd, rc, stdout="\n".join(json.dumps(x) for x in lines), stderr="")
    return run, calls


def test_runner_command_is_locked_down():
    req = Request(task_type="question", subject="X", question="q", as_of="2026-11-14")
    cmd = runner.build_command(req)
    assert cmd[:2] == ["claude", "-p"] and "--json-schema" in cmd and "--append-system-prompt" in cmd
    assert "dontAsk" in cmd and "--no-session-persistence" in cmd
    assert not any(t == "Bash" for t in cmd)                          # only scoped Bash(python -m merlin:*)
    assert "Bash(curl:*)" in cmd
    assert "--strict-mcp-config" in cmd and "--mcp-config" not in cmd
    assert runner.build_command(req, mcp_config="dex/merlin.mcp.json")[-2:] == ["--mcp-config", "dex/merlin.mcp.json"] \
        or "dex/merlin.mcp.json" in runner.build_command(req, mcp_config="dex/merlin.mcp.json")


def test_runner_validates_and_writes(tmp_path):
    fake, calls = _fake_cli(make_report())
    req = Request(task_type="financial_model", subject="SYNTH", question="q", as_of="2026-11-14")
    res = runner.run(req, tmp_path / "run", runner=fake)
    assert res["status"] in ("PASS", "WARN")
    assert (tmp_path / "run" / "report.md").exists() and (tmp_path / "run" / "validation.json").exists()
    fake2, _ = _fake_cli(make_report(executive_summary="Rating: Buy."))
    res2 = runner.run(req, tmp_path / "run2", runner=fake2)
    assert res2["status"] == "FAIL" and not (tmp_path / "run2" / "report.md").exists()


def test_cli_end_to_end(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(make_report()))
    for args in (["validate", str(p)], ["reproduce", str(p)], ["render", str(p), "--out", str(tmp_path / "r.md")],
                 ["handoff", str(p), "--to", "quant"], ["schema"]):
        r = subprocess.run(["python3", "-m", "merlin", *args], cwd=ROOT, capture_output=True, text=True)
        assert r.returncode == 0, (args, r.stderr)
    r = subprocess.run(["python3", "-m", "merlin", "calc", "tieout", "--inputs",
                        '{"revenue": 96221, "opex": 8408, "operating_income": 63734, "reported_gross_margin": 0.75}'],
                       cwd=ROOT, capture_output=True, text=True)
    assert json.loads(r.stdout)["result"]["ok"] is True


def test_runner_rejects_unobserved_capability_claims(tmp_path):
    raw = make_report()
    raw["capabilities_used"].append({"name": "model-builder:dcf-model", "kind": "fsi_plugin_skill", "genuine_execution": True})
    fake, _ = _fake_cli(raw)
    req = Request(task_type="financial_model", subject="SYNTH", question="q", as_of="2026-11-14")
    res = runner.run(req, tmp_path / "r", runner=fake)
    assert res["status"] == "FAIL"
    assert any(i["check"] == "unverified_capability_claim" for i in res["validation"]["issues"])
    raw2 = make_report()
    raw2["capabilities_used"].append({"name": "equity-research:earnings-analysis", "kind": "fsi_plugin_skill",
                                      "genuine_execution": True})
    fake2, _ = _fake_cli(raw2)
    res2 = runner.run(req, tmp_path / "r2", runner=fake2)
    assert res2["status"] in ("PASS", "WARN") and res2["skills_observed"] == ["equity-research:earnings-analysis"]
    assert res2["plugins_loaded"] == ["equity-research"]


def test_cli_accepts_long_inline_json():
    big = {"current": {f"k{i}": i + 1 for i in range(60)}, "prior": {f"k{i}": i + 2 for i in range(60)},
           "keys": [f"k{i}" for i in range(60)]}
    r = subprocess.run(["python3", "-m", "merlin", "calc", "compare", "--inputs", json.dumps(big)],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_capability_name_matching():
    assert runner._claimed_ran("dcf-model (Anthropic financial-services skill)", {"dcf-model"})
    assert runner._claimed_ran("model-builder:dcf-model", {"dcf-model"})
    assert not runner._claimed_ran("dcf-model", {"dcf"})
    assert not runner._claimed_ran("earnings-analysis", {"dcf-model"})


def test_merlin_cli_claims_checked_against_bash_trace(tmp_path):
    raw = make_report()
    raw["capabilities_used"].append({"name": "python -m merlin validate", "kind": "merlin_skill", "genuine_execution": True})
    fake, _ = _fake_cli(raw)
    req = Request(task_type="financial_model", subject="SYNTH", question="q", as_of="2026-11-14")
    res = runner.run(req, tmp_path / "r", runner=fake)
    assert res["status"] == "FAIL"                     # trace has `merlin calc` only, no `merlin validate`
    raw2 = make_report()                               # merlin_calc claim is matched by the traced calc call
    fake2, _ = _fake_cli(raw2)
    assert runner.run(req, tmp_path / "r2", runner=fake2)["status"] in ("PASS", "WARN")
