"""Versioned domain contracts shared by Marcus and the six specialists.

Every object carries a stable id, a schema version and the timestamps it needs.
Money is Decimal; prices carry their currency and pricing unit. These models are
the system of record for structured data; free-text LLM output is never stored
as the only record of a financial fact.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Contract(BaseModel):
    model_config = ConfigDict(frozen=False, extra="forbid", use_enum_values=False)
    schema_version: str = SCHEMA_VERSION


# ---------------------------------------------------------------- enumerations
class AssetClass(str, Enum):
    EQUITY = "equity"
    ETF = "etf"
    COMMODITY = "commodity"
    FX = "fx"
    INDEX = "index"


class PriceType(str, Enum):
    OFFICIAL_CLOSE = "official_close"      # final daily close after the session ended
    PROVISIONAL_INTRADAY = "provisional_intraday"
    DELAYED = "delayed"
    INDICATIVE = "indicative"
    HISTORICAL = "historical"
    UNSUPPORTED = "unsupported"


class MarketSession(str, Enum):
    PRE = "pre_market"
    REGULAR = "regular"
    POST = "post_market"
    CLOSED = "closed"
    CONTINUOUS = "continuous"   # e.g. spot metals, FX
    UNKNOWN = "unknown"


class QualityStatus(str, Enum):
    VALID = "VALID"
    WARNING = "WARNING"
    REJECTED = "REJECTED"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Action(str, Enum):
    BUY = "BUY"
    WAIT = "WAIT"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NOT_REQUIRED = "not_required"


class RetrievalStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


# --------------------------------------------------------------- reference data
class Instrument(Contract):
    instrument_id: str                      # e.g. "US:BAC", "XAU:USD-OZ"
    symbol: str                             # provider symbol, e.g. "BAC", "GC=F"
    market: str                             # "NYSE", "COMEX", "SGX"...
    asset_class: AssetClass
    currency: str                           # ISO 4217, e.g. "USD"
    price_unit: str = "per_share"           # "per_share", "per_troy_oz", "per_gram"
    timezone: str = "America/New_York"
    lot_size: int = 1


class MarketObservation(Contract):
    observation_id: str = Field(default_factory=lambda: new_id("obs"))
    event_id: Optional[str] = None          # upstream id for de-duplication
    instrument_id: str
    symbol: str
    market: str
    asset_class: AssetClass
    currency: str
    price_unit: str
    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    close: Optional[Decimal] = None         # the price this observation asserts
    volume: Optional[Decimal] = None
    bar_date: Optional[date] = None         # trading date for daily bars
    source_provider: str
    observed_at: Optional[datetime] = None  # source timestamp
    retrieved_at: datetime = Field(default_factory=utcnow)
    market_timezone: str = "America/New_York"
    session: MarketSession = MarketSession.UNKNOWN
    price_type: PriceType = PriceType.UNSUPPORTED
    validation_status: Optional[QualityStatus] = None


class MarketEvent(Contract):
    event_id: str
    instrument_id: str
    event_type: str                         # "price_alert", "trigger_hit", "news"...
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str                             # "price_monitor", "market_intelligence"...
    occurred_at: datetime
    received_at: datetime = Field(default_factory=utcnow)


class Evidence(Contract):
    evidence_id: str = Field(default_factory=lambda: new_id("ev"))
    kind: str                               # "market_data", "filing", "news", "backtest"...
    source: str
    reference: str                          # path, URL, report id
    summary: str = ""
    captured_at: datetime = Field(default_factory=utcnow)


# -------------------------------------------------------------------- mandate
class InvestmentMandate(Contract):
    mandate_id: str
    version: int
    effective_from: date
    base_currency: str = "SGD"
    paper_capital: Decimal
    paper_only: Literal[True] = True        # cannot be set False: real money is out of scope
    max_position_pct: Decimal               # of portfolio equity
    max_loss_per_trade_pct: Decimal         # of portfolio equity
    max_daily_loss_pct: Decimal
    cash_buffer_pct: Decimal
    allow_short: bool = False
    allow_margin: bool = False
    allow_options: bool = False
    approved_instruments: list[str] = Field(default_factory=list)   # instrument_ids; empty = any listed equity/ETF
    no_chase_pct: Decimal = Decimal("0.5")  # max % above the planned entry a paper buy may fill
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    notes: str = ""


# ------------------------------------------------------------------- reports
class ValidationIssue(Contract):
    check: str
    severity: Severity
    message: str
    field: Optional[str] = None
    observation_id: Optional[str] = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class DataQualityReport(Contract):
    report_id: str = Field(default_factory=lambda: new_id("dqr"))
    correlation_id: str
    status: QualityStatus
    checked_observations: int
    issues: list[ValidationIssue] = Field(default_factory=list)
    accepted_observation_ids: list[str] = Field(default_factory=list)
    rejected_observation_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    explanation: Optional[str] = None       # optional LLM narrative; never changes status


class MarketIntelligenceReport(Contract):
    report_id: str = Field(default_factory=lambda: new_id("mir"))
    correlation_id: str
    requesting_agent: str
    instrument: Instrument
    category: str
    source_provider: str
    source_timestamp: Optional[datetime] = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    observations: list[MarketObservation] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    provider_limitations: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    status: RetrievalStatus
    data_quality_report_id: Optional[str] = None   # set once Data Guardian has checked it
    data_quality_status: Optional[QualityStatus] = None


class Claim(Contract):
    claim_id: str = Field(default_factory=lambda: new_id("clm"))
    text: str
    evidence_refs: list[str] = Field(default_factory=list)
    # optional machine-checkable assertion, e.g. {"metric": "close_vs_ma200", "op": ">", "value": 0}
    assertion: Optional[dict[str, Any]] = None


class ResearchReport(Contract):
    report_id: str = Field(default_factory=lambda: new_id("rr"))
    source: str = "tradingagents"
    instrument_id: str
    as_of: date
    rating: Optional[str] = None            # TradingAgents 5-tier rating or None
    thesis: str
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    failed_feeds: list[str] = Field(default_factory=list)
    proposed_action: Optional[Action] = None
    status: RetrievalStatus = RetrievalStatus.OK


class ResearchChallengeReport(Contract):
    report_id: str = Field(default_factory=lambda: new_id("rcr"))
    challenged_report_id: str
    instrument_id: str
    thesis: str
    supporting_arguments: list[str] = Field(default_factory=list)
    counterarguments: list[str] = Field(default_factory=list)
    contradictory_evidence: list[ValidationIssue] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    downside_scenarios: list[dict[str, Any]] = Field(default_factory=list)
    assumptions_to_verify: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    limitations: list[str] = Field(default_factory=list)
    quant_review_recommended: bool = False
    independence_ok: bool = True
    llm_used: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class BacktestRequest(Contract):
    request_id: str = Field(default_factory=lambda: new_id("btq"))
    strategy_id: str
    instrument_id: str
    symbol: str
    start: date
    end: date
    data_version: str                       # hash of the input price frame
    entry_rule: dict[str, Any]
    exit_rule: dict[str, Any]
    position_sizing: dict[str, Any] = Field(default_factory=lambda: {"type": "fixed_fraction", "fraction": 1.0})
    initial_cash: Decimal = Decimal("10000")
    fees_pct: Decimal = Decimal("0.001")
    fixed_fee: Decimal = Decimal("0")
    slippage_pct: Decimal = Decimal("0.0005")
    benchmark: str = "buy_and_hold"
    config: dict[str, Any] = Field(default_factory=dict)


class BacktestResult(Contract):
    result_id: str = Field(default_factory=lambda: new_id("btr"))
    request_id: str
    status: Literal["OK", "INSUFFICIENT_DATA", "FAILED", "LEAKAGE_DETECTED"]
    engine: str
    engine_version: str
    observations: int
    trades: int
    metrics: dict[str, Any] = Field(default_factory=dict)
    benchmark_metrics: dict[str, Any] = Field(default_factory=dict)
    out_of_sample: dict[str, Any] = Field(default_factory=dict)
    sensitivity: dict[str, Any] = Field(default_factory=dict)
    concerns: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    reproducibility: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class QuantReview(Contract):
    review_id: str = Field(default_factory=lambda: new_id("qr"))
    subject_ids: list[str]
    verdict: Literal["SUPPORTED", "CHALLENGED", "INSUFFICIENT_EVIDENCE", "UNAVAILABLE"]
    findings: list[str] = Field(default_factory=list)
    reviewer: str = "quant"
    created_at: datetime = Field(default_factory=utcnow)


class Holding(Contract):
    instrument_id: str
    quantity: Decimal
    cost_basis: Decimal
    currency: str


class PortfolioSnapshot(Contract):
    snapshot_id: str = Field(default_factory=lambda: new_id("ps"))
    portfolio_id: str
    as_of: datetime
    base_currency: str
    cash: dict[str, Decimal]                # by currency
    holdings: list[Holding]
    contributed_capital: dict[str, Decimal]
    last_event_id: Optional[str] = None


class InvestmentRecommendation(Contract):
    recommendation_id: str = Field(default_factory=lambda: new_id("rec"))
    instrument_id: str
    action: Action
    research_refs: list[str] = Field(default_factory=list)
    challenge_refs: list[str] = Field(default_factory=list)
    backtest_refs: list[str] = Field(default_factory=list)
    quant_refs: list[str] = Field(default_factory=list)
    data_quality_refs: list[str] = Field(default_factory=list)
    portfolio_context: dict[str, Any] = Field(default_factory=dict)
    proposed_quantity: Optional[Decimal] = None
    proposed_allocation_pct: Optional[Decimal] = None
    entry_plan: dict[str, Any] = Field(default_factory=dict)   # trigger, limit, stop, target
    risk_assessment: dict[str, Any] = Field(default_factory=dict)
    mandate_id: str
    mandate_version: int
    evidence_limitations: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    owner_approval: ApprovalStatus = ApprovalStatus.PENDING
    decision_status: Literal["draft", "proposed", "approved", "rejected", "executed", "expired", "blocked"] = "draft"
    created_at: datetime = Field(default_factory=utcnow)


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"            # stop-market, used for exits


class PaperOrder(Contract):
    order_id: str
    portfolio_id: str
    instrument_id: str
    side: Literal["buy", "sell"]
    quantity: Decimal
    order_type: OrderType
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    trigger: Optional[dict[str, Any]] = None   # e.g. {"type": "daily_close_above", "level": "3994"}
    planned_entry: Optional[Decimal] = None    # reference for the no-chase rule
    recommendation_id: str
    mandate_id: str
    mandate_version: int
    decision_at: datetime
    expires_on: Optional[date] = None
    currency: str
    paper: Literal[True] = True


class FillStatus(str, Enum):
    FILLED = "FILLED"
    NO_FILL = "NO_FILL"
    UNVERIFIABLE = "UNVERIFIABLE"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class PaperFill(Contract):
    fill_id: str = Field(default_factory=lambda: new_id("fill"))
    order_id: str
    status: FillStatus
    quantity: Decimal = Decimal("0")
    price: Optional[Decimal] = None
    fees: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    filled_at: Optional[datetime] = None
    bar_date: Optional[date] = None
    price_source: Optional[str] = None
    constraints_tested: list[str] = Field(default_factory=list)
    reason: str = ""
    ledger_event_id: Optional[str] = None


class LedgerEventType(str, Enum):
    CONTRIBUTION = "contribution"
    WITHDRAWAL = "withdrawal"
    BUY = "buy"
    SELL = "sell"
    FEE = "fee"
    DIVIDEND = "dividend"
    FX = "fx"
    ADJUSTMENT_NOTE = "adjustment_note"     # informational only, no balance effect
    IMPORT = "import"                        # opening balance imported from a legacy record


class LedgerEvent(Contract):
    event_id: str = Field(default_factory=lambda: new_id("le"))
    sequence: int = 0                        # assigned by the ledger
    event_type: LedgerEventType
    portfolio_id: str
    instrument_id: Optional[str] = None
    quantity: Decimal = Decimal("0")
    currency: str
    amount: Decimal                          # signed cash effect in `currency`
    price: Optional[Decimal] = None
    price_source: Optional[str] = None
    event_at: datetime                       # when it happened (effective date)
    recorded_at: datetime = Field(default_factory=utcnow)
    decision_ref: Optional[str] = None
    execution_ref: Optional[str] = None
    idempotency_key: str
    memo: str = ""
    prev_hash: str = ""
    hash: str = ""


class PortfolioAnalysisReport(Contract):
    report_id: str = Field(default_factory=lambda: new_id("par"))
    portfolio_id: str
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    base_currency: str
    contributed_capital: dict[str, str] = Field(default_factory=dict)
    cash: dict[str, str] = Field(default_factory=dict)
    holdings: list[dict[str, Any]] = Field(default_factory=list)
    equity: Optional[str] = None
    realised_pnl: dict[str, str] = Field(default_factory=dict)
    unrealised_pnl: dict[str, str] = Field(default_factory=dict)
    returns: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)
    concentration: dict[str, Any] = Field(default_factory=dict)
    benchmark: dict[str, Any] = Field(default_factory=dict)
    reconciliation_exceptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    ledger_head: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class AgentExecutionLog(Contract):
    log_id: str = Field(default_factory=lambda: new_id("log"))
    agent_id: str
    tool: str
    correlation_id: str
    status: Literal["ok", "failed", "timeout", "rejected", "skipped"]
    started_at: datetime
    finished_at: datetime
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    model: Optional[str] = None
    provider: Optional[str] = None
    cost_usd: float = 0.0
    cache_hit: bool = False
    error: Optional[str] = None
    output_ref: Optional[str] = None
