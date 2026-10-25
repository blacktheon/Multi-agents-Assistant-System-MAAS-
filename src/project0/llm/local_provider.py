"""Local LLM provider: talks to an OpenAI-compatible HTTP endpoint (Ollama).

Structurally parallel to AnthropicProvider — same Protocol, same usage
recording — but no prompt caching and no extended thinking. Tool use is
implemented at the prompt level with Qwen3.5's native text protocol
(see project0.llm.qwen_tools), because Ollama 0.17.x cannot render or
parse tools for the custom-imported GGUF. Design rationale:
docs/superpowers/specs/2026-04-20-secretary-local-llm-design.md and
docs/superpowers/specs/2026-10-24-all-agents-local-llm-design.md.
"""

from __future__ import annotations

import asyncio
import logging
from typing import cast

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from project0.llm.provider import LLMProviderError, Msg, SystemBlocks
from project0.llm.qwen_tools import (
    parse_tool_calls,
    render_assistant_turn,
    render_tool_results,
    render_tools_system,
)
from project0.llm.tools import AssistantToolUseMsg, ToolResultMsg, ToolSpec, ToolUseResult
from project0.store import LLMUsageStore

log = logging.getLogger(__name__)


class LocalProviderError(LLMProviderError):
    """Base class for LocalProvider failures.

    Inherits from LLMProviderError so the agents' existing provider-error
    catch paths handle local-provider failures the same as Anthropic ones —
    graceful silent drop, daemon keeps running."""


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


def _chat_msg(role: str, content: str) -> ChatCompletionMessageParam:
    return cast(ChatCompletionMessageParam, {"role": role, "content": content})


class LocalProvider:
    """OpenAI-compatible HTTP provider. Prompt-level tool use; no caching."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        usage_store: LLMUsageStore,
        request_timeout_seconds: float = 180.0,
        retry_sleep_seconds: float = 2.0,
        thinking_allowance_tokens: int = 0,
    ) -> None:
        if not api_key:
            raise ValueError("LocalProvider requires a non-empty api_key (any string)")
        self._model = model
        self._usage_store = usage_store
        self._retry_sleep = retry_sleep_seconds
        # When the served model thinks before replying, Ollama counts those
        # tokens against max_tokens; this is the reasoning headroom added to
        # each caller's visible-reply budget (0 for a non-thinking model).
        self._thinking_allowance = thinking_allowance_tokens
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=httpx.Timeout(request_timeout_seconds),
            max_retries=0,
        )

    async def _create(
        self, payload_messages: list[ChatCompletionMessageParam], max_tokens: int
    ) -> ChatCompletion:
        """One chat-completions request with the provider's error mapping:
        timeouts/connection errors and non-retryable HTTP errors raise
        immediately; a 5xx is retried once after a short sleep."""
        last_err: Exception | None = None
        for attempt in (1, 2):
            try:
                return await self._client.chat.completions.create(
                    model=self._model,
                    messages=payload_messages,
                    max_tokens=max_tokens + self._thinking_allowance,
                    stream=False,
                )
            except APITimeoutError as e:
                log.error("local llm timeout: %s", e)
                raise LocalProviderUnavailableError("timeout") from e
            except APIConnectionError as e:
                log.error("local llm connection error: %s", e)
                raise LocalProviderUnavailableError("connection") from e
            except APIStatusError as e:
                status = getattr(e, "status_code", None)
                body_str = str(getattr(e, "body", "") or "")
                if status == 400 and (
                    "context length" in body_str.lower() or "context_length" in body_str.lower()
                ):
                    raise LocalProviderContextError(body_str) from e
                if status and 500 <= status < 600 and attempt == 1:
                    log.warning(
                        "local llm 5xx (attempt 1), retrying in %.1fs: %s",
                        self._retry_sleep, body_str,
                    )
                    last_err = e
                    await asyncio.sleep(self._retry_sleep)
                    continue
                log.error("local llm HTTP error status=%s: %s", status, body_str)
                raise LocalProviderUnavailableError(f"http {status}") from e
        raise LocalProviderUnavailableError("persistent 5xx") from last_err

    def _record_usage(
        self,
        resp: ChatCompletion,
        *,
        agent: str,
        purpose: str,
        envelope_id: int | None,
    ) -> None:
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

    @staticmethod
    def _content_of(resp: ChatCompletion) -> tuple[str, str | None, str | None]:
        """Return (content, reasoning, finish_reason). Ollama delivers the
        model's thinking in a separate `reasoning` field; should a closing
        think tag ever leak into content, the answer is what follows it."""
        if not resp.choices:
            return "", None, None
        choice = resp.choices[0]
        content = choice.message.content or ""
        if "</think>" in content:
            content = content.split("</think>", 1)[1].lstrip("\n")
        reasoning = getattr(choice.message, "reasoning", None)
        if reasoning is None and choice.message.model_extra:
            reasoning = choice.message.model_extra.get("reasoning")
        return content, (reasoning or None), choice.finish_reason

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
        payload_messages = [_chat_msg("system", _flatten_system(system))]
        payload_messages.extend(_chat_msg(m.role, m.content) for m in messages)

        resp = await self._create(payload_messages, max_tokens)
        self._record_usage(resp, agent=agent, purpose=purpose, envelope_id=envelope_id)
        content, reasoning, finish_reason = self._content_of(resp)
        if not content.strip() and reasoning and finish_reason == "length":
            log.warning(
                "local llm: thinking exhausted the token budget (agent=%s purpose=%s)",
                agent, purpose,
            )
        return content

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
        payload_messages = [
            _chat_msg("system", render_tools_system(tools, _flatten_system(system)))
        ]
        # Consecutive tool results collapse into one user turn, as the model's
        # own chat template does.
        pending_results: list[ToolResultMsg] = []

        def flush_results() -> None:
            if pending_results:
                payload_messages.append(_chat_msg("user", render_tool_results(pending_results)))
                pending_results.clear()

        for m in messages:
            if isinstance(m, ToolResultMsg):
                pending_results.append(m)
                continue
            flush_results()
            if isinstance(m, AssistantToolUseMsg):
                payload_messages.append(
                    _chat_msg("assistant", render_assistant_turn(m.text, m.tool_calls))
                )
            else:
                payload_messages.append(_chat_msg(m.role, m.content))
        flush_results()

        resp = await self._create(payload_messages, max_tokens)
        self._record_usage(resp, agent=agent, purpose=purpose, envelope_id=envelope_id)
        content, reasoning, finish_reason = self._content_of(resp)

        text, calls = parse_tool_calls(content, tools)
        if calls:
            return ToolUseResult(
                kind="tool_use", text=text, tool_calls=calls, stop_reason=finish_reason
            )
        # Mirror AnthropicProvider's contract: a reply that is neither usable
        # text nor a complete tool call is a provider error (the tool loop
        # turns it into errored=True and the agent drops the turn) — never a
        # text reply that would leak raw protocol markup or an empty message.
        if "<tool_call>" in content:
            raise LocalProviderError(
                f"malformed or truncated tool call in reply (finish_reason={finish_reason})"
            )
        if not content.strip():
            if reasoning and finish_reason == "length":
                raise LocalProviderError(
                    "thinking exhausted the token budget before any reply "
                    f"(allowance={self._thinking_allowance}); raise LOCAL_LLM_THINKING_BUDGET"
                )
            raise LocalProviderError(f"empty reply (finish_reason={finish_reason})")
        return ToolUseResult(kind="text", text=content, tool_calls=[], stop_reason=finish_reason)
