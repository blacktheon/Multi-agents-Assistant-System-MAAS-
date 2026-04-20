"""Local LLM provider: talks to an OpenAI-compatible HTTP endpoint.

Used only by Secretary in `SECRETARY_MODE=free`. Structurally parallel to
AnthropicProvider — same Protocol, same usage recording, but no prompt
caching and no tool-use. See docs/superpowers/specs/2026-04-20-secretary-
local-llm-design.md for the design rationale.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion
from openai.types.chat import ChatCompletionMessageParam

from project0.llm.provider import Msg, SystemBlocks
from project0.llm.tools import AssistantToolUseMsg, ToolResultMsg, ToolSpec, ToolUseResult
from project0.store import LLMUsageStore

log = logging.getLogger(__name__)


class LocalProviderError(Exception):
    """Base class for LocalProvider failures."""


class LocalProviderUnavailableError(LocalProviderError):
    """Server unreachable, timeout, or persistent 5xx."""


class LocalProviderContextError(LocalProviderError):
    """Request exceeded the server's context length (HTTP 400)."""


def _flatten_system(system: str | SystemBlocks) -> str:
    if isinstance(system, str):
        return system
    if system.facts:
        return f"{system.stable}\n\n{system.facts}"
    return system.stable


class LocalProvider:
    """OpenAI-compatible HTTP provider. No tool-use; no prompt caching."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        usage_store: LLMUsageStore,
        request_timeout_seconds: float = 180.0,
    ) -> None:
        if not api_key:
            raise ValueError("LocalProvider requires a non-empty api_key (any string)")
        self._model = model
        self._usage_store = usage_store
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=httpx.Timeout(request_timeout_seconds),
            max_retries=0,
        )

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
        # thinking_budget_tokens is Anthropic-specific; ignored here.
        system_text = _flatten_system(system)
        payload_messages: list[ChatCompletionMessageParam] = [
            cast(ChatCompletionMessageParam, {"role": "system", "content": system_text})
        ]
        for m in messages:
            payload_messages.append(
                cast(ChatCompletionMessageParam, {"role": m.role, "content": m.content})
            )

        resp: ChatCompletion = await self._client.chat.completions.create(
            model=self._model,
            messages=payload_messages,
            max_tokens=max_tokens,
            stream=False,
        )

        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        out_tok = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        self._usage_store.record(
            agent=agent,
            model=self._model,
            input_tokens=in_tok,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            output_tokens=out_tok,
            envelope_id=envelope_id,
            purpose=purpose,
        )
        log.info(
            "local llm call agent=%s model=%s in=%d out=%d env=%s purpose=%s",
            agent, self._model, in_tok, out_tok,
            envelope_id if envelope_id is not None else "-",
            purpose,
        )

        if not resp.choices:
            return ""
        content = resp.choices[0].message.content
        return content if content else ""

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
        raise NotImplementedError(
            "LocalProvider does not support tool-use; free mode must run "
            "without user_facts_writer. See 2026-04-20-secretary-local-llm-design.md §6."
        )
