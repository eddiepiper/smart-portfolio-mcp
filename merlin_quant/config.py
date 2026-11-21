"""Configuration: loaded from JSON, versioned by content hash, never from prompts.

Risk thresholds live here and only here. Nothing an LLM writes is read as a
threshold. A config whose `config_status` is EXAMPLE can run PAPER_MANUAL, but
can never enable PAPER_AUTO.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from merlin_quant.contracts import Mode

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = REPO_ROOT / "config" / "merlin_quant" / "portfolio.example.json"


class _C(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PortfolioCfg(_C):
    name: str
    initial_capital: float = Field(gt=0)
    base_currency: str
    benchmark: str
    universe: list[str] = Field(min_length=1)
    sectors: dict[str, str]


class RiskCfg(_C):
    max_position_weight: float = Field(gt=0, le=1)
    max_gross_exposure: float = Field(gt=0, le=1)
    max_sector_weight: float = Field(gt=0, le=1)
    max_order_value: float = Field(gt=0)
    max_order_quantity: int = Field(gt=0)
    min_order_value: float = Field(ge=0)
    cash_buffer_pct: float = Field(ge=0, lt=1)
    max_price_staleness_sessions: int = Field(ge=0)
    max_drawdown_halt: float = Field(gt=0, lt=1)
    max_orders_per_session: int = Field(gt=0)
    reference_price_tolerance: float = Field(ge=0)
    # Optional: reject a trade whose estimated round-trip commission (buy now + sell later, at today's price) is
    # more than this fraction of the order value. None = report the fee drag on every check but never block on it.
    max_round_trip_fee_pct: float | None = Field(default=None, gt=0, lt=1)


def commission_for(quantity: int, price: float, *, per_share: float, rate: float, minimum: float) -> float:
    """Broker commission for ONE side of a trade (a buy, or a sell).

    commission = max(per_share x shares + rate x trade value, minimum), trade value = shares x price.
    A staff online schedule of "0.06%, minimum USD 15" is rate=0.0006, minimum=15, per_share=0: a 10-share
    order at $150 ($1,500) pays max($0.90, $15) = $15; the 0.06% only wins above $25,000 a side.
    """
    if quantity <= 0:
        return 0.0
    return max(quantity * per_share + rate * quantity * price, minimum)


class CostsCfg(_C):
    spread_bps: float = Field(ge=0)
    commission_per_share: float = Field(ge=0)
    min_commission: float = Field(ge=0)
    impact_coefficient: float = Field(ge=0)
    min_trade_pct: float = Field(ge=0, lt=1)
    # Percentage-of-value commission per side (0.0006 = 0.06%). Defaults to 0 so an older config keeps its
    # exact old fees; the example config carries the staff online rate.
    commission_rate: float = Field(default=0.0, ge=0, lt=0.05)
    basis: str = ""  # where the fee schedule comes from, for the record (not read by any code)

    def commission(self, quantity: int, price: float) -> float:
        return commission_for(quantity, price, per_share=self.commission_per_share, rate=self.commission_rate,
                              minimum=self.min_commission)

    def fee_estimate(self, quantity: int, price: float) -> dict[str, float]:
        """What a trade of *quantity* at *price* costs, in plain numbers, at today's price.

        breakeven_move_pct is how far the price must rise between buying and selling just to get the money back:
        commission both ways plus the modelled half-spread both ways (market impact is left out)."""
        value = quantity * price
        one = self.commission(quantity, price)
        rt = 2 * one
        spread = 2 * self.spread_bps / 10_000
        return {"order_value": round(value, 2), "commission_one_side": round(one, 2),
                "commission_round_trip": round(rt, 2),
                "round_trip_fee_pct": round(rt / value, 6) if value else 0.0,
                "breakeven_move_pct": round(rt / value + spread, 6) if value else 0.0}


class PolicyCfg(_C):
    full_target_weight: float = Field(ge=0, le=1)
    partial_target_weight: float = Field(ge=0, le=1)
    min_conviction: float = Field(ge=0, le=1)
    rules: str = ""


class EarningsWindowCfg(_C):
    """No new buys from `pre_sessions` sessions before an expected report until `post_sessions` after it."""
    action: str = Field(default="block", pattern="^(block|flag)$")
    pre_sessions: int = Field(default=5, ge=0, le=30)
    post_sessions: int = Field(default=3, ge=0, le=30)
    min_gap_days: int = Field(default=45, ge=1)  # an estimated next report is at least this long after the last one


class MarketCfg(_C):
    """Market-wide sell-off check. Defaults fired on about 4% of sessions in 2025-26 SPY history
    (March-April 2025, 10 Oct 2025, 5 Jun 2026, 5/19/20 Nov 2026)."""
    action: str = Field(default="block", pattern="^(block|flag)$")
    index_1d_drop: float = Field(default=0.025, gt=0, lt=1)  # an index falls 2.5% or more in one session
    index_5d_drop: float = Field(default=0.05, gt=0, lt=1)  # ... or 5% or more over five sessions
    index_drawdown: float = Field(default=0.08, gt=0, lt=1)  # ... or sits 8% or more below its recent high
    drawdown_lookback_sessions: int = Field(default=20, ge=2)
    extra_indices: list[str] = Field(default_factory=lambda: ["QQQ"])  # checked when present in the data
    stock_vs_market_5d: float = Field(default=0.10, gt=0, lt=1)  # stock trails the benchmark by 10%+ in 5 sessions
    relative_action: str = Field(default="block", pattern="^(block|flag)$")


class GuardsCfg(_C):
    """Checks before buying (merlin_quant.guards). They can only hold a buy back; sales are never blocked."""
    enabled: bool = True  # False: every guard only flags (nothing blocks, Merlin is not capped)
    stale_data_action: str = Field(default="block", pattern="^(block|flag)$")
    max_quarter_age_days: int = Field(default=135, ge=60)  # ~91-day quarter + 45-day 10-Q deadline
    limited_mode_action: str = Field(default="flag", pattern="^(block|flag)$")  # no SEC history (new listings)
    earnings_window: EarningsWindowCfg = Field(default_factory=EarningsWindowCfg)
    market: MarketCfg = Field(default_factory=MarketCfg)
    earnings_dates: dict[str, list[str]] = Field(default_factory=dict)  # owner-known dates, e.g. {"NVDA": ["2027-02-24"]}
    note: str = ""


class Config(_C):
    notice: str = Field(default="", alias="_notice")
    config_status: str  # EXAMPLE | OWNER_CONFIGURED
    portfolio: PortfolioCfg
    mode: Mode
    risk: RiskCfg
    costs: CostsCfg
    execution: dict
    decision_policy: PolicyCfg
    runtimes: dict
    data: dict
    guards: GuardsCfg = Field(default_factory=GuardsCfg)  # absent in older configs: the documented defaults apply

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _auto_needs_owner_config(self):
        if self.mode == Mode.PAPER_AUTO and self.config_status != "OWNER_CONFIGURED":
            raise ValueError("PAPER_AUTO requires config_status OWNER_CONFIGURED (example thresholds are not approved)")
        if self.portfolio.benchmark in self.portfolio.universe:
            raise ValueError("the benchmark cannot also be a tradable instrument")
        return self

    @property
    def version(self) -> str:
        blob = json.dumps(self.model_dump(mode="json", by_alias=True), sort_keys=True).encode()
        return "cfg-" + hashlib.sha256(blob).hexdigest()[:12]


def load_config(path: str | Path | None = None) -> Config:
    data = json.loads(Path(path or EXAMPLE_CONFIG).read_text())
    return Config.model_validate(data)
