from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from marcus_v2.agents.data_guardian.agent import DataGuardian
from marcus_v2.agents.market_intelligence.agent import MarketIntelligence
from marcus_v2.agents.strategy_lab.agent import DataNotValidated, StrategyLab, frame_from_report
from marcus_v2.core.contracts import BacktestRequest
from marcus_v2.integrations import vectorbt_adapter as vba
from marcus_v2.integrations.openbb_adapter import FixtureProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 11, 10, 7, 30, tzinfo=timezone.utc)


@pytest.fixture
def setup(rt):
    mi = MarketIntelligence(rt, providers={"fixture": FixtureProvider(FIXTURES / "bars.json")}, clock=lambda: NOW).register()
    dg = DataGuardian(rt, clock=lambda: NOW).register()
    sl = StrategyLab(rt).register()

    def validated(iid, start=date(2025, 10, 10), end=date(2026, 11, 9)):
        r = mi.get_historical_prices(iid, start, end)
        dg.validate_report(r, purpose="research")
        return r
    return sl, validated


def req(**kw):
    base = dict(strategy_id="sma20_50", instrument_id="US:SPY", symbol="SPY", start=date(2025, 10, 10),
                end=date(2026, 11, 9), data_version="", entry_rule={"type": "sma_cross", "fast": 10, "slow": 30},
                exit_rule={"type": "sma_cross_down", "fast": 10, "slow": 30}, initial_cash=Decimal("20000"))
    base.update(kw)
    return BacktestRequest(**base)


def test_backtest_runs_reproducibly_with_costs_and_benchmark(setup):
    sl, validated = setup
    rep = validated("US:SPY")
    a = sl.run_strategy_backtest(req(), rep)
    b = sl.run_strategy_backtest(req(), rep)
    assert a.status in ("OK", "INSUFFICIENT_DATA") and a.observations > 200
    assert a.metrics == b.metrics and a.reproducibility["result_hash"] == b.reproducibility["result_hash"]
    assert a.reproducibility["data_version"] == vba.data_version(frame_from_report(rep))
    assert a.benchmark_metrics["trades"] == 1
    if a.trades:
        assert a.metrics["total_fees"] > 0
        assert a.sensitivity["fees"]["fees_x0"] >= a.sensitivity["fees"]["fees_x4"]
    assert "out_of_sample" in a.out_of_sample and a.sensitivity["parameters"]["variants"]


def test_transaction_costs_are_charged():
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=10, freq="D")
    df = pd.DataFrame({"open": [100.0] * 10, "high": [100.0] * 10, "low": [100.0] * 10, "close": [100.0] * 10}, index=idx)
    rule = {"type": "custom", "fn": lambda d: pd.Series([True] + [False] * 9, index=d.index)}
    flat = vba.run_backtest(df, rule, {"type": "none"}, init_cash=1000, fees=0.0, fixed_fees=0.0, slippage=0.0)
    costly = vba.run_backtest(df, rule, {"type": "none"}, init_cash=1000, fees=0.01, fixed_fees=15.0, slippage=0.0)
    assert flat["final_value"] == pytest.approx(1000)
    assert costly["final_value"] < 1000 - 15 and costly["total_fees"] > 15


def test_lookahead_fixture_is_detected(setup):
    sl, validated = setup
    leaky = {"type": "custom", "fn": lambda d: d["close"].shift(-1) > d["close"]}   # peeks at tomorrow
    res = sl.run_strategy_backtest(req(entry_rule=leaky), validated("US:SPY"))
    assert res.status == "LEAKAGE_DETECTED" and res.concerns


def test_insufficient_data(setup):
    sl, validated = setup
    res = sl.run_strategy_backtest(req(), validated("US:SPY", date(2026, 9, 1), date(2026, 11, 9)))
    assert res.status == "INSUFFICIENT_DATA"


def test_parameter_sensitivity_reported(setup):
    sl, validated = setup
    res = sl.run_strategy_backtest(req(entry_rule={"type": "close_above_ma", "window": 20},
                                       exit_rule={"type": "close_below_ma", "window": 20}), validated("US:BAC"))
    assert len(res.sensitivity["parameters"]["variants"]) == 4


def test_unvalidated_data_refused(rt, setup):
    sl, _ = setup
    mi = MarketIntelligence(rt, providers={"fixture": FixtureProvider(FIXTURES / "bars.json")}, clock=lambda: NOW)
    raw = mi.get_historical_prices("US:SPY", date(2025, 10, 10), date(2026, 11, 9))
    with pytest.raises(DataNotValidated):
        sl.run_strategy_backtest(req(), raw)
