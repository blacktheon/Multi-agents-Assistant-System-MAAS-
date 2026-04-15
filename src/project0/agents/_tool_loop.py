"""Shared agentic tool-use loop used by Manager (6c) and Intelligence (6d).

Extracted from ``Manager._agentic_loop`` in 6d so both agents can drive the
same loop mechanics without duplication. The loop is stateless across calls
— all per-turn state lives in a local ``TurnState`` so concurrent turns
cannot cross-contaminate.

Semantics:
  - iterate up to ``max_iterations`` times
  - each iteration calls ``llm.complete_with_tools`` with the running
    messages list
  - on ``kind='text'``: return ``LoopResult(final_text=..., errored=False)``
  - on ``kind='tool_use'``: append an ``AssistantToolUseMsg``, run
    ``dispatch_tool`` for every tool_call, append a ``ToolResultMsg`` for
    each, continue
  - ``LLMProviderError`` from ``complete_with_tools`` is caught and surfaced
    as ``LoopResult(final_text=None, errored=True)``; the caller decides
    whether to drop the turn
  - exceeding ``max_iterations`` raises ``LLMProviderError``

Callers are responsible for finalizing the ``LoopResult`` into an
``AgentResult`` — Manager applies its pulse-silent and delegation-wins
rules; Intelligence just maps ``final_text`` straight to ``reply_text``.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from project0.llm.provider import LLMProvider, LLMProviderError, Msg
from project0.llm.tools import (
    AssistantToolUseMsg,
    ToolCall,
    ToolResultMsg,
    ToolSpec,
)

log = logging.getLogger(__name__)


@dataclass
class TurnState:
    """Mutable state for one agentic loop invocation. Lives in the local
    scope of ``run_agentic_loop`` so concurrent turns never share state."""
    delegation_target: str | None = None
    delegation_handoff: str | None = None
    delegation_payload: dict[str, Any] | None = None


@dataclass
class LoopResult:
    """Raw output of ``run_agentic_loop``. Callers finalize it into
    ``AgentResult`` using agent-specific rules (pulse suppression,
    delegation-wins, etc)."""
    final_text: str | None
    turn_state: TurnState
    errored: bool = False


DispatchTool = Callable[[ToolCall, TurnState], Awaitable[tuple[str, bool]]]


async def run_agentic_loop(
    *,
    llm: LLMProvider,
    system: str,
    initial_user_text: str,
    tools: list[ToolSpec],
    dispatch_tool: DispatchTool,
    max_iterations: int,
    max_tokens: int,
    agent: str,
    purpose: str,
    envelope_id: int | None = None,
) -> LoopResult:
    turn_state = TurnState()
    messages: list = [Msg(role="user", content=initial_user_text)]

    for _iter in range(max_iterations):
        try:
            result = await llm.complete_with_tools(
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                agent=agent,
                purpose=purpose,
                envelope_id=envelope_id,
            )
        except LLMProviderError as e:
            log.warning("tool loop LLM call failed: %s", e)
            return LoopResult(final_text=None, turn_state=turn_state, errored=True)

        if result.kind == "text":
            return LoopResult(
                final_text=result.text or "",
                turn_state=turn_state,
                errored=False,
            )

        # tool_use branch
        messages.append(
            AssistantToolUseMsg(
                tool_calls=list(result.tool_calls),
                text=result.text,
            )
        )
        for call in result.tool_calls:
            content_str, is_err = await dispatch_tool(call, turn_state)
            messages.append(
                ToolResultMsg(
                    tool_use_id=call.id,
                    content=content_str,
                    is_error=is_err,
                )
            )

    raise LLMProviderError(f"tool loop exceeded max_iterations={max_iterations}")
