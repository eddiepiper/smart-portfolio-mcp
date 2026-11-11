from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from marcus_v2.agents.data_guardian.agent import DataGuardian
from marcus_v2.agents.market_intelligence.agent import MarketIntelligence
from marcus_v2.core.contracts import PriceType, QualityStatus, RetrievalStatus
from marcus_v2.integrations.openbb_adapter import FixtureProvider, RawBars

from pathlib import Path
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

NOW = datetime(2026, 11, 10, 7, 30, tzinfo=timezone.utc)      # 02:30 New York, Tue
clock = lambda: NOW


class CountingProvider(FixtureProvider):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls = 0

    def historical(self, *a, **k):
        self.calls += 1
        return super().historical(*a, **k)


class DownProvider:
    name = "down"

    def historical(self, symbol, start, end, interval="1d"):
        return RawBars(self.name, symbol, None, [], error="ConnectionError: provider unreachable")


@pytest.fixture
def mi(rt):
    p = CountingProvider(FIXTURES / "bars.json", name="fixture")
    return MarketIntelligence(rt, providers={"fixture": p, "down": DownProvider()}, clock=clock).register()


@pytest.fixture
def dg(rt):
    return DataGuardian(rt, clock=clock).register()


def test_history_units_timestamps_and_cache(mi):
    r = mi.get_historical_prices("US:BAC", date(2026, 10, 1), date(2026, 11, 9))
    assert r.status == RetrievalStatus.OK and r.coverage["bars"] > 20
    last = r.observations[-1]
    assert last.currency == "USD" and last.price_unit == "per_share" and last.bar_date == date(2026, 11, 9)
    assert last.close == Decimal("50.55") and last.price_type == PriceType.OFFICIAL_CLOSE
    assert last.observed_at is not None and last.retrieved_at == NOW
    assert r.data_quality_status is None                 # not yet validated
    calls = mi.providers["fixture"].calls
    r2 = mi.get_historical_prices("US:BAC", date(2026, 10, 1), date(2026, 11, 9))
    assert r2.coverage["cache_hit"] and mi.providers["fixture"].calls == calls


def test_missing_provider_access_is_reported_not_invented(mi):
    r = mi.get_historical_prices("US:BAC", date(2026, 10, 1), date(2026, 11, 9), provider="down")
    assert r.status == RetrievalStatus.FAILED and r.observations == []
    assert "unreachable" in r.missing_information[0]


def test_unknown_instrument(mi):
    with pytest.raises(KeyError):
        mi.get_historical_prices("US:NOPE", date(2026, 10, 1), date(2026, 11, 9))


def test_context_and_macro(mi):
    r = mi.get_market_context("US:PFE")
    assert r.context["bars_used"] >= 200 and "ma200" in r.context and isinstance(r.context["uptrend"], bool)
    assert mi.get_macro_indicators(["CPIAUCSL"])["status"] == "unsupported"


def test_valid_equity_history(mi, dg):
    r = mi.get_historical_prices("US:BAC", date(2026, 10, 1), date(2026, 11, 9))
    dq = dg.validate_report(r, purpose="decision")
    assert dq.status in (QualityStatus.VALID, QualityStatus.WARNING)
    assert not dq.rejected_observation_ids
    assert r.data_quality_status == dq.status


def test_provisional_gold_cannot_masquerade_as_daily_close(mi, dg):
    r = mi.get_historical_prices("COMEX:GC", date(2026, 11, 1), date(2026, 11, 10))
    today = [o for o in r.observations if o.bar_date == date(2026, 11, 10)][0]
    assert today.price_type == PriceType.PROVISIONAL_INTRADAY
    forged = today.model_copy(update={"price_type": PriceType.OFFICIAL_CLOSE})
    dq = dg.validate_observations([forged], correlation_id="c", purpose="decision")
    assert dq.status == QualityStatus.REJECTED
    assert any(i.check == "price_type" for i in dq.issues)
    assert forged.price_type == PriceType.OFFICIAL_CLOSE      # source record untouched


def test_duplicates_dropped_and_conflicts_rejected(mi, dg):
    o = mi.get_historical_prices("US:BAC", date(2026, 11, 9), date(2026, 11, 9)).observations[0]
    dup = o.model_copy(update={"observation_id": "obs_dup"})
    dq = dg.validate_observations([o, dup], correlation_id="c")
    assert dq.status == QualityStatus.WARNING and dq.rejected_observation_ids == ["obs_dup"]
    clash = o.model_copy(update={"observation_id": "obs_clash", "close": Decimal("55")})
    assert dg.validate_observations([o, clash], correlation_id="c").status == QualityStatus.REJECTED
    again = dg.validate_observations([o], correlation_id="c", seen_event_ids={o.event_id})
    assert again.accepted_observation_ids == []


def test_wrong_units_and_currency_rejected(mi, dg):
    g = mi.get_historical_prices("COMEX:GC", date(2026, 11, 9), date(2026, 11, 9)).observations[0]
    per_gram = g.model_copy(update={"close": (g.close / Decimal("31.1034768")).quantize(Decimal("0.01")),
                                    "open": None, "high": None, "low": None})
    dq = dg.validate_observations([per_gram], correlation_id="c", reference_price=g.close)
    assert dq.status == QualityStatus.REJECTED and any(i.check == "quote_convention" for i in dq.issues)
    sgd = g.model_copy(update={"currency": "SGD"})
    assert any(i.check == "currency" for i in dg.validate_observations([sgd], correlation_id="c").issues)
    unit = g.model_copy(update={"price_unit": "per_gram"})
    assert dg.validate_observations([unit], correlation_id="c").status == QualityStatus.REJECTED


def test_invalid_numbers_rejected(mi, dg):
    o = mi.get_historical_prices("US:BAC", date(2026, 11, 9), date(2026, 11, 9)).observations[0]
    for bad in ({"close": Decimal("-1")}, {"close": Decimal("0")}, {"high": Decimal("40")}):
        dq = dg.validate_observations([o.model_copy(update=bad)], correlation_id="c")
        assert dq.status == QualityStatus.REJECTED, bad


def test_stale_prices_block_decisions(mi, dg):
    r = mi.get_historical_prices("US:BAC", date(2026, 9, 1), date(2026, 10, 1))
    assert dg.validate_report(r, purpose="decision").status == QualityStatus.REJECTED
    r2 = mi.get_historical_prices("US:BAC", date(2026, 9, 1), date(2026, 10, 1))
    assert dg.validate_report(r2, purpose="research").status == QualityStatus.WARNING


def test_missing_data_never_becomes_data(dg):
    dq = dg.validate_observations([], correlation_id="c")
    assert dq.status == QualityStatus.REJECTED and dq.accepted_observation_ids == []


def test_provider_discrepancy(mi, dg):
    a = mi.get_historical_prices("US:BAC", date(2026, 11, 9), date(2026, 11, 9)).observations
    b = [x.model_copy(update={"close": x.close * Decimal("1.004"), "source_provider": "other"}) for x in a]
    assert dg.compare_provider_observations(a, b, correlation_id="c").status == QualityStatus.WARNING
    c = [x.model_copy(update={"close": x.close * Decimal("1.05"), "source_provider": "other"}) for x in a]
    assert dg.compare_provider_observations(a, c, correlation_id="c").status == QualityStatus.REJECTED


def test_llm_explanation_cannot_change_status(mi, dg):
    o = mi.get_historical_prices("US:BAC", date(2026, 11, 9), date(2026, 11, 9)).observations[0]
    dq = dg.validate_observations([o.model_copy(update={"close": Decimal("0")})], correlation_id="c")
    out = dg.explain_exceptions(dq)
    assert out.status == QualityStatus.REJECTED and out.explanation
