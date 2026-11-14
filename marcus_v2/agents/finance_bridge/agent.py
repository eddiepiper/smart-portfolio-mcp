"""Finance Bridge agent: Marcus <-> Anthropic financial-services agents.

prepare_data_pack -> the upstream Earnings Reviewer / Market Researcher (run by Claude
with the vendored skills) work from this pack instead of FactSet/Daloopa ->
ingest_output verifies their JSON sidecar against the filings and hands the result
to the Research Challenger. Output is supplementary research only: it never replaces
TradingAgents, never carries a rating or an action, and is stored with its
verification status so Marcus can see exactly how much of it was sourced.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from ...core.contracts import QualityStatus, utcnow
from ...integrations import fsi_adapter as fsi
from ..base import Agent


class FinanceBridge(Agent):
    agent_id = "finance_bridge"

    def __init__(self, rt, mi, dg, rc=None, sec_offline_dir: Optional[str] = None, clock=utcnow):
        super().__init__(rt)
        self.mi, self.dg, self.rc, self.clock = mi, dg, rc, clock
        self.sec_offline_dir = sec_offline_dir
        self.dir = rt.home / "fsi"

    def register(self):
        self.tool("prepare_data_pack", self.prepare_data_pack, "read_market_data", timeout_s=300)
        self.tool("ingest_output", self.ingest_output, "read_research", timeout_s=180)
        return self

    def prepare_data_pack(self, instrument_id: str, as_of: Optional[date] = None) -> dict:
        inst = self.mi.get_instrument_metadata(instrument_id)
        ticker = inst.symbol
        as_of = as_of or self.clock().date()
        mkt = self.mi.get_market_context(instrument_id, requesting_agent="finance_bridge")
        self.dg.validate_report(mkt, purpose="research")
        if mkt.data_quality_status not in (QualityStatus.VALID, QualityStatus.WARNING):
            mkt = None                                    # rejected prices are not offered to the agents
        sec = fsi.sec_quarters(ticker, offline_dir=self.sec_offline_dir)
        pack = fsi.build_data_pack(ticker, instrument_id, market_report=mkt, sec=sec, as_of=as_of)
        d = self.dir / ticker / pack["pack_id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "data_pack.json").write_text(json.dumps(pack, indent=2, default=str))
        (d / "data_pack.md").write_text(fsi.pack_markdown(pack))
        (d / "OUTPUT_CONTRACT.json").write_text(json.dumps(fsi.FsiOutput.model_json_schema(), indent=2))
        self.rt.evidence.put(pack, "fsi_data_pack", obj_id=pack["pack_id"])
        return {"pack_id": pack["pack_id"], "dir": str(d), "sources": len(pack["sources"]),
                "figures": len(pack["figures"]), "unavailable": pack["unavailable"]}

    def ingest_output(self, sidecar_path: str, note_path: Optional[str] = None) -> dict:
        raw = json.loads(Path(sidecar_path).read_text())
        try:
            out = fsi.FsiOutput.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - malformed agent output is reported, not repaired
            return {"status": "REJECTED", "issues": [{"severity": "error", "check": "contract", "message": str(exc)[:500]}]}
        if not self.rt.evidence.exists(out.pack_id):
            return {"status": "REJECTED", "issues": [{"severity": "error", "check": "pack_identity",
                                                      "message": f"unknown data pack {out.pack_id}"}]}
        pack = self.rt.evidence.get(out.pack_id)
        note_text = Path(note_path or out.note_path).read_text() if (note_path or out.note_path) and Path(note_path or out.note_path).exists() else ""
        ver = fsi.verify(out, pack, note_text)
        result = {"status": ver["status"], "verification": ver, "agent": out.agent, "ticker": out.ticker}
        if ver["status"] != "REJECTED":
            rr = fsi.to_research_report(out, pack, ver, pack["instrument_id"])
            self.rt.evidence.put(rr, "research_report")
            result["research_report"] = rr.report_id
            if self.rc is not None:
                ctx = self.mi.get_market_context(pack["instrument_id"], requesting_agent="finance_bridge")
                self.dg.validate_report(ctx, purpose="research")
                ch = self.rc.review_investment_thesis(rr, ctx)
                result["challenge_report"] = ch.report_id
                result["challenge"] = {"unsupported_claims": ch.unsupported_claims,
                                       "contradictions": [i.message for i in ch.contradictory_evidence],
                                       "confidence": ch.confidence}
        self.rt.evidence.put(result, "fsi_ingest_result", obj_id=f"fsiin_{out.pack_id}")
        self.rt.audit.log_event("fsi_ingest", "-", pack_id=out.pack_id, status=ver["status"], agent=out.agent)
        return result
