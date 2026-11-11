"""Builds the shared services (ledger, mandates, audit, evidence, dispatcher) under one home dir."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .audit import AuditLog, EvidenceStore
from .ledger import Ledger, LedgerReader
from .llm import BaseLLM, default_llm
from .mandate import MandateStore
from .orchestration import Dispatcher
from .permissions import assert_no_broker_credentials

DEFAULT_HOME = Path(os.environ.get("MARCUS_V2_HOME", "var/marcus_v2"))


@dataclass
class Runtime:
    home: Path
    audit: AuditLog
    evidence: EvidenceStore
    mandates: MandateStore
    dispatcher: Dispatcher
    llm: BaseLLM
    portfolio_id: str = "marcus-paper"

    @property
    def ledger_path(self) -> Path:
        return self.home / "ledger" / f"{self.portfolio_id}.jsonl"

    def ledger_writer(self) -> Ledger:
        return Ledger(self.ledger_path)

    def ledger_reader(self) -> LedgerReader:
        return LedgerReader(self.ledger_path)

    @property
    def cache_dir(self) -> Path:
        p = self.home / "cache"
        p.mkdir(parents=True, exist_ok=True)
        return p


def build_runtime(home: str | Path | None = None, llm: BaseLLM | None = None,
                  environ: dict | None = None) -> Runtime:
    assert_no_broker_credentials(dict(os.environ if environ is None else environ))
    home = Path(home or DEFAULT_HOME)
    home.mkdir(parents=True, exist_ok=True)
    audit = AuditLog(home / "audit")
    return Runtime(
        home=home,
        audit=audit,
        evidence=EvidenceStore(home / "evidence"),
        mandates=MandateStore(home / "mandates"),
        dispatcher=Dispatcher(audit=audit),
        llm=llm or default_llm(home / "llm_cache"),
    )
