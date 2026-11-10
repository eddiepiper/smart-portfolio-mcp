"""Security: real-money trading must be technically unavailable."""
import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from marcus_v2.core.permissions import (POLICY, LiveTradingForbidden, PermissionDenied, assert_no_broker_credentials,
                                        assert_no_live_trading, require)
from marcus_v2.core.runtime import build_runtime

PKG = Path(__file__).resolve().parents[3] / "marcus_v2"
BROKER_MODULES = {"alpaca", "alpaca_trade_api", "ib_insync", "ibapi", "ccxt", "x402", "eth_account", "web3",
                  "tradier", "oandapyV20", "binance", "coinbase", "robin_stocks", "schwab", "tda"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods.add(n.module.split(".")[0])
    return mods


def test_no_broker_sdk_imported_anywhere():
    hits = {str(p.relative_to(PKG)): sorted(_imports(p) & BROKER_MODULES) for p in PKG.rglob("*.py")}
    assert not {k: v for k, v in hits.items() if v}


def test_no_live_trading_entry_points_in_tools(rt):
    from marcus_v2.integrations.openbb_adapter import FixtureProvider
    from marcus_v2.marcus.team import build_team
    fx = Path(__file__).resolve().parents[1] / "fixtures" / "bars.json"
    build_team(rt, providers={"fixture": FixtureProvider(fx)})
    names = " ".join(rt.dispatcher.tools)
    for bad in ("live", "place_order", "submit_order", "broker", "withdraw", "transfer"):
        assert bad not in names.lower(), bad


def test_only_execution_specialist_can_write_ledger():
    writers = [a for a, caps in POLICY.items() if "append_ledger" in caps]
    assert writers == ["execution_specialist"]


@pytest.mark.parametrize("cmd", [["lean", "live", "deploy"], "lean live", "--live", "alpaca place_stock_order",
                                 "LEAN LIVE", "x402 pay", "interactive-brokers"])
def test_live_paths_refused(cmd):
    with pytest.raises(LiveTradingForbidden):
        assert_no_live_trading(cmd)


def test_runtime_refuses_to_start_with_broker_credentials(tmp_path):
    with pytest.raises(LiveTradingForbidden):
        build_runtime(tmp_path, environ={"ALPACA_API_KEY": "pk", "ALPACA_SECRET_KEY": "sk"})


def test_lean_adapter_cannot_build_live_command():
    from marcus_v2.integrations import lean_adapter
    with pytest.raises(LiveTradingForbidden):
        lean_adapter.build_command("live", "/tmp/x")
    cmd = lean_adapter.build_command("backtest", "/tmp/x")
    assert cmd[cmd.index("--network") + 1] == "none"


def test_paper_flags_cannot_be_disabled():
    from marcus_v2.core.mandate import default_v2_mandate
    from marcus_v2.core.contracts import InvestmentMandate
    d = default_v2_mandate().model_dump()
    d["paper_only"] = False
    with pytest.raises(Exception):
        InvestmentMandate(**d)


def test_cli_owner_actions_need_owner(tmp_path):
    root = PKG.parent
    for cmd in ("approve", "import_legacy", "mandate_approve"):
        r = subprocess.run([sys.executable, "-m", "marcus_v2", cmd, "--home", str(tmp_path), "--payload", "{}"],
                           capture_output=True, text=True, cwd=root)
        assert r.returncode == 2 and "owner" in r.stdout


def test_cli_has_no_trading_or_credential_commands():
    src = (PKG / "__main__.py").read_text().lower()
    assert "api_key" not in src and "secret" not in src and "place_order" not in src


def test_agents_cannot_exceed_permissions():
    for agent in POLICY:
        if agent != "marcus":
            with pytest.raises(PermissionDenied):
                require(agent, "write_recommendation")
