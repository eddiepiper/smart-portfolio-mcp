"""Opt-in checks against real runtimes (cost money): MERLIN_QUANT_LIVE=1 pytest -m live."""

import os
import shutil
from datetime import date

import pytest

from merlin_quant.contracts import Stance
from merlin_quant.data import synthetic_fixture
from merlin_quant.merlin import FSI_PLUGINS, run_merlin
from merlin_quant.quant import run_quant
from merlin_quant.runtime import ClaudeCLIRuntime
from merlin_quant.workflow import FSI_HOME

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.environ.get("MERLIN_QUANT_LIVE") != "1" or not shutil.which("claude"),
                                                    reason="set MERLIN_QUANT_LIVE=1 with a signed-in claude CLI")]
CUTOFF = date(2026, 11, 6)


def test_live_merlin_invokes_fsi_skills():
    dirs = [str(FSI_HOME / "plugins" / p) for p in FSI_PLUGINS]
    rt = ClaudeCLIRuntime("claude-sonnet-5-5", plugin_dirs=dirs, tools="Skill,Read")
    r = run_merlin(synthetic_fixture().view(CUTOFF), "AAPL", ["AAPL", "MSFT", "NVDA"], CUTOFF, rt)
    assert r.fsi_skills_used, "no financial-services skill was invoked"
    assert r.run.cost_usd is not None and r.stance in set(Stance)


def test_live_quant_tradingagents_on_claude():
    q = run_quant(synthetic_fixture().view(CUTOFF), "NVDA", "SPY", ClaudeCLIRuntime("claude-haiku-5-5"),
                  ClaudeCLIRuntime("claude-sonnet-5-5"), quick_model="claude-haiku-5-5", deep_model="claude-sonnet-5-5")
    assert q.tradingagents_rating in {"Buy", "Overweight", "Hold", "Underweight", "Sell"}, q.run.notes
    assert q.run.model_calls >= 10
