"""Live provider checks. Opt-in: MARCUS_V2_LIVE=1 (needs network access to Yahoo)."""
import os
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("MARCUS_V2_LIVE") != "1", reason="set MARCUS_V2_LIVE=1 to run live provider tests")


def test_openbb_and_yahoo_live_cross_check(rt):
    from marcus_v2.agents.data_guardian.agent import DataGuardian
    from marcus_v2.agents.market_intelligence.agent import MarketIntelligence
    mi = MarketIntelligence(rt).register()
    dg = DataGuardian(rt).register()
    end = date.today()
    cmp = mi.compare_data_providers("US:BAC", end - timedelta(days=30), end)
    reps = cmp["_reports"]
    assert reps["openbb"].status.value in ("ok", "partial") and reps["openbb"].observations, "OpenBB returned no bars"
    assert reps["yahoo"].observations
    dq = dg.compare_provider_observations(reps["openbb"].observations, reps["yahoo"].observations, correlation_id="live")
    print("openbb vs yahoo:", dq.status.value, [i.message for i in dq.issues][:5])
    assert dq.status.value in ("VALID", "WARNING", "REJECTED")   # discrepancies are reported, not hidden
