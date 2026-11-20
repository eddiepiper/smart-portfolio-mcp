"""Typed contracts for every record in the workflow (merlin-quant/v1).

Every record carries a stable id, and later records reference earlier ones by id,
so a fill can be traced back to the research that produced it:

    MerlinReport.report_id ─┐
    QuantReport.report_id ──┼─> ChallengeReport ─> JointAssessment ─> TradeProposal
                            │                                            │
                            └────────────────────────────────────────────┤
                                       RiskGateResult <──────────────────┤
                                       ExecutionRecord <─────────────────┘
                                       PortfolioSnapshot / PerformanceReport

LLM output never becomes an order directly: a proposal is built by deterministic
code from typed fields, then validated here (extra fields forbidden, every
required field present, enums closed), then checked by the risk gate.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from merlin_quant import CONTRACT_VERSION


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=False)


def stable_id(prefix: str, payload: dict) -> str:
    """Deterministic id: the same inputs always give the same id (idempotency)."""
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return f"{prefix}-{digest[:16]}"


class Mode(str, Enum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    PAPER_MANUAL = "PAPER_MANUAL"
    PAPER_AUTO = "PAPER_AUTO"


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    NO_ACTION = "NO_ACTION"


class Verdict(str, Enum):
    AGREE = "AGREE"
    PARTIALLY_AGREE = "PARTIALLY_AGREE"
    DISAGREE = "DISAGREE"
    INCONCLUSIVE = "INCONCLUSIVE"


class Stance(str, Enum):
    """A lead's directional view. Research, not an order."""
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class Source(Strict):
    name: str
    kind: Literal["synthetic_fixture", "market_data", "filing", "news", "model_output", "computed", "plugin_skill"]
    as_of: str
    reference: str = ""


class RunMeta(Strict):
    """How a lead ran: which runtime, which models, how many calls, what it cost."""
    runtime: str
    models: list[str] = Field(default_factory=list)
    model_calls: int = 0
    failed_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    wall_seconds: float = 0.0
    context_id: str = Field(description="Separate execution context id (process/session) for independence audits")
    inputs_digest: str = Field(description="sha256 of exactly what this lead was shown")
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- Merlin

class Scenario(Strict):
    name: Literal["bull", "base", "bear"]
    revenue_cagr: float
    terminal_margin: float
    wacc: float
    terminal_growth: float
    value_per_share: float
    upside_pct: float


class MerlinReport(Strict):
    contract: str = CONTRACT_VERSION
    record_type: Literal["merlin_research_report"] = "merlin_research_report"
    report_id: str
    instrument: str
    market: str
    research_date: date
    evidence_cutoff: date
    business_overview: str
    financial_metrics: dict[str, float | None]
    valuation_assumptions: dict[str, float]
    scenarios: list[Scenario]
    reference_price: float
    reference_price_date: date
    fair_value_base: float
    thesis: str
    catalysts: list[str]
    material_risks: list[str]
    sources: list[Source]
    unknowns: list[str]
    research_implications: str
    stance: Stance
    conviction: float = Field(ge=0, le=1)
    fsi_skills_used: list[str] = Field(default_factory=list)
    run: RunMeta

    @field_validator("scenarios")
    @classmethod
    def _three(cls, v):
        if sorted(s.name for s in v) != ["base", "bear", "bull"]:
            raise ValueError("scenarios must be exactly bull, base and bear")
        return v

    @model_validator(mode="after")
    def _cutoff(self):
        if self.evidence_cutoff > self.research_date:
            raise ValueError("evidence_cutoff cannot be after research_date")
        if self.reference_price_date > self.evidence_cutoff:
            raise ValueError("reference price is after the evidence cutoff (look-ahead)")
        return self


# ---------------------------------------------------------------- Quant

class QuantReport(Strict):
    contract: str = CONTRACT_VERSION
    record_type: Literal["quant_independent_report"] = "quant_independent_report"
    report_id: str
    instrument: str
    market: str
    analysis_date: date
    data_cutoff: date
    saw_merlin_report: Literal[False] = False
    indicators: dict[str, float | None]
    technical_assessment: str
    momentum_assessment: str
    news_sentiment_assessment: str
    volatility_liquidity_risks: str
    bull_case: str
    bear_case: str
    risk_reward: str
    entry_exit_considerations: str
    tradingagents_rating: str | None = Field(description="TradingAgents Portfolio Manager rating, verbatim")
    tradingagents_reports: dict[str, str] = Field(default_factory=dict)
    stance: Stance
    conviction: float = Field(ge=0, le=1)
    sources: list[Source]
    run: RunMeta


class Challenge(Strict):
    topic: Literal["financial_assumptions", "growth_expectations", "valuation_sensitivity",
                   "market_timing", "downside_risks", "contrary_market_evidence", "evidence_quality"]
    finding: str
    severity: Literal["low", "medium", "high"]
    supported_by: list[str] = Field(description="Evidence references Quant used (independent of Merlin)")


class ChallengeReport(Strict):
    contract: str = CONTRACT_VERSION
    record_type: Literal["quant_challenge_report"] = "quant_challenge_report"
    report_id: str
    merlin_report_id: str
    quant_report_id: str
    instrument: str
    verdict: Verdict
    challenges: list[Challenge]
    unsupported_assumptions: list[str]
    reasoning: str
    run: RunMeta


class JointAssessment(Strict):
    contract: str = CONTRACT_VERSION
    record_type: Literal["joint_investment_assessment"] = "joint_investment_assessment"
    assessment_id: str
    instrument: str
    merlin_report_id: str
    quant_report_id: str
    challenge_report_id: str
    merlin_thesis: str
    merlin_stance: Stance
    quant_assessment: str
    quant_stance: Stance
    challenge_verdict: Verdict
    quant_challenges: list[str]
    agreements: list[str]
    disagreements: list[str]
    unresolved_risks: list[str]
    disagreement_preserved: bool
    policy_applied: str
    proposed_action: Action
    proposed_target_weight: float = Field(ge=0, le=1)
    rationale: str


# ---------------------------------------------------------------- Decision / risk / execution

class TradeProposal(Strict):
    """The ONE shared paper-trading decision contract."""
    contract: str = CONTRACT_VERSION
    record_type: Literal["paper_trade_proposal"] = "paper_trade_proposal"
    proposal_id: str
    instrument: str
    market: str
    decision_timestamp: datetime
    data_cutoff: date
    merlin_report_id: str
    quant_report_id: str
    challenge_report_id: str
    assessment_id: str
    challenge_outcome: Verdict
    action: Action
    quantity: int = Field(ge=0)
    target_weight: float = Field(ge=0, le=1)
    reference_price: float = Field(gt=0)
    reference_price_date: date
    reference_price_source: str
    thesis: str = Field(min_length=10)
    risk_considerations: list[str] = Field(min_length=1)
    position_context: dict[str, float]
    eligibility: Literal["ELIGIBLE", "NOT_ELIGIBLE"]
    eligibility_reasons: list[str] = Field(default_factory=list)
    # Estimated commission at the reference price under the config's fee schedule (order_value,
    # commission_one_side, commission_round_trip, round_trip_fee_pct, breakeven_move_pct). Empty on proposals
    # made before fees were modelled; those are left as written.
    estimated_costs: dict[str, float] = Field(default_factory=dict)
    status: Literal["PROPOSED", "APPROVED", "REJECTED", "EXECUTED", "BLOCKED", "CANCELLED"] = "PROPOSED"

    @model_validator(mode="after")
    def _consistent(self):
        if self.action in (Action.BUY, Action.SELL, Action.REDUCE) and self.quantity <= 0:
            raise ValueError(f"{self.action.value} needs a positive quantity")
        if self.action in (Action.HOLD, Action.NO_ACTION) and self.quantity != 0:
            raise ValueError(f"{self.action.value} must have quantity 0")
        if self.reference_price_date > self.data_cutoff:
            raise ValueError("reference price is after the data cutoff (look-ahead)")
        if self.decision_timestamp.date() < self.data_cutoff:
            raise ValueError("decision timestamp is before its own data cutoff")
        return self


class RiskCheck(Strict):
    name: str
    passed: bool
    detail: str


class RiskGateResult(Strict):
    record_type: Literal["risk_gate_result"] = "risk_gate_result"
    result_id: str
    proposal_id: str
    outcome: Literal["PASS", "REJECT", "BLOCKED"]
    checks: list[RiskCheck]
    reasons: list[str]
    config_version: str
    evaluated_at: datetime


class ExecutionRecord(Strict):
    record_type: Literal["paper_execution_record"] = "paper_execution_record"
    execution_id: str
    proposal_id: str
    risk_result_id: str
    instrument: str
    side: Literal["buy", "sell"]
    quantity: int
    reference_price: float
    fill_session: date
    fill_basis: str = Field(description="e.g. 'next session open + half-spread + impact'")
    fill_price: float
    commission: float
    slippage_cost: float
    gross_value: float
    net_cash_change: float
    simulated: Literal[True] = True
    note: str = "SIMULATED fill. No real order was placed and no market fill occurred."


class PositionView(Strict):
    instrument: str
    quantity: int
    avg_cost: float
    mark: float | None
    mark_date: date | None
    market_value: float | None
    unrealized_pnl: float | None
    weight: float | None


class PortfolioSnapshot(Strict):
    record_type: Literal["portfolio_snapshot"] = "portfolio_snapshot"
    as_of: date
    base_currency: str
    cash: float
    positions: list[PositionView]
    nav: float
    realized_pnl: float
    unrealized_pnl: float
    fees_paid: float
    dividends_received: float
    gross_exposure: float
    initial_capital: float
    halted: bool
    mode: Mode
    ledger_head: str
    stale_marks: list[str] = Field(default_factory=list)
    # Commission it would cost to sell every position at its mark under today's fee schedule (an estimate,
    # not yet paid). unrealized_pnl already includes the commission paid on the buys.
    estimated_exit_fees: float = 0.0
