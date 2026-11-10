"""Lightweight in-process event bus / task dispatcher.

No always-on service: tasks run in the caller's process, which is enough for a
Mac mini and for being driven by Claude's tool calls. Provides:
typed task routing to registered agent tools, permission checks, idempotency
(the same idempotency key never runs a state-changing task twice), timeouts,
bounded retries for transient errors, and an execution log entry per call.
"""
from __future__ import annotations

import concurrent.futures as cf
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .audit import AuditLog
from .contracts import AgentExecutionLog, new_id, utcnow
from .permissions import PermissionDenied, require


class TransientError(Exception):
    """Raise from a tool for errors worth retrying (network blips, rate limits)."""


@dataclass
class ToolSpec:
    agent_id: str
    name: str
    fn: Callable[..., Any]
    capability: str
    state_changing: bool = False
    timeout_s: float = 60.0
    retries: int = 1


@dataclass
class TaskResult:
    status: str
    output: Any = None
    error: Optional[str] = None
    log_id: Optional[str] = None
    cached: bool = False


@dataclass
class Dispatcher:
    audit: AuditLog
    tools: dict[str, ToolSpec] = field(default_factory=dict)
    _done: dict[str, TaskResult] = field(default_factory=dict)
    subscribers: dict[str, list[Callable[[dict], None]]] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> None:
        self.tools[f"{spec.agent_id}.{spec.name}"] = spec

    def subscribe(self, topic: str, handler: Callable[[dict], None]) -> None:
        self.subscribers.setdefault(topic, []).append(handler)

    def publish(self, topic: str, message: dict) -> None:
        self.audit.log_event("publish", message.get("correlation_id", "-"), topic=topic)
        for h in self.subscribers.get(topic, []):
            h(message)

    def call(self, agent_id: str, tool: str, *, correlation_id: Optional[str] = None,
             idempotency_key: Optional[str] = None, **kwargs: Any) -> TaskResult:
        key = f"{agent_id}.{tool}"
        spec = self.tools.get(key)
        correlation_id = correlation_id or new_id("corr")
        if spec is None:
            return TaskResult("failed", error=f"unknown tool {key}")
        if spec.state_changing:
            if not idempotency_key:
                return TaskResult("rejected", error="state-changing tool requires an idempotency_key")
            if idempotency_key in self._done:
                prior = self._done[idempotency_key]
                return TaskResult(prior.status, prior.output, prior.error, prior.log_id, cached=True)
        try:
            if "correlation_id" in inspect.signature(spec.fn).parameters:
                kwargs["correlation_id"] = correlation_id      # propagate the trace id into the tool
        except (TypeError, ValueError):
            pass
        started = utcnow()
        t0 = time.monotonic()
        status, output, error = "ok", None, None
        try:
            require(agent_id, spec.capability)
            attempt = 0
            while True:
                attempt += 1
                try:
                    with cf.ThreadPoolExecutor(max_workers=1) as ex:
                        output = ex.submit(spec.fn, **kwargs).result(timeout=spec.timeout_s)
                    break
                except TransientError as exc:
                    if attempt > spec.retries:
                        raise
                    time.sleep(min(2 ** attempt, 8) * 0.01)
        except PermissionDenied as exc:
            status, error = "rejected", str(exc)
        except cf.TimeoutError:
            status, error = "timeout", f"{key} exceeded {spec.timeout_s}s"
        except Exception as exc:  # noqa: BLE001 - every failure is logged, never swallowed silently
            status, error = "failed", f"{type(exc).__name__}: {exc}"
        usage = getattr(output, "_usage", None) or {}
        entry = AgentExecutionLog(
            agent_id=agent_id, tool=tool, correlation_id=correlation_id,
            status=status, started_at=started, finished_at=utcnow(),
            latency_ms=int((time.monotonic() - t0) * 1000), error=error,
            input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
            model=usage.get("model"), provider=usage.get("provider"), cost_usd=usage.get("cost_usd", 0.0),
            cache_hit=bool(usage.get("cache_hit")),
        )
        self.audit.log_execution(entry)
        result = TaskResult(status, output, error, entry.log_id)
        if spec.state_changing and status == "ok":
            self._done[idempotency_key] = result
        return result
