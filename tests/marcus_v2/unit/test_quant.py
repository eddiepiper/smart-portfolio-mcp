from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from marcus_v2.agents.data_guardian.agent import DataGuardian
from marcus_v2.agents.market_intelligence.agent import MarketIntelligence
from marcus_v2.agents.quant.agent import Quant, bootstrap_mean_ci
from marcus_v2.agents.strategy_lab.agent import StrategyLab, frame_from_report
from marcus_v2.core.contracts import BacktestRequest
from marcus_v2.integrations import vectorbt_adapter as vba
from marcus_v2.integrations.existing import QuantAdapter
from marcus_v2.integrations.openbb_adapter import FixtureProvider

FIX = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 11, 10, 7, 30, tzinfo=timezone.utc)


@pytest.fixture
def lab(rt):
    mi = MarketIntelligence(rt, providers={"fixture": FixtureProvider(FIX / "bars.json")}, clock=lambda: NOW)
    dg = DataGuardian(rt, clock=lambda: NOW)
    sl = StrategyLab(rt)
    q = Quant(rt, mi=mi, dg=dg).register()

    def backtest(iid="US:SPY", sym="SPY", entry=None, exit_=None):
        r = mi.get_historical_prices(iid, date(2025, 10, 10), date(2026, 11, 9))
        dg.validate_report(r, purpose="research")
        req = BacktestRequest(strategy_id="t", instrument_id=iid, symbol=sym, start=date(2025, 10, 10), end=date(2026, 11, 9),
                              data_version=vba.data_version(frame_from_report(r)),
                              entry_rule=entry or {"type": "sma_cross", "fast": 10, "slow": 30},
                              exit_rule=exit_ or {"type": "sma_cross_down", "fast": 10, "slow": 30}, initial_cash=Decimal("20000"))
        return sl.run_strategy_backtest(req, r).model_dump(mode="json")
    return q, backtest


def test_real_backtest_reviewed_independently(lab):
    q, backtest = lab
    bt = backtest()
    out = q.review({"request_id": "r1", "evidence": {"instrument_id": "US:SPY", "data_quality_status": "VALID", "backtest": bt}})
    assert out["verdict"] in ("CHALLENGED", "INSUFFICIENT_EVIDENCE", "SUPPORTED")
    assert any("independent buy-and-hold" in f for f in out["findings"])          # own data pull and recomputation
    if bt["trades"] < 10:
        assert out["verdict"] != "SUPPORTED"                                      # stricter than Strategy Lab's minimum


def test_tampered_benchmark_is_caught(lab):
    q, backtest = lab
    bt = backtest()
    bt["benchmark_metrics"]["total_return_pct"] += 25.0
    out = q.review({"request_id": "r2", "evidence": {"instrument_id": "US:SPY", "backtest": bt}})
    assert out["verdict"] == "CHALLENGED" and any("inputs or computation differ" in f for f in out["findings"])


def test_leakage_and_missing_evidence(lab):
    q, backtest = lab
    leaky = backtest(entry={"type": "custom", "fn": lambda d: d["close"].shift(-1) > d["close"]})
    assert q.review({"evidence": {"backtest": leaky}})["verdict"] == "CHALLENGED"
    assert q.review({"evidence": {}})["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert q.review({"evidence": {"backtest": {"status": "OK"}, "data_quality_status": "REJECTED"}})["verdict"] == "INSUFFICIENT_EVIDENCE"


def _bt(trade_returns, total=10.0, bench=5.0):
    log = [{"entry_price": 100.0, "exit_price": 100.0 * (1 + r), "status": "Closed"} for r in trade_returns]
    return {"status": "OK", "metrics": {"trade_log": log, "total_return_pct": total}, "benchmark_metrics": {"total_return_pct": bench},
            "sensitivity": {}, "out_of_sample": {"out_of_sample": {"trades": 3}}}


def test_significance_rules(rt):
    q = Quant(rt)
    noisy = [0.05, -0.05, 0.04, -0.045, 0.03, -0.03, 0.02, -0.025, 0.01, -0.01, 0.015, -0.012]
    assert q.review({"evidence": {"backtest": _bt(noisy)}})["verdict"] == "CHALLENGED"           # CI spans zero
    steady = [0.02, 0.025, 0.018, 0.03, 0.022, 0.019, 0.027, 0.021, 0.024, 0.02, 0.023, 0.026]
    assert q.review({"evidence": {"backtest": _bt(steady)}})["verdict"] == "SUPPORTED"
    assert q.review({"evidence": {"backtest": _bt(steady, total=2.0, bench=8.0)}})["verdict"] == "CHALLENGED"   # lags buy-and-hold
    assert q.review({"evidence": {"backtest": _bt(steady[:5])}})["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_bootstrap_is_reproducible():
    v = [0.01, -0.02, 0.03, 0.005, -0.01]
    assert bootstrap_mean_ci(v) == bootstrap_mean_ci(v)


def test_quant_through_adapter_never_sees_marcus_verdict(lab):
    q, backtest = lab
    seen = {}

    class Spy:
        def review(self, request):
            seen.update(request)
            return q.review(request)
    review = QuantAdapter(Spy()).request_review(["x"], {"instrument_id": "US:SPY", "backtest": backtest()})
    assert review.verdict in ("SUPPORTED", "CHALLENGED", "INSUFFICIENT_EVIDENCE")
    assert "recommendation" not in str(seen).lower() and "action" not in seen.get("evidence", {})
