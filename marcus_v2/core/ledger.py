"""Append-only, hash-chained paper portfolio ledger (JSON Lines).

- Events are never edited or deleted. Corrections are new events.
- Each event stores the previous event's hash; `verify()` detects any tampering.
- Every write carries an idempotency key: replaying the same key returns the
  original event instead of creating a duplicate (duplicate alerts cannot create
  duplicate fills or contributions).
- Writes go through a write-ahead marker. An interrupted write leaves either a
  complete line or a torn trailing line; on open, a torn line is moved to a
  quarantine file (never silently "fixed") and reported for reconciliation.
- `LedgerReader` is the read-only view given to the Portfolio Analyst.
"""
from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

from .contracts import LedgerEvent, LedgerEventType, utcnow

GENESIS = "0" * 64


class LedgerError(Exception):
    pass


class LedgerIntegrityError(LedgerError):
    pass


def _canonical(event: LedgerEvent) -> str:
    data = event.model_dump(mode="json")
    data.pop("hash", None)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def compute_hash(event: LedgerEvent) -> str:
    return hashlib.sha256(_canonical(event).encode()).hexdigest()


class LedgerReader:
    """Read-only access. Holds no write methods."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)

    def events(self) -> list[LedgerEvent]:
        out: list[LedgerEvent] = []
        if not self.path.exists():
            return out
        with self.path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(LedgerEvent.model_validate_json(line))
                except Exception as exc:  # torn or corrupt line
                    raise LedgerIntegrityError(f"unreadable ledger line: {exc}") from exc
        return out

    def verify(self) -> list[str]:
        """Return a list of integrity problems (empty = chain intact)."""
        problems = []
        prev = GENESIS
        for i, ev in enumerate(self.events(), start=1):
            if ev.sequence != i:
                problems.append(f"sequence gap at {ev.event_id}: expected {i}, got {ev.sequence}")
            if ev.prev_hash != prev:
                problems.append(f"broken link at {ev.event_id}")
            if compute_hash(ev) != ev.hash:
                problems.append(f"hash mismatch at {ev.event_id} (record altered)")
            prev = ev.hash
        return problems

    def head(self) -> Optional[str]:
        evs = self.events()
        return evs[-1].hash if evs else None


class Ledger(LedgerReader):
    """Writable ledger. Only the Execution Specialist and the approved capital
    workflow receive an instance of this class (see core.permissions)."""

    def __init__(self, path: str | os.PathLike):
        super().__init__(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.wal = self.path.with_suffix(self.path.suffix + ".wal")
        self.quarantine = self.path.with_suffix(self.path.suffix + ".quarantine")
        self.recovery_notes: list[str] = self._recover()

    # -- recovery ---------------------------------------------------------------
    def _recover(self) -> list[str]:
        notes = []
        if self.path.exists():
            raw = self.path.read_bytes()
            if raw and not raw.endswith(b"\n"):
                cut = raw.rfind(b"\n") + 1
                torn = raw[cut:]
                with self.quarantine.open("ab") as q:
                    q.write(torn + b"\n")
                with self.path.open("r+b") as f:
                    f.truncate(cut)
                notes.append(f"torn trailing write ({len(torn)} bytes) quarantined to {self.quarantine.name}")
        if self.wal.exists():
            pending = json.loads(self.wal.read_text())
            committed = {e.idempotency_key for e in self.events()}
            if pending.get("idempotency_key") in committed:
                notes.append(f"write-ahead entry {pending.get('idempotency_key')} was committed; marker cleared")
            else:
                notes.append(f"write-ahead entry {pending.get('idempotency_key')} was NOT committed; needs reconciliation")
                with self.quarantine.open("a") as q:
                    q.write(json.dumps({"uncommitted_wal": pending}) + "\n")
            self.wal.unlink()
        problems = self.verify() if self.path.exists() else []
        if problems:
            raise LedgerIntegrityError("; ".join(problems))
        return notes

    # -- writes -------------------------------------------------------------------
    def find_by_key(self, key: str) -> Optional[LedgerEvent]:
        for ev in self.events():
            if ev.idempotency_key == key:
                return ev
        return None

    def append(self, event: LedgerEvent, _crash_after_wal: bool = False) -> LedgerEvent:
        existing = self.find_by_key(event.idempotency_key)
        if existing is not None:
            return existing                       # idempotent replay
        evs = self.events()
        event.sequence = len(evs) + 1
        event.prev_hash = evs[-1].hash if evs else GENESIS
        event.recorded_at = utcnow()
        event.hash = compute_hash(event)
        line = event.model_dump_json() + "\n"
        self.wal.write_text(json.dumps({"idempotency_key": event.idempotency_key, "event_id": event.event_id}))
        if _crash_after_wal:                      # test hook: simulate a crash mid-write
            with self.path.open("a") as f:
                f.write(line[: len(line) // 2])
            raise LedgerError("simulated crash during ledger write")
        with self.path.open("a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        self.wal.unlink()
        return event


# ------------------------------------------------------------------- derived state
def balances(events: Iterable[LedgerEvent]) -> dict:
    """Cash by currency, positions by instrument, contributed capital by currency."""
    cash: dict[str, Decimal] = {}
    contributed: dict[str, Decimal] = {}
    positions: dict[str, dict] = {}
    realised: dict[str, Decimal] = {}
    for ev in events:
        if ev.event_type == LedgerEventType.ADJUSTMENT_NOTE:
            continue
        cash[ev.currency] = cash.get(ev.currency, Decimal("0")) + ev.amount
        if ev.event_type in (LedgerEventType.CONTRIBUTION, LedgerEventType.IMPORT) and ev.instrument_id is None:
            contributed[ev.currency] = contributed.get(ev.currency, Decimal("0")) + ev.amount
        if ev.event_type == LedgerEventType.WITHDRAWAL:
            contributed[ev.currency] = contributed.get(ev.currency, Decimal("0")) + ev.amount
        if ev.instrument_id and ev.event_type == LedgerEventType.BUY:
            p = positions.setdefault(ev.instrument_id, {"quantity": Decimal("0"), "cost": Decimal("0"), "currency": ev.currency})
            p["quantity"] += ev.quantity
            p["cost"] += -ev.amount          # amount is negative cash; includes fees
        if ev.instrument_id and ev.event_type == LedgerEventType.SELL:
            p = positions.setdefault(ev.instrument_id, {"quantity": Decimal("0"), "cost": Decimal("0"), "currency": ev.currency})
            if p["quantity"] <= 0:
                raise LedgerIntegrityError(f"sell of {ev.instrument_id} with no position ({ev.event_id})")
            avg = p["cost"] / p["quantity"]
            cost_out = avg * ev.quantity
            realised[ev.currency] = realised.get(ev.currency, Decimal("0")) + (ev.amount - cost_out)
            p["quantity"] -= ev.quantity
            p["cost"] -= cost_out
    positions = {k: v for k, v in positions.items() if v["quantity"] != 0}
    return {"cash": cash, "contributed": contributed, "positions": positions, "realised": realised}
