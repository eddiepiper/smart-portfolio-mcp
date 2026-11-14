"""The ledger: the single source of truth for the paper portfolio.

An append-only, hash-chained JSONL event log (the design ai-hedge-fund uses for
its paper funds, simplified to one file). Portfolio state is never stored on its
own: it is rebuilt by folding events in order, so it cannot drift. Editing,
removing or reordering a line breaks the chain and `verify()` refuses.

Every append carries an idempotency key. Appending the same key again returns the
existing event instead of writing a second one; the same key with a different
payload raises, because that would be two different facts claiming one identity.

The broker book (ai-hedge-fund PaperBroker JSON) is a second, independent book.
`engine.PaperEngine` reconciles the two on every start and refuses to trade when
they disagree, unless the difference is exactly one interrupted fill it can prove.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


class LedgerError(RuntimeError):
    pass


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Holding:
    quantity: int = 0
    cost: float = 0.0  # total cost basis incl. buy fees

    @property
    def avg_cost(self) -> float:
        return self.cost / self.quantity if self.quantity else 0.0


@dataclass
class State:
    initialized: bool = False
    initial_capital: float = 0.0
    base_currency: str = ""
    cash: float = 0.0
    holdings: dict[str, Holding] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fees: float = 0.0
    dividends: float = 0.0
    nav_history: list[dict] = field(default_factory=list)
    open_intents: dict[str, dict] = field(default_factory=dict)  # execution_id -> intent payload
    filled: set[str] = field(default_factory=set)
    closed_trades: list[dict] = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""
    mode: str = "PAPER_MANUAL"
    config_version: str = ""
    applied_actions: set[str] = field(default_factory=set)

    def shares(self) -> dict[str, int]:
        return {s: h.quantity for s, h in self.holdings.items() if h.quantity}


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    # ------------------------------------------------------------ read
    def events(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def verify(self) -> list[dict]:
        evs, prev = self.events(), "GENESIS"
        for i, e in enumerate(evs):
            body = {k: v for k, v in e.items() if k != "hash"}
            if e.get("seq") != i or e.get("prev_hash") != prev or _hash(body) != e.get("hash"):
                raise LedgerError(f"ledger chain broken at line {i + 1} of {self.path}")
            prev = e["hash"]
        return evs

    def head(self) -> str:
        evs = self.events()
        return evs[-1]["hash"][:12] if evs else "empty"

    def find(self, idem_key: str) -> dict | None:
        for e in self.events():
            if e["idem_key"] == idem_key:
                return e
        return None

    # ------------------------------------------------------------ write
    def append(self, etype: str, idem_key: str, payload: dict) -> tuple[dict, bool]:
        """Append an event; returns (event, created). Idempotent on idem_key."""
        evs = self.verify()
        for e in evs:
            if e["idem_key"] == idem_key:
                if _hash(e["payload"]) != _hash(json.loads(json.dumps(payload, default=str))):
                    raise LedgerError(f"idempotency key {idem_key} reused with a different payload")
                return e, False
        body = {
            "seq": len(evs), "ts": datetime.now(timezone.utc).isoformat(), "type": etype,
            "idem_key": idem_key, "payload": json.loads(json.dumps(payload, default=str)),
            "prev_hash": evs[-1]["hash"] if evs else "GENESIS",
        }
        body["hash"] = _hash(body)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(body, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return body, True

    # ------------------------------------------------------------ fold
    def state(self) -> State:
        st = State()
        for e in self.verify():
            p, t = e["payload"], e["type"]
            if t == "INIT":
                st.initialized, st.initial_capital, st.cash = True, p["initial_capital"], p["initial_capital"]
                st.base_currency, st.mode, st.config_version = p["base_currency"], p["mode"], p["config_version"]
            elif t == "CONFIG":
                st.config_version = p["config_version"]
            elif t == "MODE":
                st.mode = p["mode"]
            elif t == "HALT":
                st.halted, st.halt_reason = True, p["reason"]
            elif t == "RESUME":
                st.halted, st.halt_reason = False, ""
            elif t == "ORDER_INTENT":
                st.open_intents[p["execution_id"]] = p
            elif t == "ORDER_ABANDONED":
                st.open_intents.pop(p["execution_id"], None)
            elif t == "FILL":
                st.open_intents.pop(p["execution_id"], None)
                st.filled.add(p["proposal_id"])
                _apply_fill(st, p)
            elif t == "CORPORATE_ACTION":
                st.applied_actions.add(p["action_id"])
                if p["type"] == "split" and p["added_shares"]:
                    st.holdings.setdefault(p["instrument"], Holding()).quantity += p["added_shares"]
                elif p["type"] == "dividend":
                    st.cash += p["cash"]
                    st.dividends += p["cash"]
            elif t == "NAV":
                st.nav_history.append(p)
        return st


def _apply_fill(st: State, p: dict) -> None:
    sym, q, px, fee = p["instrument"], p["quantity"], p["fill_price"], p["commission"]
    h = st.holdings.setdefault(sym, Holding())
    st.fees += fee
    if p["side"] == "buy":
        st.cash -= q * px + fee
        h.cost += q * px + fee
        h.quantity += q
    else:
        if q > h.quantity:
            raise LedgerError(f"fill sells {q} {sym} but only {h.quantity} held (no shorting in this engine)")
        basis = h.avg_cost * q
        proceeds = q * px - fee
        st.cash += proceeds
        pnl = proceeds - basis
        st.realized_pnl += pnl
        st.closed_trades.append({"instrument": sym, "quantity": q, "pnl": pnl, "proposal_id": p["proposal_id"],
                                 "session": p["fill_session"]})
        h.cost -= basis
        h.quantity -= q
        if h.quantity == 0:
            h.cost = 0.0
