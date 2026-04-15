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
from typing import Any, Literal, Protocol, cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam

from project0.llm.tools import AssistantToolUseMsg, ToolCall, ToolResultMsg, ToolSpec, ToolUseResult
from project0.store import LLMUsageStore

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


@dataclass(frozen=True)
class SystemBlocks:
    """Structured system prompt with optional two-breakpoint layout.

    stable:  Segment 1. Large, cached, rarely busts (persona + mode + profile).
    facts:   Segment 2, optional. Small, busts on conversational cadence
             (user_facts block). None or empty string → no second breakpoint.
    """

    stable: str
    facts: str | None = None


def _render_system_param(
    system: str | SystemBlocks,
    *,
    cache_ttl: str = "ephemeral",
) -> list[dict[str, Any]]:
    """Turn the caller's system input into the SDK's `system` parameter shape.

    Plain string → one cached text block. SystemBlocks with only `stable` →
    one cached block. SystemBlocks with both → two blocks, each with its own
    cache_control marker. Empty `facts` is treated as None.
    """
    cache_marker: dict[str, Any] = {"type": "ephemeral"}
    if cache_ttl == "1h":
        cache_marker = {"type": "ephemeral", "ttl": "1h"}

    if isinstance(system, str):
        return [{"type": "text", "text": system, "cache_control": cache_marker}]

    out: list[dict[str, Any]] = [
        {"type": "text", "text": system.stable, "cache_control": cache_marker}
    ]
    if system.facts:
        out.append(
            {"type": "text", "text": system.facts, "cache_control": cache_marker}
        )
    return out


class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        system: str | SystemBlocks,
        messages: list[Msg],
        max_tokens: int = 800,
        thinking_budget_tokens: int | None = None,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
    ) -> str:
        ...

    async def complete_with_tools(
        self,
        *,
        system: str | SystemBlocks,
        messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
    ) -> ToolUseResult:
        ...


@dataclass
class FakeProvider:
    """Test-only provider. Either pre-loaded with canned responses or driven
    by a callable. Records every call. Optionally writes usage rows to a
    real LLMUsageStore when one is supplied — this lets tests exercise the
    recording path without mocking AnthropicProvider."""

    responses: list[str] | None = None
    callable_: Callable[[str, list[Msg]], str] | None = None
    calls: list[ProviderCall] = field(default_factory=list)
    tool_responses: list[ToolUseResult] | None = None
    tool_calls_log: list[dict[str, Any]] = field(default_factory=list)
    usage_store: LLMUsageStore | None = None
    fake_usage: tuple[int, int, int, int] = (100, 0, 80, 20)
    _idx: int = 0
    _tool_idx: int = 0

    async def complete(
        self,
        *,
        system: str | SystemBlocks,
        messages: list[Msg],
        max_tokens: int = 800,
        thinking_budget_tokens: int | None = None,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
    ) -> str:
        # Normalize system to a string for the call-log for test inspection.
        sys_str = system if isinstance(system, str) else system.stable + (
            "\n" + system.facts if system.facts else ""
        )
        self.calls.append(
            ProviderCall(
                system=sys_str, messages=list(messages),
                max_tokens=max_tokens,
                thinking_budget_tokens=thinking_budget_tokens,
            )
        )
        if self.usage_store is not None:
            in_tok, cc, cr, out_tok = self.fake_usage
            self.usage_store.record(
                agent=agent, model="fake",
                input_tokens=in_tok,
                cache_creation_input_tokens=cc,
                cache_read_input_tokens=cr,
                output_tokens=out_tok,
                envelope_id=envelope_id, purpose=purpose,
            )
        if self.callable_ is not None:
            return self.callable_(sys_str, messages)
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
        system: str | SystemBlocks,
        messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
    ) -> ToolUseResult:
        sys_str = system if isinstance(system, str) else system.stable + (
            "\n" + system.facts if system.facts else ""
        )
        self.tool_calls_log.append({
            "system": sys_str, "messages": list(messages),
            "tools": list(tools), "max_tokens": max_tokens,
            "agent": agent, "purpose": purpose,
            "envelope_id": envelope_id,
        })
        if self.usage_store is not None:
            in_tok, cc, cr, out_tok = self.fake_usage
            self.usage_store.record(
                agent=agent, model="fake",
                input_tokens=in_tok,
                cache_creation_input_tokens=cc,
                cache_read_input_tokens=cr,
                output_tokens=out_tok,
                envelope_id=envelope_id, purpose=purpose,
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

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        usage_store: LLMUsageStore,
        cache_ttl: str = "ephemeral",
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._usage_store = usage_store
        self._cache_ttl = cache_ttl

    async def complete(
        self,
        *,
        system: str | SystemBlocks,
        messages: list[Msg],
        max_tokens: int = 800,
        thinking_budget_tokens: int | None = None,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
    ) -> str:
        sdk_messages: list[MessageParam] = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        system_block = _render_system_param(system, cache_ttl=self._cache_ttl)
        extra: dict[str, Any] = {}
        if thinking_budget_tokens is not None:
            # `adaptive` lets the model decide how much of the budget to
            # actually use per-turn, which Anthropic reports as higher
            # quality than the fixed `enabled` shape (deprecated as of
            # late 2025). `budget_tokens` still caps the upper bound.
            extra["thinking"] = {
                "type": "adaptive",
                "budget_tokens": thinking_budget_tokens,
            }
        # Always stream: the Anthropic SDK refuses non-streaming calls whose
        # max_tokens + thinking_budget could exceed the 10-minute request
        # limit (e.g. Opus + 32k tokens + 16k thinking). Streaming sidesteps
        # that check and works identically for small requests too — we just
        # accumulate the final message and return its text. Callers see the
        # same `str` return as before.
        try:
            async with self._client.messages.stream(
                model=self._model,
                max_tokens=max_tokens,
                system=cast(Any, system_block),
                messages=cast(Any, sdk_messages),
                **extra,
            ) as stream:
                final_message = await stream.get_final_message()
        except Exception as e:
            log.exception("anthropic call failed")
            raise LLMProviderError(f"anthropic {type(e).__name__}") from e

        usage = getattr(final_message, "usage", None)
        if usage is not None:
            in_tok = int(getattr(usage, "input_tokens", 0) or 0)
            cc_tok = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            cr_tok = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            out_tok = int(getattr(usage, "output_tokens", 0) or 0)
            self._usage_store.record(
                agent=agent,
                model=self._model,
                input_tokens=in_tok,
                cache_creation_input_tokens=cc_tok,
                cache_read_input_tokens=cr_tok,
                output_tokens=out_tok,
                envelope_id=envelope_id,
                purpose=purpose,
            )
            log.info(
                "llm call agent=%s model=%s in=%d cc=%d cr=%d out=%d env=%s purpose=%s",
                agent, self._model, in_tok, cc_tok, cr_tok, out_tok,
                envelope_id if envelope_id is not None else "-",
                purpose,
            )

        for block in final_message.content:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", None)
                if text:
                    return str(text)
        raise LLMProviderError("anthropic response contained no text block")

    async def complete_with_tools(
        self,
        *,
        system: str | SystemBlocks,
        messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
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

        system_block = _render_system_param(system, cache_ttl=self._cache_ttl)

        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=cast(Any, system_block),
                messages=cast(Any, sdk_messages),
                tools=cast(Any, sdk_tools),
            )
        except Exception as e:
            log.exception("anthropic tool-use call failed")
            raise LLMProviderError(f"anthropic {type(e).__name__}") from e

        usage = getattr(resp, "usage", None)
        if usage is not None:
            in_tok = int(getattr(usage, "input_tokens", 0) or 0)
            cc_tok = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            cr_tok = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            out_tok = int(getattr(usage, "output_tokens", 0) or 0)
            self._usage_store.record(
                agent=agent,
                model=self._model,
                input_tokens=in_tok,
                cache_creation_input_tokens=cc_tok,
                cache_read_input_tokens=cr_tok,
                output_tokens=out_tok,
                envelope_id=envelope_id,
                purpose=purpose,
            )
            log.info(
                "llm tool-call agent=%s model=%s in=%d cc=%d cr=%d out=%d env=%s purpose=%s",
                agent, self._model, in_tok, cc_tok, cr_tok, out_tok,
                envelope_id if envelope_id is not None else "-",
                purpose,
            )

        text_preamble: str | None = None
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                if text_preamble is None:
                    text_preamble = getattr(block, "text", None)
            elif btype == "tool_use":
                tu = cast(Any, block)
                tool_calls.append(
                    ToolCall(
                        id=tu.id,
                        name=tu.name,
                        input=dict(getattr(tu, "input", {}) or {}),
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
