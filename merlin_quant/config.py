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


class CostsCfg(_C):
    spread_bps: float = Field(ge=0)
    commission_per_share: float = Field(ge=0)
    min_commission: float = Field(ge=0)
    impact_coefficient: float = Field(ge=0)
    min_trade_pct: float = Field(ge=0, lt=1)


class PolicyCfg(_C):
    full_target_weight: float = Field(ge=0, le=1)
    partial_target_weight: float = Field(ge=0, le=1)
    min_conviction: float = Field(ge=0, le=1)
    rules: str = ""


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
