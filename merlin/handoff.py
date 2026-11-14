"""Merlin -> Marcus / Quant handoff.

Merlin's output reaches the investment team only after `merlin.validate` passes (PASS or WARN):

- Marcus receives a `ResearchReport(source="merlin", rating=None, proposed_action=None)`:
  supplementary research, like the Finance Bridge. TradingAgents stays the primary engine.
- Quant receives a challenge packet: the claims, the assumptions, the model inputs and
  sensitivities, and the open questions, so it can scrutinise the conclusions independently.
  Merlin never asks Quant to agree and never routes around it.
"""
from __future__ import annotations

from typing import Optional

from .contracts import EvidenceStatus, MerlinReport, QualityStatus
from .validate import validate


class HandoffRefused(Exception):
    pass


def _checked(report: MerlinReport) -> dict:
    v = validate(report)
    if v["status"] == "FAIL":
        raise HandoffRefused(f"report {report.report_id} failed validation: "
                             + "; ".join(i["message"] for i in v["issues"] if i["severity"] == "error")[:800])
    return v


def to_marcus_research_report(report: MerlinReport):
    """Convert to Marcus V2's ResearchReport. Import is local so Merlin runs without Marcus installed."""
    from marcus_v2.core.contracts import Claim, Evidence, ResearchReport, RetrievalStatus

    v = _checked(report)
    ev_map, evidence = {}, []
    for e in report.evidence:
        x = Evidence(kind=e.kind, source=f"merlin:{e.publisher}", reference=e.url or e.evidence_id,
                     summary=f"[{e.status.value}] {e.title} ({e.document_date or 'undated'}; {e.period or 'n/a'})")
        ev_map[e.evidence_id] = x.evidence_id
        evidence.append(x)
    figs = {f.figure_id: f for f in report.figures}
    claims = []
    for f in sorted(report.key_findings, key=lambda x: x.rank):
        refs = set(f.evidence_ids) | {e for fid in f.figure_ids if fid in figs for e in figs[fid].evidence_ids}
        claims.append(Claim(text=f.finding, evidence_refs=[ev_map[r] for r in sorted(refs) if r in ev_map]))
    failed = [f"{e.title} ({e.status.value})" for e in report.evidence
              if e.status in (EvidenceStatus.FAILED, EvidenceStatus.UNKNOWN)]
    status = {QualityStatus.VERIFIED: RetrievalStatus.OK, QualityStatus.PROVISIONAL: RetrievalStatus.PARTIAL,
              QualityStatus.INCOMPLETE: RetrievalStatus.PARTIAL, QualityStatus.FAILED: RetrievalStatus.FAILED}
    thesis = (f"[Merlin {report.request.task_type}, quality {report.quality_status.value}, validation {v['status']}] "
              f"{report.executive_summary}\nRisks: " + "; ".join(r.risk for r in report.risks))
    return ResearchReport(source="merlin", instrument_id=report.request.instrument_id or f"RESEARCH:{report.request.subject}",
                          as_of=report.request.as_of, rating=None, thesis=thesis, claims=claims, evidence=evidence,
                          failed_feeds=failed, proposed_action=None, status=status[report.quality_status])


def quant_challenge_packet(report: MerlinReport) -> dict:
    """What Quant needs to challenge Merlin's work independently. JSON-able."""
    v = _checked(report)
    m = report.model
    return {
        "packet": "merlin-quant-challenge/v1",
        "report_id": report.report_id,
        "subject": report.request.subject,
        "as_of": str(report.request.as_of),
        "validation": {"status": v["status"], "issues": v["issues"]},
        "quality_status": report.quality_status.value,
        "claims": [{"rank": f.rank, "text": f.finding, "significance": f.significance,
                    "figures": [report_fig.model_dump(mode="json") for report_fig in report.figures
                                if report_fig.figure_id in f.figure_ids]} for f in report.key_findings],
        "assumptions": [a.model_dump(mode="json") for a in (m.assumptions if m else [])],
        "model_inputs": m.inputs if m else None,
        "model_outputs": {k: v2 for k, v2 in (m.outputs if m else {}).items() if k != "rows"},
        "sensitivity": m.sensitivity if m else None,
        "scenarios": [s.model_dump(mode="json") for s in report.scenarios],
        "unknowns": report.unknowns,
        "questions_for_quant": [
            "Which assumption, moved within a plausible range, changes the conclusion?",
            "Do the cited figures reconcile with the original sources?",
            "Is any conclusion stronger than the evidence status allows?",
        ] + [f"Challenge: {c}" for c in report.counterarguments],
        "reproduce": "python -m merlin reproduce <report.json>",
    }


def ingest_into_marcus(report: MerlinReport, rt, team=None) -> dict:
    """Store the converted report in Marcus's evidence store and, if a team is given, run the
    Research Challenger on it. Returns ids. Never writes a recommendation."""
    rr = to_marcus_research_report(report)
    rt.evidence.put(rr, "research_report")
    out = {"research_report": rr.report_id, "source": "merlin", "status": rr.status.value}
    if team is not None and report.request.instrument_id:
        ctx = team.mi.get_market_context(report.request.instrument_id, requesting_agent="merlin")
        team.dg.validate_report(ctx, purpose="research")
        ch = team.rc.review_investment_thesis(rr, ctx)
        out["challenge_report"] = ch.report_id
        out["challenge"] = {"unsupported_claims": ch.unsupported_claims, "confidence": ch.confidence,
                            "missing_information": ch.missing_information}
    packet = quant_challenge_packet(report)
    rt.evidence.put(packet, "merlin_quant_packet", obj_id=f"mqp_{report.report_id}")
    out["quant_packet"] = f"mqp_{report.report_id}"
    rt.audit.log_event("merlin_ingest", "-", report_id=report.report_id, quality=report.quality_status.value)
    return out
