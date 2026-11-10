"""Assemble the full V2 team (used by the CLI, the Grok adapter and end-to-end tests)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..agents.data_guardian.agent import DataGuardian
from ..agents.execution_specialist.agent import ExecutionSpecialist
from ..agents.market_intelligence.agent import MarketIntelligence
from ..agents.portfolio_analyst.agent import PortfolioAnalyst
from ..agents.research_challenger.agent import ResearchChallenger
from ..agents.strategy_lab.agent import StrategyLab
from ..core.runtime import Runtime
from ..integrations.existing import FileQuantTransport, QuantAdapter, TradingAgentsAdapter
from .orchestrator import Marcus


def build_team(rt: Runtime, *, providers: Optional[dict] = None, clock=None, quant: Optional[QuantAdapter] = None,
               tradingagents: Optional[TradingAgentsAdapter] = None) -> Marcus:
    kw = {"clock": clock} if clock else {}
    mi = MarketIntelligence(rt, providers=providers, **kw).register()
    dg = DataGuardian(rt, **kw).register()
    sl = StrategyLab(rt).register()
    rc = ResearchChallenger(rt).register()
    ex = ExecutionSpecialist(rt).register()
    pa = PortfolioAnalyst(rt).register()
    quant = quant or QuantAdapter(FileQuantTransport(rt.home / "quant" / "inbox", rt.home / "quant" / "outbox"))
    ta = tradingagents or TradingAgentsAdapter(rt.home / "tradingagents")
    return Marcus(rt, mi=mi, dg=dg, sl=sl, rc=rc, ex=ex, pa=pa, tradingagents=ta, quant=quant)
