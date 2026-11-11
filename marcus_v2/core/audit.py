"""Structured audit log, evidence store and per-agent usage telemetry (JSON Lines)."""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from .contracts import AgentExecutionLog, Contract, new_id, utcnow

# Model prices (USD per million input/output tokens) for cost telemetry are read
# from MARCUS_V2_MODEL_PRICES (a JSON file: {"model": [input, output]}). None are
# hard-coded: unverified prices would make the cost report fiction. Models without
# a configured price are reported with cost 0.0 and listed under "unpriced_models".
import os as _os

def _load_prices() -> dict:
    p = _os.environ.get("MARCUS_V2_MODEL_PRICES")
    if p and Path(p).exists():
        return {k: tuple(v) for k, v in json.loads(Path(p).read_text()).items()}
    return {}

MODEL_PRICES = _load_prices()


class AuditLog:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _append(self, name: str, record: dict) -> None:
        with self._lock, (self.dir / name).open("a") as f:
            f.write(json.dumps(record, default=str, sort_keys=True) + "\n")

    def log_execution(self, entry: AgentExecutionLog) -> None:
        self._append("agent_executions.jsonl", entry.model_dump(mode="json"))

    def log_event(self, kind: str, correlation_id: str, **fields: Any) -> None:
        self._append("events.jsonl", {"kind": kind, "correlation_id": correlation_id, "at": utcnow().isoformat(), **fields})

    def read(self, name: str) -> list[dict]:
        p = self.dir / name
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def telemetry(self) -> dict[str, dict]:
        """Invocations, failures, latency, tokens, cost and cache hits per agent."""
        agg: dict[str, dict] = defaultdict(lambda: {"invocations": 0, "ok": 0, "failed": 0, "latency_ms_total": 0,
                                                    "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                                                    "cache_hits": 0, "models": set()})
        for r in self.read("agent_executions.jsonl"):
            a = agg[r["agent_id"]]
            a["invocations"] += 1
            a["ok" if r["status"] == "ok" else "failed"] += 1
            a["latency_ms_total"] += r["latency_ms"]
            a["input_tokens"] += r.get("input_tokens", 0)
            a["output_tokens"] += r.get("output_tokens", 0)
            a["cost_usd"] += r.get("cost_usd", 0.0)
            a["cache_hits"] += 1 if r.get("cache_hit") else 0
            if r.get("model"):
                a["models"].add(r["model"])
        return {k: {**v, "models": sorted(v["models"])} for k, v in agg.items()}


class EvidenceStore:
    """Content-addressed store for reports and artefacts referenced by id."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def put(self, obj: Contract | dict, kind: str, obj_id: Optional[str] = None) -> str:
        data = obj.model_dump(mode="json") if isinstance(obj, Contract) else obj
        obj_id = obj_id or data.get("report_id") or data.get("result_id") or data.get("recommendation_id") or new_id(kind)
        (self.dir / f"{obj_id}.json").write_text(json.dumps({"kind": kind, "data": data}, default=str, indent=2))
        return obj_id

    def get(self, obj_id: str) -> dict:
        p = self.dir / f"{obj_id}.json"
        if not p.exists():
            raise KeyError(obj_id)
        return json.loads(p.read_text())["data"]

    def exists(self, obj_id: str) -> bool:
        return (self.dir / f"{obj_id}.json").exists()


def cost_for(model: Optional[str], input_tokens: int, output_tokens: int) -> float:
    if not model or model not in MODEL_PRICES:
        return 0.0
    pin, pout = MODEL_PRICES[model]
    return round(input_tokens / 1e6 * pin + output_tokens / 1e6 * pout, 6)
