"""Merlin's output contract: `merlin-research/v1`.

One schema for both environments. Claude Cloud Merlin writes it as `report.json` next to its
markdown report; the Grokbot/CLI runner asks `claude -p --json-schema` for it directly. The
validator (`merlin.validate`) then checks it deterministically before Marcus or Quant see it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CONTRACT = "merlin-research/v1"
SPEC_VERSION = "1.0.0"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceStatus(str, Enum):
    VERIFIED = "VERIFIED"        # read from the original document this run; URL, date and period recorded
    PROVISIONAL = "PROVISIONAL"  # credible but not confirmed against the original (summary, secondary source)
    ASSUMPTION = "ASSUMPTION"    # an input Merlin or the user chose (incl. synthetic data); never a fact
    UNKNOWN = "UNKNOWN"          # needed but not found
    STALE = "STALE"              # real, but older than the freshness rule for its kind
    FAILED = "FAILED"            # retrieval or validation failed; must not support any figure


class QualityStatus(str, Enum):
    VERIFIED = "VERIFIED"
    PROVISIONAL = "PROVISIONAL"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"


# strength order used to stop a figure claiming more than its evidence
STRENGTH = {EvidenceStatus.VERIFIED: 4, EvidenceStatus.PROVISIONAL: 3, EvidenceStatus.STALE: 2,
            EvidenceStatus.ASSUMPTION: 1, EvidenceStatus.UNKNOWN: 0, EvidenceStatus.FAILED: 0}

TaskType = Literal["company_research", "market_research", "earnings_review", "financial_model",
                   "valuation_check", "macro", "comparison", "question"]
EvidenceKind = Literal["filing", "press_release", "transcript", "statistics", "central_bank",
                       "industry_body", "dataset", "connector", "market_data", "news",
                       "synthetic", "derived", "other"]
CapabilityKind = Literal["fsi_plugin_skill", "fsi_agent", "merlin_skill", "merlin_calc", "web_fetch",
                         "web_search", "connector", "sec_api", "ordinary_analysis"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Request(Strict):
    task_type: TaskType
    subject: str                                   # "NVDA", "semiconductors", "gold"
    question: str
    as_of: date
    requested_by: Literal["owner", "marcus", "quant", "grokbot", "test"] = "owner"
    instrument_id: Optional[str] = None            # Marcus instrument id when there is one, e.g. "US:NVDA"
    request_id: str = Field(default_factory=lambda: new_id("mreq"))


class Capability(Strict):
    name: str                                      # e.g. "equity-research:earnings-analysis", "merlin.calc.run_dcf"
    kind: CapabilityKind
    genuine_execution: bool                        # True only when the plugin/agent/tool actually ran
    detail: str = ""


class EvidenceItem(Strict):
    evidence_id: str                               # short stable id cited by figures, e.g. "E1"
    title: str
    publisher: str
    kind: EvidenceKind
    status: EvidenceStatus
    url: Optional[str] = None
    document_date: Optional[date] = None
    period: Optional[str] = None                   # observation period, e.g. "Q2 FY2027 (to 2026-07-26)"
    retrieved_at: Optional[datetime] = None
    notes: str = ""


class Figure(Strict):
    figure_id: str
    metric: str
    value: float
    unit: str                                      # "USD m", "%", "x", "USD/share"
    period: Optional[str] = None
    evidence_ids: list[str] = Field(min_length=1)
    status: EvidenceStatus
    derived_by: Optional[str] = None               # merlin.calc function when computed, else None (reported)
    as_live: bool = False                          # True only for a live quote; Merlin's sources never are


class Finding(Strict):
    rank: int
    finding: str
    significance: Literal["high", "medium", "low"]
    figure_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class Assumption(Strict):
    name: str
    value: Any
    rationale: str
    status: Literal["ASSUMPTION", "PROVISIONAL", "VERIFIED"] = "ASSUMPTION"


class Model(Strict):
    type: Literal["dcf", "comps", "ratios", "projection", "other"]
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[Assumption] = Field(default_factory=list)
    sensitivity: Optional[dict[str, Any]] = None
    limitations: list[str] = Field(default_factory=list)


class Scenario(Strict):
    name: Literal["bull", "base", "bear"]
    narrative: str
    key_assumptions: dict[str, Any] = Field(default_factory=dict)
    value: Optional[float] = None
    unit: Optional[str] = None


class Risk(Strict):
    risk: str
    evidence_ids: list[str] = Field(default_factory=list)


class Implication(Strict):
    audience: Literal["marcus", "quant", "owner", "tradingagents"]
    text: str


class Boundaries(Strict):
    """Fixed. A report that changes these fails validation."""
    paper_research_only: Literal[True] = True
    trade_instruction: Literal[False] = False
    binding_portfolio_decision: Literal[False] = False


class MerlinReport(Strict):
    contract: Literal["merlin-research/v1"] = CONTRACT
    spec_version: str = SPEC_VERSION
    report_id: str = Field(default_factory=lambda: new_id("mrep"))
    created_at: datetime = Field(default_factory=utcnow)
    environment: Literal["claude-cloud", "claude-cli", "anthropic-api", "test"]
    request: Request
    capabilities_used: list[Capability] = Field(min_length=1)
    executive_summary: str
    key_findings: list[Finding] = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(min_length=1)
    figures: list[Figure] = Field(default_factory=list)
    model: Optional[Model] = None
    scenarios: list[Scenario] = Field(default_factory=list)
    risks: list[Risk] = Field(min_length=1)
    counterarguments: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(min_length=1)
    implications: list[Implication] = Field(min_length=1)
    quality_status: QualityStatus
    quality_notes: str                             # strength and limits of the evidence, in words (no % confidence)
    limitations: list[str] = Field(default_factory=list)
    boundaries: Boundaries = Field(default_factory=Boundaries)
    report_markdown_path: Optional[str] = None


def json_schema() -> dict:
    return MerlinReport.model_json_schema()
