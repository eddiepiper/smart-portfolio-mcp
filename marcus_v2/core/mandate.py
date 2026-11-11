"""Versioned investment mandates.

Mandates are immutable once written: a change is a new version. Only an APPROVED
version (owner approval recorded) is "active". Agents read the active mandate;
none can approve or edit one (see core.permissions).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .contracts import ApprovalStatus, InvestmentMandate, utcnow


class MandateError(Exception):
    pass


class MandateStore:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, mandate_id: str, version: int) -> Path:
        return self.dir / f"{mandate_id}.v{version}.json"

    def versions(self, mandate_id: str) -> list[InvestmentMandate]:
        out = [InvestmentMandate.model_validate_json(p.read_text()) for p in self.dir.glob(f"{mandate_id}.v*.json")
               if not p.name.endswith(".approval.json")]
        return sorted(out, key=lambda m: m.version)

    def propose(self, mandate: InvestmentMandate) -> InvestmentMandate:
        existing = self.versions(mandate.mandate_id)
        expected = (existing[-1].version + 1) if existing else 1
        if mandate.version != expected:
            raise MandateError(f"next version must be {expected}, got {mandate.version}")
        mandate.status = ApprovalStatus.PENDING
        mandate.approved_by = None
        mandate.approved_at = None
        p = self._path(mandate.mandate_id, mandate.version)
        if p.exists():
            raise MandateError("mandate versions are immutable")
        p.write_text(mandate.model_dump_json(indent=2))
        return mandate

    def approve(self, mandate_id: str, version: int, approver: str) -> InvestmentMandate:
        """Owner action only. Records approval as a sidecar so the proposal stays byte-identical."""
        if not approver or approver.lower() in {"marcus", "agent"} or approver.startswith("agent:"):
            raise MandateError("mandates are approved by the human owner, not by an agent")
        m = self.get(mandate_id, version)
        side = self.dir / f"{mandate_id}.v{version}.approval.json"
        if side.exists():
            raise MandateError("already decided")
        side.write_text(json.dumps({"approved_by": approver, "approved_at": utcnow().isoformat()}))
        return self.get(mandate_id, version)

    def get(self, mandate_id: str, version: int) -> InvestmentMandate:
        p = self._path(mandate_id, version)
        if not p.exists():
            raise MandateError(f"no mandate {mandate_id} v{version}")
        m = InvestmentMandate.model_validate_json(p.read_text())
        side = self.dir / f"{mandate_id}.v{version}.approval.json"
        if side.exists():
            d = json.loads(side.read_text())
            m.status = ApprovalStatus.APPROVED
            m.approved_by = d["approved_by"]
            m.approved_at = datetime.fromisoformat(d["approved_at"])
        return m

    def active(self, mandate_id: str, on: Optional[date] = None) -> InvestmentMandate:
        on = on or date.today()
        approved = [self.get(mandate_id, m.version) for m in self.versions(mandate_id)]
        approved = [m for m in approved if m.status == ApprovalStatus.APPROVED and m.effective_from <= on]
        if not approved:
            raise MandateError(f"no approved mandate {mandate_id} in effect on {on}")
        return approved[-1]


def default_v2_mandate(version: int = 1) -> InvestmentMandate:
    """The S$20,000 paper mandate described in the V2 directive.

    Risk limits are NOT invented here: they are carried over from the existing
    approved paper configuration (trader/config.json: max loss 10 of 500 = 2%,
    daily loss 25 of 500 = 5%, cash buffer 5%, position cap 95%, no short/margin/
    options) as percentages. They take effect only after owner approval.
    """
    return InvestmentMandate(
        mandate_id="marcus-paper",
        version=version,
        effective_from=date(2026, 11, 10),
        base_currency="SGD",
        paper_capital=Decimal("20000"),
        max_position_pct=Decimal("95"),
        max_loss_per_trade_pct=Decimal("2"),
        max_daily_loss_pct=Decimal("5"),
        cash_buffer_pct=Decimal("5"),
        no_chase_pct=Decimal("0.5"),
        notes=("Limits carried over as percentages from the existing approved $500 paper config "
               "(trader/config.json). Needs owner approval; awaiting confirmation of whether "
               "the original $500 was SGD or USD."),
    )
