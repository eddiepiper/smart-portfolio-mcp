"""Adapters to the EXISTING components: TradingAgents, Quant, Price Monitor, Grok.

TradingAgents: real adapter over the repo's pinned runner
(.claude/skills/stock-analysis/run_tradingagents.py). Runs only when an LLM key
is configured; otherwise returns a FAILED ResearchReport that says so.
Status: NOT VERIFIED end to end (no LLM key in the build environment).

Quant / Price Monitor / Grok: the real components live on the Mac mini and were
not reachable. These adapters define the JSON contract they exchange and a
file/CLI transport any runtime can drive. Status: NOT VERIFIED against the real
systems; MOCKED in tests.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional, Protocol

from ..core.contracts import (Action, Claim, Evidence, MarketEvent, QuantReview, ResearchReport, RetrievalStatus,
                              new_id, utcnow)

REPO = Path(__file__).resolve().parents[2]
TA_RUNNER = REPO / ".claude/skills/stock-analysis/run_tradingagents.py"
TA_PYTHON = Path(os.environ.get("TRADINGAGENTS_PYTHON", Path.home() / ".tradingagents-src/venv/bin/python"))
RATING_TO_ACTION = {"Buy": Action.BUY, "Overweight": Action.BUY, "Hold": Action.HOLD,
                    "Underweight": Action.REDUCE, "Sell": Action.EXIT}


# --------------------------------------------------------------------- TradingAgents
class TradingAgentsAdapter:
    """Primary research. Marcus V2 never replaces it; Research Challenger only challenges it."""

    def __init__(self, out_dir: str | Path, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self.out_dir = Path(out_dir)
        self.runner = runner

    def available(self) -> tuple[bool, str]:
        if not TA_PYTHON.exists():
            return False, f"TradingAgents virtualenv not found at {TA_PYTHON}: run setup_tradingagents.sh"
        if not any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GOOGLE_API_KEY")):
            return False, "no LLM API key configured for TradingAgents"
        return True, "ok"

    def research(self, instrument_id: str, symbol: str, as_of: date, timeout_s: int = 1800) -> ResearchReport:
        ok, why = self.available()
        if not ok:
            return ResearchReport(instrument_id=instrument_id, as_of=as_of, thesis="", status=RetrievalStatus.FAILED,
                                  failed_feeds=[f"tradingagents: {why}"])
        cmd = [str(TA_PYTHON), str(TA_RUNNER), symbol, as_of.isoformat(), "--out", str(self.out_dir)]
        proc = self.runner(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=str(REPO))
        if proc.returncode != 0:
            return ResearchReport(instrument_id=instrument_id, as_of=as_of, thesis="", status=RetrievalStatus.FAILED,
                                  failed_feeds=[f"tradingagents exit {proc.returncode}: {proc.stderr[-400:]}"])
        return self.parse_output(instrument_id, symbol, as_of, proc.stdout)

    def parse_output(self, instrument_id: str, symbol: str, as_of: date, stdout: str) -> ResearchReport:
        rating = re.search(r"^RATING:\s*(\S+)", stdout, re.M)
        path = re.search(r"^REPORT:\s*(.+)$", stdout, re.M)
        rating = rating.group(1) if rating else None
        md = Path(path.group(1).strip()) if path else None
        text = md.read_text() if md and md.exists() else stdout
        sections = dict(re.findall(r"^## (.+?)\n(.*?)(?=^## |\Z)", text, re.M | re.S))
        evidence, failed = [], []
        kinds = {"Market Report": "market", "Sentiment Report": "sentiment", "News Report": "news",
                 "Fundamentals Report": "fundamentals"}
        for title, kind in kinds.items():
            body = sections.get(title, "").strip()
            if body:
                evidence.append(Evidence(kind=kind, source="tradingagents", reference=f"{md or 'stdout'}#{title}", summary=body[:500]))
            else:
                failed.append(kind)
        thesis = (sections.get("Final Trade Decision") or sections.get("Trader Investment Plan") or "").strip()
        return ResearchReport(instrument_id=instrument_id, as_of=as_of, rating=rating, thesis=thesis[:4000],
                              claims=[Claim(text=thesis[:300], evidence_refs=[e.evidence_id for e in evidence])] if thesis else [],
                              evidence=evidence, failed_feeds=failed, proposed_action=RATING_TO_ACTION.get(rating or "", Action.WAIT),
                              status=RetrievalStatus.OK if thesis and rating else RetrievalStatus.PARTIAL)


# ------------------------------------------------------------------------- Quant
class QuantTransport(Protocol):
    def review(self, request: dict) -> dict: ...


class FileQuantTransport:
    """Drop a request JSON into an inbox; Quant (on the Mac mini) writes a response JSON to the outbox.
    NOT VERIFIED: the real Quant's interface was not available."""

    def __init__(self, inbox: str | Path, outbox: str | Path):
        self.inbox, self.outbox = Path(inbox), Path(outbox)
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.outbox.mkdir(parents=True, exist_ok=True)

    def review(self, request: dict) -> dict:
        (self.inbox / f"{request['request_id']}.json").write_text(json.dumps(request, default=str, indent=2))
        resp = self.outbox / f"{request['request_id']}.json"
        return json.loads(resp.read_text()) if resp.exists() else {"verdict": "UNAVAILABLE", "findings": ["Quant has not responded yet"]}


class QuantAdapter:
    """Quant stays the independent quantitative challenger: it receives the evidence, never Marcus's conclusion."""

    def __init__(self, transport: QuantTransport):
        self.transport = transport

    def request_review(self, subject_ids: list[str], evidence: dict) -> QuantReview:
        req = {"request_id": new_id("qreq"), "subject_ids": subject_ids, "evidence": evidence,
               "requested_at": utcnow().isoformat(), "instructions": "independently assess data, parameters, statistics and claimed significance"}
        try:
            resp = self.transport.review(req)
        except Exception as exc:  # noqa: BLE001
            resp = {"verdict": "UNAVAILABLE", "findings": [f"transport error: {exc}"]}
        verdict = resp.get("verdict", "UNAVAILABLE")
        if verdict not in ("SUPPORTED", "CHALLENGED", "INSUFFICIENT_EVIDENCE", "UNAVAILABLE"):
            verdict = "UNAVAILABLE"
        return QuantReview(subject_ids=subject_ids, verdict=verdict, findings=list(resp.get("findings", [])),
                           reviewer=resp.get("reviewer", "quant"))


# ------------------------------------------------------------------ Price Monitor
def parse_price_monitor_event(payload: dict) -> MarketEvent:
    """Price Monitor -> MarketEvent. Expected fields: event_id, instrument_id, type, price, level,
    occurred_at (ISO). Also accepts trader-alerts output lines (`ALERT SYM last $X: ...`). NOT VERIFIED."""
    if "line" in payload:                       # trader-alerts text output
        m = re.match(r"ALERT\s+(\S+)\s+last\s+\$([\d.]+):\s*(.*)", payload["line"])
        if not m:
            raise ValueError(f"unrecognised alert line: {payload['line']!r}")
        sym, price, text = m.groups()
        occurred = datetime.fromisoformat(payload["occurred_at"])
        return MarketEvent(event_id=payload.get("event_id") or f"trader-alerts:{sym}:{occurred.isoformat()}:{text[:40]}",
                           instrument_id=payload.get("instrument_id", f"US:{sym}"), event_type="price_alert",
                           payload={"price": price, "text": text}, source="price_monitor", occurred_at=occurred)
    for k in ("event_id", "instrument_id", "type", "occurred_at"):
        if k not in payload:
            raise ValueError(f"price monitor event missing {k}")
    return MarketEvent(event_id=payload["event_id"], instrument_id=payload["instrument_id"], event_type=payload["type"],
                       payload={k: v for k, v in payload.items() if k not in ("event_id", "instrument_id", "type", "occurred_at")},
                       source="price_monitor", occurred_at=datetime.fromisoformat(payload["occurred_at"]))


# --------------------------------------------------------------------------- Grok
GROK_TOOLS = {
    "marcus.daily_monitoring": "Workflow A: validate Price Monitor events, refresh research only when needed",
    "marcus.new_opportunity": "Workflow B: full specialist pipeline for one instrument; ends with a recommendation pending owner approval",
    "marcus.portfolio_review": "Workflow C: analyst metrics and rebalancing proposals",
    "marcus.execute_approved_order": "Execution Specialist on an owner-approved paper order",
    "marcus.telemetry": "Per-agent invocation, latency, token and cost telemetry",
}


def grok_tool_manifest() -> list[dict]:
    """A generic function-calling manifest (JSON schema) for the Grok runtime. NOT VERIFIED against Grok."""
    return [{"name": n, "description": d, "parameters": {"type": "object", "properties": {"payload": {"type": "object"}},
                                                          "required": ["payload"]}} for n, d in GROK_TOOLS.items()]
