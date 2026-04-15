import pytest

from project0.llm.provider import FakeProvider, LLMProviderError, Msg
from project0.llm.tools import (
    AssistantToolUseMsg,
    ToolCall,
    ToolResultMsg,
    ToolSpec,
    ToolUseResult,
)

_TOOLS = [
    ToolSpec(name="foo", description="foo", input_schema={"type": "object"}),
]


@pytest.mark.asyncio
async def test_fake_provider_replays_tool_use_script():
    fake = FakeProvider(
        tool_responses=[
            ToolUseResult(
                kind="tool_use",
                text=None,
                tool_calls=[ToolCall(id="toolu_1", name="foo", input={"x": 1})],
                stop_reason="tool_use",
            ),
            ToolUseResult(
                kind="text", text="all done", tool_calls=[], stop_reason="end_turn"
            ),
        ]
    )

    first = await fake.complete_with_tools(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        tools=_TOOLS,
        agent="manager", purpose="tool_loop", envelope_id=None,
    )
    assert first.kind == "tool_use"
    assert first.tool_calls[0].id == "toolu_1"

    second = await fake.complete_with_tools(
        system="sys",
        messages=[
            Msg(role="user", content="hi"),
            AssistantToolUseMsg(
                tool_calls=[ToolCall(id="toolu_1", name="foo", input={"x": 1})]
            ),
            ToolResultMsg(tool_use_id="toolu_1", content="42"),
        ],
        tools=_TOOLS,
        agent="manager", purpose="tool_loop", envelope_id=None,
    )
    assert second.kind == "text"
    assert second.text == "all done"

    assert len(fake.tool_calls_log) == 2


@pytest.mark.asyncio
async def test_fake_provider_exhausted_tool_script_raises():
    fake = FakeProvider(tool_responses=[])
    with pytest.raises(LLMProviderError, match="tool_responses"):
        await fake.complete_with_tools(
            system="sys",
            messages=[Msg(role="user", content="hi")],
            tools=_TOOLS,
            agent="manager", purpose="tool_loop", envelope_id=None,
        )
