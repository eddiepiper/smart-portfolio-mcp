"""Agent permission policy and paper-only enforcement.

Each agent gets an explicit allowlist of capabilities. Anything not listed is
denied. Real-money trading is not a capability at all: there is no permission
that grants it, the contracts force `paper=True`, and `assert_no_live_trading`
refuses any command or module that could reach a live broker.
"""
from __future__ import annotations

import re
from typing import Iterable

CAPABILITIES = {
    "read_market_data", "write_market_cache", "validate_data", "run_backtest",
    "read_research", "write_research_challenge", "read_ledger", "append_ledger",
    "simulate_paper_execution", "write_recommendation", "request_quant_review",
    "read_mandate", "propose_mandate", "call_llm",
}

POLICY: dict[str, set[str]] = {
    "market_intelligence": {"read_market_data", "write_market_cache", "read_mandate"},
    "data_guardian": {"read_market_data", "validate_data", "call_llm"},
    "strategy_lab": {"read_market_data", "run_backtest", "read_mandate"},
    "research_challenger": {"read_market_data", "read_research", "write_research_challenge",
                            "request_quant_review", "call_llm"},
    "execution_specialist": {"read_market_data", "read_ledger", "append_ledger",
                             "simulate_paper_execution", "read_mandate"},
    "portfolio_analyst": {"read_market_data", "read_ledger", "read_mandate"},
    "marcus": {"read_market_data", "read_research", "read_ledger", "read_mandate",
               "write_recommendation", "request_quant_review", "propose_mandate", "call_llm"},
}

# Anything matching these is a live-trading path and is refused outright.
_LIVE_PATTERNS = [
    r"\blean\s+live\b", r"\blive[-_ ]?trading\b", r"\blive\s+deploy\b", r"--live\b",
    r"\bbrokerage\b", r"\bplace_stock_order\b", r"\balpaca\b", r"\binteractive[-_ ]?brokers\b",
    r"\bib[-_]?gateway\b", r"\btradier\b", r"\boanda\b", r"\bbinance\b", r"\bcoinbase\b",
    r"x402", r"wallet[-_]?private[-_]?key",
]
_LIVE_RE = re.compile("|".join(_LIVE_PATTERNS), re.IGNORECASE)

# Environment variables that would indicate brokerage credentials were provisioned.
FORBIDDEN_ENV = [
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "IB_ACCOUNT", "QC_LIVE_BROKERAGE",
    "TRADIER_ACCESS_TOKEN", "OANDA_ACCESS_TOKEN", "X402_WALLET_PRIVATE_KEY",
]


class PermissionDenied(Exception):
    pass


class LiveTradingForbidden(PermissionDenied):
    pass


def require(agent_id: str, capability: str) -> None:
    if capability not in CAPABILITIES:
        raise PermissionDenied(f"unknown capability {capability!r}")
    if capability not in POLICY.get(agent_id, set()):
        raise PermissionDenied(f"{agent_id} is not allowed to {capability}")


def assert_no_live_trading(command: Iterable[str] | str) -> None:
    text = command if isinstance(command, str) else " ".join(command)
    if _LIVE_RE.search(text):
        raise LiveTradingForbidden(f"live-trading path refused: {text!r}")


def assert_no_broker_credentials(environ: dict) -> None:
    present = [k for k in FORBIDDEN_ENV if environ.get(k)]
    if present:
        raise LiveTradingForbidden(f"brokerage credentials present in environment: {present}; refusing to run")
