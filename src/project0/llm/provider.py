"""Thin LLM provider interface.

Only one method: `complete`. No streaming (Telegram does not natively stream),
no tool use (Secretary does not need it; sub-project 6b will add a sibling
method when Manager needs it). Prompt caching is an implementation detail of
`AnthropicProvider` — it does not leak into the interface, so local-model
providers can simply ignore it.

Implementations:
  - `FakeProvider`: tests. Canned responses or a callable; records every call.
  - `AnthropicProvider`: added in sub-project 6a Task 5 — wraps
    `anthropic.AsyncAnthropic`, enables prompt caching on the system prompt
    block. Not present in this file yet.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol


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
