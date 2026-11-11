"""Minimal LLM client abstraction with usage telemetry and response caching.

The LLM is used only for narrative work (explanations, counter-thesis wording).
It never decides whether numeric data is valid, never computes fills or returns.

Backends:
- AnthropicLLM: real calls via the `anthropic` SDK when ANTHROPIC_API_KEY is set.
  NOT VERIFIED in this build environment (no key available).
- MockLLM: deterministic, labelled responses for tests and offline demos.
- NullLLM: returns None; callers fall back to deterministic output.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .audit import cost_for


@dataclass
class LLMResponse:
    text: Optional[str]
    model: Optional[str]
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit: bool = False
    label: str = "LIVE"   # LIVE | MOCKED | UNAVAILABLE

    @property
    def usage(self) -> dict:
        return {"model": self.model, "provider": self.provider, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cache_hit": self.cache_hit,
                "cost_usd": 0.0 if self.cache_hit else cost_for(self.model, self.input_tokens, self.output_tokens)}


class BaseLLM:
    provider = "none"

    def __init__(self, cache_dir: Optional[str | Path] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, system: str, prompt: str, model: str) -> str:
        return hashlib.sha256(json.dumps([self.provider, model, system, prompt]).encode()).hexdigest()

    def complete(self, system: str, prompt: str, model: str = "claude-sonnet-5", max_tokens: int = 1200) -> LLMResponse:
        key = self._key(system, prompt, model)
        if self.cache_dir and (self.cache_dir / f"{key}.json").exists():
            d = json.loads((self.cache_dir / f"{key}.json").read_text())
            return LLMResponse(**{**d, "cache_hit": True})
        resp = self._complete(system, prompt, model, max_tokens)
        if self.cache_dir and resp.text is not None:
            (self.cache_dir / f"{key}.json").write_text(json.dumps(resp.__dict__))
        return resp

    def _complete(self, system, prompt, model, max_tokens) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError


class NullLLM(BaseLLM):
    provider = "none"

    def _complete(self, system, prompt, model, max_tokens) -> LLMResponse:
        return LLMResponse(None, None, self.provider, label="UNAVAILABLE")


class MockLLM(BaseLLM):
    provider = "mock"

    def __init__(self, reply: str = "MOCKED LLM RESPONSE", **kw):
        super().__init__(**kw)
        self.reply = reply
        self.calls = 0

    def _complete(self, system, prompt, model, max_tokens) -> LLMResponse:
        self.calls += 1
        return LLMResponse(self.reply, model, self.provider, input_tokens=len(prompt) // 4,
                           output_tokens=len(self.reply) // 4, label="MOCKED")


class AnthropicLLM(BaseLLM):  # NOT VERIFIED: no API key in the build environment
    provider = "anthropic"

    def _complete(self, system, prompt, model, max_tokens) -> LLMResponse:
        import anthropic  # imported lazily; optional dependency
        key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("MARCUS_ANTHROPIC_API_KEY")
        client = anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com")
        msg = client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                     messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return LLMResponse(text, model, self.provider, msg.usage.input_tokens, msg.usage.output_tokens)


def default_llm(cache_dir: Optional[str | Path] = None) -> BaseLLM:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("MARCUS_ANTHROPIC_API_KEY"):
        return AnthropicLLM(cache_dir=cache_dir)
    return NullLLM(cache_dir=cache_dir)
