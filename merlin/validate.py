"""Deterministic checks on a `merlin-research/v1` report.

PASS: no errors and no warnings. WARN: usable, with warnings listed. FAIL: must not be handed
to Marcus or Quant. The checks enforce the research standards in merlin/spec/MERLIN.md:
cited figures, honest evidence status, no live-data claims, no invented confidence numbers,
no trade instructions, reproducible models and an honest overall quality status.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from . import calc
from .contracts import STRENGTH, EvidenceStatus, MerlinReport, QualityStatus

# freshness rules: how old a source may be (relative to request.as_of) before it is STALE
FRESHNESS_DAYS = {"market_data": 3, "news": 30, "press_release": 200, "filing": 200, "transcript": 200,
                  "statistics": 120, "central_bank": 120, "industry_body": 200, "dataset": 400,
                  "connector": 3}

CONFIDENCE_PATTERNS = [
    r"\b\d{1,3}(\.\d+)?\s?%\s+(confidence|confident|probability|chance|likelihood|certain)",
    r"\b(confidence|probability|likelihood)\s*(level|score)?\s*(of|:|=)\s*\d{1,3}(\.\d+)?\s?%",
    r"\b\d{1,3}(\.\d+)?\s?%\s+(likely|sure)\b",
]
TRADE_PATTERNS = [
    r"\b(buy|sell|short)\s+now\b", r"\bplace (an? )?(order|trade)\b", r"\bsubmit (an? )?order\b",
    r"\bwe (recommend|advise) (buying|selling|shorting|you buy|you sell)\b",
    r"\b(rating|recommendation)\s*[:=]\s*(strong )?(buy|sell|overweight|underweight|outperform|underperform)\b",
    r"\b(allocate|put|invest)\s+\d+(\.\d+)?\s?%\s+of\s+(the|your)\s+(portfolio|capital)\b",
    r"\bset (a )?stop[- ]loss at\b", r"\bexecute (the|this|a) trade\b",
]
INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|prompts)", r"disregard (the )?(system|previous)",
    r"\byou are now\b", r"\bcurl\s+https?://", r"rm\s+-rf", r"\bapi[_ -]?key\b",
]


def _texts(r: MerlinReport) -> list[tuple[str, str]]:
    out = [("executive_summary", r.executive_summary), ("quality_notes", r.quality_notes)]
    out += [(f"key_findings[{f.rank}]", f.finding) for f in r.key_findings]
    out += [("risks", x.risk) for x in r.risks] + [("counterarguments", x) for x in r.counterarguments]
    out += [("unknowns", x) for x in r.unknowns] + [("implications", x.text) for x in r.implications]
    out += [("scenarios", s.narrative) for s in r.scenarios] + [("limitations", x) for x in r.limitations]
    return out


def suggested_quality(r: MerlinReport) -> QualityStatus:
    used = {e for f in r.figures for e in f.evidence_ids}
    ev = {e.evidence_id: e for e in r.evidence}
    statuses = [ev[e].status for e in used if e in ev]
    if not r.figures and not statuses:
        return QualityStatus.PROVISIONAL
    if any(s == EvidenceStatus.FAILED for s in statuses):
        return QualityStatus.FAILED
    fig = [f.status for f in r.figures]
    if all(s == EvidenceStatus.VERIFIED for s in fig) and all(s == EvidenceStatus.VERIFIED for s in statuses):
        return QualityStatus.VERIFIED
    if any(s in (EvidenceStatus.UNKNOWN, EvidenceStatus.STALE) for s in fig + statuses):
        return QualityStatus.INCOMPLETE
    return QualityStatus.PROVISIONAL


QUALITY_ORDER = {QualityStatus.FAILED: 0, QualityStatus.INCOMPLETE: 1, QualityStatus.PROVISIONAL: 2,
                 QualityStatus.VERIFIED: 3}


def validate(raw: Any, *, today: date | None = None) -> dict:
    issues: list[dict] = []

    def err(check, msg):
        issues.append({"severity": "error", "check": check, "message": msg})

    def warn(check, msg):
        issues.append({"severity": "warning", "check": check, "message": msg})

    try:
        r = raw if isinstance(raw, MerlinReport) else MerlinReport.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - schema failures are reported, not repaired
        return {"status": "FAIL", "issues": [{"severity": "error", "check": "schema", "message": str(exc)[:1500]}]}

    as_of = r.request.as_of
    ev = {e.evidence_id: e for e in r.evidence}
    figs = {f.figure_id: f for f in r.figures}
    if len(ev) != len(r.evidence):
        err("duplicate_id", "evidence ids are not unique")
    if len(figs) != len(r.figures):
        err("duplicate_id", "figure ids are not unique")

    # evidence register
    for e in r.evidence:
        if e.status == EvidenceStatus.VERIFIED:
            missing = [k for k in ("url", "document_date", "retrieved_at") if getattr(e, k) is None]
            if missing:
                err("evidence_provenance", f"{e.evidence_id} is VERIFIED but has no {', '.join(missing)}")
        if e.kind == "synthetic" and e.status != EvidenceStatus.ASSUMPTION:
            err("synthetic_as_fact", f"{e.evidence_id} is synthetic data but marked {e.status.value}")
        if e.document_date and e.document_date > as_of + timedelta(days=1):
            err("future_document", f"{e.evidence_id} is dated {e.document_date}, after the as-of date {as_of}")
        limit = FRESHNESS_DAYS.get(e.kind)
        if limit and e.document_date and e.status in (EvidenceStatus.VERIFIED, EvidenceStatus.PROVISIONAL):
            age = (as_of - e.document_date).days
            if age > limit:
                warn("stale_source", f"{e.evidence_id} ({e.kind}) is {age} days old (limit {limit}); mark it STALE or say why it is still current")

    # figures
    for f in r.figures:
        cited = [ev.get(i) for i in f.evidence_ids]
        unknown = [i for i, c in zip(f.evidence_ids, cited) if c is None]
        if unknown:
            err("phantom_evidence", f"{f.figure_id} ({f.metric}) cites unknown evidence {unknown}")
            continue
        if any(c.status == EvidenceStatus.FAILED for c in cited):
            err("failed_evidence_used", f"{f.figure_id} ({f.metric}) rests on FAILED evidence")
        strongest = max(STRENGTH[c.status] for c in cited)
        if STRENGTH[f.status] > strongest:
            err("status_inflation", f"{f.figure_id} ({f.metric}) is {f.status.value} but its evidence is at most "
                                    f"{max(cited, key=lambda c: STRENGTH[c.status]).status.value}")
        if f.as_live:
            live_ok = any(c.kind in ("connector", "market_data") and c.retrieved_at and
                          (r.created_at - c.retrieved_at) <= timedelta(minutes=15) for c in cited)
            if not live_ok:
                err("historical_as_live", f"{f.figure_id} ({f.metric}) is presented as live without a quote retrieved within 15 minutes")
        if f.derived_by and not f.derived_by.startswith("merlin.calc"):
            warn("non_deterministic_derivation", f"{f.figure_id} derived by {f.derived_by}, not merlin.calc")
    for fd in r.key_findings:
        bad = [i for i in fd.figure_ids if i not in figs] + [i for i in fd.evidence_ids if i not in ev]
        if bad:
            err("phantom_reference", f"finding {fd.rank} cites unknown ids {bad}")
        if not fd.figure_ids and not fd.evidence_ids:
            warn("uncited_finding", f"finding {fd.rank} cites no figure or evidence")
    for x in r.risks:
        bad = [i for i in x.evidence_ids if i not in ev]
        if bad:
            err("phantom_reference", f"risk cites unknown ids {bad}")

    # language rules
    for where, text in _texts(r):
        for pat in CONFIDENCE_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                err("invented_confidence", f"{where}: numeric confidence/probability language /{pat}/")
        for pat in TRADE_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                err("trade_instruction", f"{where}: trade-instruction or rating language /{pat}/")
        for pat in INJECTION_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                err("injection_text", f"{where}: instruction-like text copied from a source /{pat}/")

    # model reproducibility
    if r.model and r.model.type == "dcf":
        try:
            rep = calc.reproduce(r.model.model_dump())
        except Exception as exc:  # noqa: BLE001
            rep = {"ok": False, "differences": {"error": str(exc)}}
        if rep.get("ok") is False:
            err("model_not_reproducible", f"stored DCF outputs differ from merlin.calc: {rep.get('differences')}")
        elif rep.get("ok") is None:
            err("model_not_reproducible", "DCF model block lacks inputs/outputs")
        if not r.model.assumptions:
            err("model_assumptions", "DCF model lists no assumptions")
        if not r.model.limitations:
            warn("model_limitations", "model lists no limitations")
        if r.model.sensitivity and r.model.sensitivity.get("centre_equals_base") is False:
            err("sensitivity_centre", "sensitivity table centre cell is not the base case")
    if r.request.task_type in ("financial_model", "valuation_check") and not r.scenarios:
        warn("scenarios_missing", "valuation work without bull/base/bear scenarios")

    # quality status honesty
    sug = suggested_quality(r)
    if QUALITY_ORDER[r.quality_status] > QUALITY_ORDER[sug]:
        err("quality_inflation", f"report claims {r.quality_status.value} but its evidence supports at most {sug.value}")

    # capabilities: genuine plugin execution must be named precisely
    if not any(c.kind == "merlin_calc" for c in r.capabilities_used) and any(f.derived_by for f in r.figures):
        warn("capabilities", "derived figures present but merlin_calc not listed in capabilities_used")

    errors = [i for i in issues if i["severity"] == "error"]
    status = "FAIL" if errors else ("WARN" if issues else "PASS")
    return {"status": status, "report_id": r.report_id, "quality_status": r.quality_status.value,
            "suggested_quality_status": sug.value, "figures": len(r.figures), "evidence": len(r.evidence),
            "issues": issues}
