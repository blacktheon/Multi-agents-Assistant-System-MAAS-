"""Thin LLM provider interface.

Two methods: `complete` (plain text) and `complete_with_tools` (tool-use).
No streaming (Telegram does not natively stream). Prompt caching is an
implementation detail of `AnthropicProvider` — it does not leak into the
interface, so local-model providers can simply ignore it.

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
from typing import Any, Literal, Protocol

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, TextBlockParam

from project0.llm.tools import AssistantToolUseMsg, ToolCall, ToolResultMsg, ToolSpec, ToolUseResult

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
    thinking_budget_tokens: int | None = None


class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
        thinking_budget_tokens: int | None = None,
    ) -> str:
        ...

    async def complete_with_tools(
        self,
        *,
        system: str,
        messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
    ) -> ToolUseResult:
        ...


@dataclass
class FakeProvider:
    """Test-only provider. Either pre-loaded with canned responses or driven
    by a callable that can inspect inputs."""

    responses: list[str] | None = None
    callable_: Callable[[str, list[Msg]], str] | None = None
    calls: list[ProviderCall] = field(default_factory=list)
    tool_responses: list[ToolUseResult] | None = None
    tool_calls_log: list[dict] = field(default_factory=list)
    _idx: int = 0
    _tool_idx: int = 0

    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
        thinking_budget_tokens: int | None = None,
    ) -> str:
        self.calls.append(
            ProviderCall(
                system=system,
                messages=list(messages),
                max_tokens=max_tokens,
                thinking_budget_tokens=thinking_budget_tokens,
            )
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

    async def complete_with_tools(
        self,
        *,
        system: str,
        messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
    ) -> ToolUseResult:
        self.tool_calls_log.append(
            {
                "system": system,
                "messages": list(messages),
                "tools": list(tools),
                "max_tokens": max_tokens,
            }
        )
        if self.tool_responses is None:
            raise LLMProviderError(
                "FakeProvider.complete_with_tools called but tool_responses is None"
            )
        if self._tool_idx >= len(self.tool_responses):
            raise LLMProviderError(
                f"FakeProvider.complete_with_tools exhausted: "
                f"{len(self.tool_responses)} canned tool_responses, "
                f"{self._tool_idx + 1} requested"
            )
        out = self.tool_responses[self._tool_idx]
        self._tool_idx += 1
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
        thinking_budget_tokens: int | None = None,
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
        extra: dict[str, Any] = {}
        if thinking_budget_tokens is not None:
            extra["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget_tokens,
            }
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system_block,
                messages=sdk_messages,
                **extra,
            )
        except Exception as e:
            log.exception("anthropic call failed")
            raise LLMProviderError(f"anthropic {type(e).__name__}") from e

        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", None)
                if text:
                    return str(text)
        raise LLMProviderError("anthropic response contained no text block")

    async def complete_with_tools(
        self,
        *,
        system: str,
        messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
    ) -> ToolUseResult:
        sdk_messages: list[dict[str, Any]] = []
        for m in messages:
            if isinstance(m, Msg):
                sdk_messages.append({"role": m.role, "content": m.content})
            elif isinstance(m, AssistantToolUseMsg):
                blocks: list[dict[str, Any]] = []
                if m.text:
                    blocks.append({"type": "text", "text": m.text})
                for tc in m.tool_calls:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.input,
                    })
                sdk_messages.append({"role": "assistant", "content": blocks})
            elif isinstance(m, ToolResultMsg):
                tr_block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": m.tool_use_id,
                    "content": m.content,
                }
                if m.is_error:
                    tr_block["is_error"] = True
                sdk_messages.append({"role": "user", "content": [tr_block]})
            else:
                raise LLMProviderError(f"unknown message variant: {type(m).__name__}")

        sdk_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

        system_block = [
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
                tools=sdk_tools,
            )
        except Exception as e:
            log.exception("anthropic tool-use call failed")
            raise LLMProviderError(f"anthropic {type(e).__name__}") from e

        text_preamble: str | None = None
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                if text_preamble is None:
                    text_preamble = getattr(block, "text", None)
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=getattr(block, "id"),
                        name=getattr(block, "name"),
                        input=dict(getattr(block, "input", {}) or {}),
                    )
                )

        stop_reason = getattr(resp, "stop_reason", None)
        if tool_calls:
            return ToolUseResult(
                kind="tool_use",
                text=text_preamble,
                tool_calls=tool_calls,
                stop_reason=stop_reason,
            )
        if text_preamble is None:
            raise LLMProviderError("anthropic response contained no text or tool_use block")
        return ToolUseResult(
            kind="text",
            text=text_preamble,
            tool_calls=[],
            stop_reason=stop_reason,
        )
