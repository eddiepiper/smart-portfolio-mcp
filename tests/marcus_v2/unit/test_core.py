from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from marcus_v2.core.contracts import LedgerEvent, LedgerEventType, PaperOrder, OrderType
from marcus_v2.core.ledger import Ledger, LedgerError, LedgerIntegrityError, LedgerReader, balances
from marcus_v2.core.mandate import MandateError, MandateStore, default_v2_mandate
from marcus_v2.core.orchestration import ToolSpec, TransientError
from marcus_v2.core.permissions import (LiveTradingForbidden, PermissionDenied, assert_no_broker_credentials,
                                        assert_no_live_trading, require)

T = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)


def ev(key, etype, amount, **kw):
    return LedgerEvent(event_type=etype, portfolio_id="p", currency=kw.pop("currency", "USD"),
                       amount=Decimal(amount), event_at=kw.pop("at", T), idempotency_key=key, **kw)


def test_ledger_chain_and_idempotency(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    a = led.append(ev("c1", LedgerEventType.CONTRIBUTION, "500"))
    b = led.append(ev("b1", LedgerEventType.BUY, "-203.60", instrument_id="US:BAC", quantity=Decimal(4), price=Decimal("50.90")))
    again = led.append(ev("b1", LedgerEventType.BUY, "-203.60", instrument_id="US:BAC", quantity=Decimal(4)))
    assert again.event_id == b.event_id and len(led.events()) == 2
    assert led.verify() == []
    s = balances(led.events())
    assert s["cash"]["USD"] == Decimal("296.40")
    assert s["positions"]["US:BAC"]["quantity"] == 4
    assert s["contributed"]["USD"] == Decimal("500")


def test_ledger_tamper_detected(tmp_path):
    p = tmp_path / "l.jsonl"
    led = Ledger(p)
    led.append(ev("c1", LedgerEventType.CONTRIBUTION, "500"))
    led.append(ev("c2", LedgerEventType.CONTRIBUTION, "100"))
    p.write_text(p.read_text().replace('"amount":"100"', '"amount":"900"'))
    assert any("hash mismatch" in x for x in LedgerReader(p).verify())
    with pytest.raises(LedgerIntegrityError):
        Ledger(p)


def test_interrupted_write_recovers_without_inventing(tmp_path):
    p = tmp_path / "l.jsonl"
    led = Ledger(p)
    led.append(ev("c1", LedgerEventType.CONTRIBUTION, "500"))
    with pytest.raises(LedgerError):
        led.append(ev("b1", LedgerEventType.BUY, "-100", instrument_id="US:X", quantity=Decimal(1)), _crash_after_wal=True)
    led2 = Ledger(p)
    assert len(led2.events()) == 1                      # the torn buy is NOT counted
    assert any("quarantined" in n for n in led2.recovery_notes)
    assert any("NOT committed" in n for n in led2.recovery_notes)
    assert led2.verify() == []
    again = led2.append(ev("b1", LedgerEventType.BUY, "-100", instrument_id="US:X", quantity=Decimal(1)))
    assert again.sequence == 2


def test_sell_without_position_rejected(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append(ev("s1", LedgerEventType.SELL, "100", instrument_id="US:X", quantity=Decimal(1)))
    with pytest.raises(LedgerIntegrityError):
        balances(led.events())


def test_mandate_versioning_and_owner_approval(tmp_path):
    ms = MandateStore(tmp_path)
    m = ms.propose(default_v2_mandate(1))
    with pytest.raises(MandateError):
        ms.active("marcus-paper", date(2026, 11, 10))        # pending is not active
    with pytest.raises(MandateError):
        ms.approve("marcus-paper", 1, "marcus")               # an agent cannot approve
    ms.approve("marcus-paper", 1, "owner:edward")
    act = ms.active("marcus-paper", date(2026, 11, 10))
    assert act.paper_capital == Decimal("20000") and act.paper_only is True
    with pytest.raises(MandateError):
        ms.propose(default_v2_mandate(1))                      # versions immutable


def test_mandate_cannot_be_real_money():
    with pytest.raises(Exception):
        default_v2_mandate().model_copy(update={}).__class__(**{**default_v2_mandate().model_dump(), "paper_only": False})


def test_paper_order_must_be_paper():
    base = dict(order_id="o", portfolio_id="p", instrument_id="US:X", side="buy", quantity=Decimal(1),
                order_type=OrderType.MARKET, recommendation_id="r", mandate_id="m", mandate_version=1,
                decision_at=T, currency="USD")
    PaperOrder(**base)
    with pytest.raises(Exception):
        PaperOrder(**base, paper=False)


def test_permissions():
    require("execution_specialist", "append_ledger")
    for agent in ["portfolio_analyst", "research_challenger", "strategy_lab", "market_intelligence", "marcus"]:
        with pytest.raises(PermissionDenied):
            require(agent, "append_ledger")
    with pytest.raises(PermissionDenied):
        require("data_guardian", "write_recommendation")


@pytest.mark.parametrize("cmd", ["lean live deploy", ["lean", "live", "--brokerage", "x"], "place_stock_order", "alpaca", "--live"])
def test_live_commands_refused(cmd):
    with pytest.raises(LiveTradingForbidden):
        assert_no_live_trading(cmd)


def test_broker_credentials_refused():
    with pytest.raises(LiveTradingForbidden):
        assert_no_broker_credentials({"ALPACA_API_KEY": "x"})
    assert_no_broker_credentials({"PATH": "/bin"})


def test_dispatcher_idempotency_permissions_retry_timeout(rt):
    calls = {"n": 0}

    def write(x):
        calls["n"] += 1
        return x * 2

    rt.dispatcher.register(ToolSpec("execution_specialist", "w", write, "append_ledger", state_changing=True))
    r1 = rt.dispatcher.call("execution_specialist", "w", idempotency_key="k", x=2)
    r2 = rt.dispatcher.call("execution_specialist", "w", idempotency_key="k", x=2)
    assert r1.output == 4 and r2.cached and calls["n"] == 1
    assert rt.dispatcher.call("execution_specialist", "w", x=2).status == "rejected"

    rt.dispatcher.register(ToolSpec("portfolio_analyst", "w", write, "append_ledger"))
    assert rt.dispatcher.call("portfolio_analyst", "w", x=1).status == "rejected"

    flaky = {"n": 0}

    def sometimes(x):
        flaky["n"] += 1
        if flaky["n"] == 1:
            raise TransientError("blip")
        return x

    rt.dispatcher.register(ToolSpec("market_intelligence", "s", sometimes, "read_market_data", retries=2))
    assert rt.dispatcher.call("market_intelligence", "s", x=3).output == 3

    import time
    rt.dispatcher.register(ToolSpec("market_intelligence", "slow", lambda: time.sleep(2), "read_market_data", timeout_s=0.2))
    assert rt.dispatcher.call("market_intelligence", "slow").status == "timeout"
    tel = rt.audit.telemetry()
    assert tel["market_intelligence"]["invocations"] == 2 and tel["portfolio_analyst"]["failed"] == 1
