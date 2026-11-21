"""Vendored from virattt/ai-hedge-fund @ 5c553b5222044f6a5527bd377b4104c50ee0ad28 (MIT, see LICENSE).

Only hedge_fund/brokers/{models,costs,sim,paper}.py and paths.write_atomic are
copied. The code is unchanged apart from import paths and one patch: SimBroker
passes the fill price to `TradingCosts.commission(quantity, price)`, so the
engine's cost model can charge a percentage of trade value (a broker's
"0.06%, minimum USD 15" schedule). The upstream per-share model ignores it. The investor-persona
agents, signals, LLM layer and pipeline are deliberately NOT vendored: Merlin and
Quant are the only research leads, and this package is execution and accounting.

Why vendored rather than pip-installed: aihf 2.7.2 pins pandas ^2 / numpy ^1 /
langchain-anthropic 0.3.5, which conflict with TradingAgents 0.7.1 (pandas>=3,
langchain-anthropic>=1.7). The four broker modules depend only on pydantic.
"""
UPSTREAM_REPO = "https://github.com/virattt/ai-hedge-fund"
UPSTREAM_COMMIT = "5c553b5222044f6a5527bd377b4104c50ee0ad28"

from .costs import TradingCosts  # noqa: E402
from .models import Fill, Order, Position  # noqa: E402
from .paper import PaperBroker  # noqa: E402
from .sim import SimBroker  # noqa: E402

__all__ = ["TradingCosts", "Fill", "Order", "Position", "PaperBroker", "SimBroker"]
