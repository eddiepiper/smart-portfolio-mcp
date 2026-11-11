"""Common agent scaffolding: identity, instructions, config, tool registration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from ..core.orchestration import ToolSpec
from ..core.runtime import Runtime

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def load_instruments() -> dict:
    return json.loads((CONFIG_DIR / "instruments.json").read_text())


class Agent:
    agent_id = "base"

    def __init__(self, rt: Runtime):
        self.rt = rt
        here = Path(__file__).resolve().parent / self.agent_id
        self.instructions = (here / "instructions.md").read_text() if (here / "instructions.md").exists() else ""
        self.config = json.loads((here / "config.json").read_text()) if (here / "config.json").exists() else {}

    def tool(self, name: str, fn: Callable, capability: str, *, state_changing: bool = False,
             timeout_s: float = 60.0, retries: int = 1) -> None:
        self.rt.dispatcher.register(ToolSpec(self.agent_id, name, fn, capability, state_changing, timeout_s, retries))

    def register(self) -> "Agent":  # pragma: no cover - overridden
        return self
