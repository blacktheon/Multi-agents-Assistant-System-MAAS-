"""Thin LLM provider interface.

Only one method: `complete`. No streaming (Telegram does not natively stream),
no tool use (Secretary does not need it; sub-project 6b will add a sibling
method when Manager needs it). Prompt caching is an implementation detail of
`AnthropicProvider` — it does not leak into the interface, so local-model
providers can simply ignore it.

Implementations:
  - `FakeProvider`: tests. Canned responses or a callable; records every call.
  - `AnthropicProvider`: wraps `anthropic.AsyncAnthropic` and enables prompt
    caching on the system prompt block (long stable persona) so volatile
    per-turn content in `messages` reuses the cached prefix.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

import anthropic
from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, TextBlockParam

log = logging.getLogger(__name__)


class LLMProviderError(Exception):
    """Raised when the provider cannot produce a response. Agents catch this
    at their boundary and log + drop the turn."""


@dataclass
class Msg:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class ProviderCall:
    system: str
    messages: list[Msg]
    max_tokens: int


class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
    ) -> str:
        ...


@dataclass
class FakeProvider:
    """Test-only provider. Either pre-loaded with canned responses or driven
    by a callable that can inspect inputs."""

    responses: list[str] | None = None
    callable_: Callable[[str, list[Msg]], str] | None = None
    calls: list[ProviderCall] = field(default_factory=list)
    _idx: int = 0

    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
    ) -> str:
        self.calls.append(
            ProviderCall(system=system, messages=list(messages), max_tokens=max_tokens)
        )
        if self.callable_ is not None:
            return self.callable_(system, messages)
        if self.responses is None:
            raise LLMProviderError("FakeProvider has neither responses nor callable_")
        if self._idx >= len(self.responses):
            raise LLMProviderError(
                f"FakeProvider exhausted: {len(self.responses)} canned responses, "
                f"{self._idx + 1} requested"
            )
        out = self.responses[self._idx]
        self._idx += 1
        return out


class AnthropicProvider:
    """Real provider. Prompt caching is enabled on the system prompt — pass
    the long stable persona prompt in `system` and the volatile per-turn
    transcript in `messages` to benefit."""

    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
    ) -> str:
        sdk_messages: list[MessageParam] = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        system_block: list[TextBlockParam] = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system_block,
                messages=sdk_messages,
            )
        except (anthropic.APIError, TimeoutError) as e:
            log.exception("anthropic call failed")
            raise LLMProviderError(f"anthropic {type(e).__name__}") from e

        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", None)
                if text:
                    return str(text)
        raise LLMProviderError("anthropic response contained no text block")
