"""LLM runtimes for Merlin and Quant, with call/cost metering.

Three runtimes, chosen in config (never silently switched):

- `scripted`    deterministic test double. Clearly labelled in every record it
                touches; used for reproducible integration tests.
- `claude_cli`  headless Claude Code (`claude -p`) in a fresh temporary
                directory per call: a separate execution context, no project
                settings, no MCP servers, no session persistence. Works where
                Claude Code is signed in (Claude cloud sessions, Ed's Mac)
                without a Python API key. Cost is the CLI's reported list-price
                `total_cost_usd`; whether that is billed to a subscription or an
                API account depends on how Claude Code is signed in.
- `anthropic_api` the Anthropic Python SDK with ANTHROPIC_API_KEY (or
                MERLIN_QUANT_ANTHROPIC_API_KEY). Always sent to api.anthropic.com.

`CLIChatModel` adapts any runtime to a LangChain chat model with tool calling,
so the real TradingAgents graph can run on it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


class RuntimeUnavailable(RuntimeError):
    pass


@dataclass
class CallResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    seconds: float = 0.0
    context_id: str = ""
    tools_used: list = field(default_factory=list)


@dataclass
class Meter:
    calls: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_known: bool = True
    models: set = field(default_factory=set)
    contexts: list = field(default_factory=list)
    seconds: float = 0.0

    def add(self, r: CallResult):
        self.calls += 1
        self.input_tokens += r.input_tokens
        self.output_tokens += r.output_tokens
        self.seconds += r.seconds
        self.models.add(r.model)
        self.contexts.append(r.context_id)
        if r.cost_usd is None:
            self.cost_known = False
        else:
            self.cost_usd += r.cost_usd


class Runtime:
    name = "base"

    def __init__(self, model: str):
        self.model = model
        self.meter = Meter()

    def complete(self, system: str, prompt: str, *, model: str | None = None) -> CallResult:
        t0 = time.time()
        try:
            r = self._complete(system, prompt, model or self.model)
        except Exception:
            self.meter.failed += 1
            raise
        r.seconds = r.seconds or (time.time() - t0)
        self.meter.add(r)
        return r

    def _complete(self, system, prompt, model) -> CallResult:  # pragma: no cover
        raise NotImplementedError


class ScriptedRuntime(Runtime):
    """Deterministic responder: `responder(system, prompt) -> text`. TEST DOUBLE."""
    name = "scripted"

    def __init__(self, responder: Callable[[str, str], str], model: str = "scripted-test-double"):
        super().__init__(model)
        self.responder = responder
        self.transcript: list[tuple[str, str, str]] = []

    def _complete(self, system, prompt, model):
        text = self.responder(system, prompt)
        self.transcript.append((system, prompt, text))
        return CallResult(text=text, model=model, cost_usd=0.0, context_id=f"scripted-{uuid.uuid4().hex[:8]}")


class ClaudeCLIRuntime(Runtime):
    name = "claude_cli"

    def __init__(self, model: str, plugin_dirs: list[str] | None = None, tools: str = "",
                 timeout: int = 600, effort: str | None = None):
        super().__init__(model)
        self.plugin_dirs = plugin_dirs or []
        self.tools = tools
        self.timeout = timeout
        self.effort = effort
        self.exe = shutil.which("claude")
        if not self.exe:
            raise RuntimeUnavailable("the `claude` CLI is not on PATH")

    def _complete(self, system, prompt, model):
        ctx = tempfile.mkdtemp(prefix="mq-ctx-")  # a fresh, empty working dir per call
        cmd = [self.exe, "-p", prompt, "--model", model, "--system-prompt", system,
               "--tools", self.tools, "--strict-mcp-config", "--no-session-persistence",
               "--output-format", "stream-json", "--verbose"]
        if not self.plugin_dirs:
            cmd += ["--setting-sources", ""]
        for d in self.plugin_dirs:
            cmd += ["--plugin-dir", d]
        if self.effort:
            cmd += ["--effort", self.effort]
        env = {k: v for k, v in os.environ.items() if not k.startswith("MQ_SECRET")}
        t0 = time.time()
        try:
            out = subprocess.run(cmd, cwd=ctx, capture_output=True, text=True, timeout=self.timeout, env=env)
        finally:
            shutil.rmtree(ctx, ignore_errors=True)
        d, tools_used = None, []
        for line in out.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result":
                d = ev
            elif ev.get("type") == "assistant":
                for block in ev.get("message", {}).get("content", []) or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tools_used.append({"name": block.get("name"), "input": block.get("input")})
        if d is None:
            raise RuntimeUnavailable(f"claude CLI returned no result (exit {out.returncode}): "
                                     f"{(out.stderr or out.stdout)[-300:]}")
        if d.get("is_error") or d.get("subtype") not in (None, "success"):
            raise RuntimeUnavailable(f"claude CLI error: {str(d.get('result'))[:300]}")
        u = d.get("usage", {})
        models = list(d.get("modelUsage", {})) or [model]
        return CallResult(text=d.get("result", ""), model=",".join(models),
                          input_tokens=u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                          + u.get("cache_creation_input_tokens", 0),
                          output_tokens=u.get("output_tokens", 0), cost_usd=d.get("total_cost_usd"),
                          seconds=time.time() - t0, context_id=f"claude-session-{d.get('session_id', '?')}",
                          tools_used=tools_used)


class AnthropicAPIRuntime(Runtime):
    name = "anthropic_api"
    # list prices per million tokens (input, output); update with the price list
    PRICES = {"claude-haiku-5-5": (1.0, 5.0), "claude-sonnet-5-5": (3.0, 15.0), "claude-opus-5-5": (5.0, 25.0)}

    def __init__(self, model: str):
        super().__init__(model)
        key = os.environ.get("MERLIN_QUANT_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeUnavailable("no ANTHROPIC_API_KEY / MERLIN_QUANT_ANTHROPIC_API_KEY set")
        import anthropic  # noqa: PLC0415
        self.client = anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com")

    def _complete(self, system, prompt, model):
        t0 = time.time()
        m = self.client.messages.create(model=model, max_tokens=4000, system=system,
                                        messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in m.content if getattr(b, "type", "") == "text")
        pi, po = self.PRICES.get(model, (None, None))
        cost = None if pi is None else (m.usage.input_tokens * pi + m.usage.output_tokens * po) / 1e6
        return CallResult(text=text, model=model, input_tokens=m.usage.input_tokens,
                          output_tokens=m.usage.output_tokens, cost_usd=cost, seconds=time.time() - t0,
                          context_id=f"api-{m.id}")


def make_runtime(spec: dict, *, model_key: str = "model", responder=None, plugin_dirs=None, tools: str = "") -> Runtime:
    kind = spec.get("runtime", "scripted")
    model = spec.get(model_key) or spec.get("model") or "claude-sonnet-5-5"
    if kind == "scripted":
        if responder is None:
            raise RuntimeUnavailable("scripted runtime needs a responder")
        return ScriptedRuntime(responder)
    if kind == "claude_cli":
        return ClaudeCLIRuntime(model, plugin_dirs=plugin_dirs, tools=tools)
    if kind == "anthropic_api":
        return AnthropicAPIRuntime(model)
    raise RuntimeUnavailable(f"unknown runtime {kind!r}")


def extract_json(text: str) -> dict:
    """The first JSON object in a model reply (fenced or bare)."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    blob = m.group(1) if m else None
    if blob is None:
        start = text.find("{")
        if start < 0:
            raise ValueError("no JSON object in model output")
        depth = 0
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                blob = text[start:i + 1]
                break
    if blob is None:
        raise ValueError("unterminated JSON object in model output")
    return json.loads(blob)


# ====================================================================== LangChain adapter

def build_chat_model(runtime: Runtime, model: str):
    """A LangChain chat model backed by *runtime*, with JSON tool calling."""
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.utils.function_calling import convert_to_openai_tool

    TOOL_PROTOCOL = (
        "\n\nTOOLS. You may call these tools:\n{tools}\n"
        "To call tools, reply with ONLY a JSON object and nothing else: "
        '{{"tool_calls": [{{"name": "<tool name>", "args": {{...}}}}]}}. '
        "When you have the data you need, reply with your final answer as plain text (no JSON wrapper)."
    )

    def render(messages) -> tuple[str, str]:
        system, parts = [], []
        for m in messages:
            if isinstance(m, SystemMessage):
                system.append(str(m.content))
            elif isinstance(m, ToolMessage):
                parts.append(f"[TOOL RESULT {m.name or ''}]\n{m.content}")
            elif isinstance(m, AIMessage):
                calls = "; ".join(f"{c['name']}({json.dumps(c['args'])})" for c in (m.tool_calls or []))
                parts.append(f"[ASSISTANT]\n{m.content}" + (f"\n[called {calls}]" if calls else ""))
            elif isinstance(m, HumanMessage):
                parts.append(f"[USER]\n{m.content}")
            else:
                parts.append(str(getattr(m, "content", m)))
        return "\n\n".join(system) or "You are a helpful financial analysis assistant.", "\n\n".join(parts) or "Begin."

    class CLIChatModel(BaseChatModel):
        rt: Any
        model_name: str

        @property
        def _llm_type(self) -> str:
            return f"merlin-quant-{self.rt.name}"

        def bind_tools(self, tools, **kwargs):
            return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

        def with_structured_output(self, schema, **kwargs):
            # TradingAgents falls back to its free-text path when this is unsupported.
            raise NotImplementedError("structured output not offered; TradingAgents uses its free-text path")

        def _generate(self, messages, stop=None, run_manager=None, tools=None, **kwargs):
            system, prompt = render(messages)
            if tools:
                spec = "\n".join(f"- {t['function']['name']}: {t['function'].get('description', '')[:300]} "
                                 f"params={json.dumps(t['function'].get('parameters', {}).get('properties', {}))}"
                                 for t in tools)
                system += TOOL_PROTOCOL.format(tools=spec)
            r = self.rt.complete(system, prompt, model=self.model_name)
            text = r.text.strip()
            msg = AIMessage(content=text)
            if tools and '"tool_calls"' in text:
                try:
                    calls = extract_json(text)["tool_calls"]
                    names = {t["function"]["name"] for t in tools}
                    tc = [{"name": c["name"], "args": c.get("args", {}), "id": f"call_{uuid.uuid4().hex[:10]}",
                           "type": "tool_call"} for c in calls if c.get("name") in names]
                    if tc:
                        msg = AIMessage(content="", tool_calls=tc)
                except (ValueError, KeyError, TypeError):
                    pass
            msg.response_metadata = {"model": r.model, "cost_usd": r.cost_usd}
            return ChatResult(generations=[ChatGeneration(message=msg)])

    return CLIChatModel(rt=runtime, model_name=model)
