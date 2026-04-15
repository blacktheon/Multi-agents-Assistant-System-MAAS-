"""Shared agentic loop helper tests. The helper is the extraction of
Manager._agentic_loop into a reusable module. Intelligence will also use it.
These tests verify the loop mechanics in isolation from any agent class."""
from __future__ import annotations

import pytest

from project0.agents._tool_loop import LoopResult, TurnState, run_agentic_loop
from project0.llm.provider import FakeProvider, LLMProviderError, Msg
from project0.llm.tools import ToolCall, ToolSpec, ToolUseResult

TOOL_ECHO = ToolSpec(
    name="echo",
    description="Echo the input back.",
    input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
)


async def _dispatch_echo(call: ToolCall, turn_state: TurnState) -> tuple[str, bool]:
    if call.name == "echo":
        return call.input.get("text", ""), False
    return f"unknown tool: {call.name}", True


@pytest.mark.asyncio
async def test_plain_text_response_returns_loop_result():
    llm = FakeProvider(tool_responses=[
        ToolUseResult(kind="text", text="hello world", tool_calls=[], stop_reason="end_turn"),
    ])
    result = await run_agentic_loop(
        llm=llm,
        system="sys",
        initial_user_text="hi",
        tools=[TOOL_ECHO],
        dispatch_tool=_dispatch_echo,
        max_iterations=4,
        max_tokens=256,
        agent="manager",
        purpose="tool_loop",
    )
    assert isinstance(result, LoopResult)
    assert result.final_text == "hello world"
    assert result.errored is False
    assert result.turn_state.delegation_target is None


@pytest.mark.asyncio
async def test_tool_use_then_text_runs_dispatch_then_returns_text():
    llm = FakeProvider(tool_responses=[
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(id="t1", name="echo", input={"text": "ping"})],
            stop_reason="tool_use",
        ),
        ToolUseResult(kind="text", text="ping done", tool_calls=[], stop_reason="end_turn"),
    ])
    result = await run_agentic_loop(
        llm=llm,
        system="sys",
        initial_user_text="hi",
        tools=[TOOL_ECHO],
        dispatch_tool=_dispatch_echo,
        max_iterations=4,
        max_tokens=256,
        agent="manager",
        purpose="tool_loop",
    )
    assert result.final_text == "ping done"
    assert result.errored is False


@pytest.mark.asyncio
async def test_llm_error_is_caught_and_flagged():
    # FakeProvider exhaustion raises LLMProviderError on the first call.
    llm = FakeProvider(tool_responses=[])
    result = await run_agentic_loop(
        llm=llm,
        system="sys",
        initial_user_text="hi",
        tools=[TOOL_ECHO],
        dispatch_tool=_dispatch_echo,
        max_iterations=4,
        max_tokens=256,
        agent="manager",
        purpose="tool_loop",
    )
    assert result.errored is True
    assert result.final_text is None


@pytest.mark.asyncio
async def test_iteration_overflow_raises():
    # Keep returning tool_use forever → loop exceeds max_iterations.
    infinite = [
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(id=f"t{i}", name="echo", input={"text": "x"})],
            stop_reason="tool_use",
        )
        for i in range(10)
    ]
    llm = FakeProvider(tool_responses=infinite)
    with pytest.raises(LLMProviderError, match="max_iterations"):
        await run_agentic_loop(
            llm=llm,
            system="sys",
            initial_user_text="hi",
            tools=[TOOL_ECHO],
            dispatch_tool=_dispatch_echo,
            max_iterations=3,
            max_tokens=256,
            agent="manager",
            purpose="tool_loop",
        )


@pytest.mark.asyncio
async def test_dispatch_tool_sees_turn_state_and_mutation_persists():
    async def dispatch_set_delegation(call: ToolCall, turn_state: TurnState) -> tuple[str, bool]:
        turn_state.delegation_target = "secretary"
        turn_state.delegation_handoff = "please remind X"
        turn_state.delegation_payload = {"kind": "reminder_request"}
        return "delegated", False

    llm = FakeProvider(tool_responses=[
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(id="t1", name="echo", input={"text": "x"})],
            stop_reason="tool_use",
        ),
        ToolUseResult(kind="text", text="ok", tool_calls=[], stop_reason="end_turn"),
    ])
    result = await run_agentic_loop(
        llm=llm,
        system="sys",
        initial_user_text="hi",
        tools=[TOOL_ECHO],
        dispatch_tool=dispatch_set_delegation,
        max_iterations=4,
        max_tokens=256,
        agent="manager",
        purpose="tool_loop",
    )
    assert result.turn_state.delegation_target == "secretary"
    assert result.turn_state.delegation_handoff == "please remind X"
    assert result.turn_state.delegation_payload == {"kind": "reminder_request"}
    assert result.final_text == "ok"
