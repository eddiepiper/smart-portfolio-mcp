"""Assemble the full V2 team (used by the CLI, the Claude skill and end-to-end tests)."""
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
    if quant is None:
        from ..agents.quant.agent import Quant
        quant = QuantAdapter(Quant(rt, mi=mi, dg=dg).register())       # cloud-native Quant (rebuilt 2026-11-10)
    ta = tradingagents or TradingAgentsAdapter(rt.home / "tradingagents")
    return Marcus(rt, mi=mi, dg=dg, sl=sl, rc=rc, ex=ex, pa=pa, tradingagents=ta, quant=quant)
