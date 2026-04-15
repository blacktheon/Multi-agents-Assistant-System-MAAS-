from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from project0.llm.provider import AnthropicProvider, LLMProviderError, Msg
from project0.llm.tools import (
    AssistantToolUseMsg,
    ToolCall,
    ToolResultMsg,
    ToolSpec,
)
from project0.store import LLMUsageStore, Store


def _usage_store() -> LLMUsageStore:
    s = Store(":memory:")
    s.init_schema()
    return LLMUsageStore(s.conn)


def _fake_response_tool_use():
    # Mimics Anthropic SDK response object shape enough for the translator.
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="text", text="let me check"),
            SimpleNamespace(
                type="tool_use",
                id="toolu_1",
                name="calendar_list_events",
                input={"time_min": "2026-04-14T00:00:00Z"},
            ),
        ],
    )


def _fake_response_text():
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="all good")],
    )


@pytest.mark.asyncio
async def test_anthropic_translates_messages_and_tools():
    provider = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6", usage_store=_usage_store())
    mock_create = AsyncMock(return_value=_fake_response_tool_use())
    provider._client.messages.create = mock_create  # type: ignore[method-assign]

    tools = [
        ToolSpec(
            name="calendar_list_events",
            description="List events",
            input_schema={"type": "object", "properties": {}},
        )
    ]
    messages = [
        Msg(role="user", content="check my day"),
        AssistantToolUseMsg(
            tool_calls=[ToolCall(id="toolu_old", name="noop", input={})],
            text="checking...",
        ),
        ToolResultMsg(tool_use_id="toolu_old", content="ok"),
    ]
    result = await provider.complete_with_tools(
        system="你是经理",
        messages=messages,
        tools=tools,
        max_tokens=512,
        agent="manager", purpose="tool_loop", envelope_id=None,
    )

    assert result.kind == "tool_use"
    assert result.text == "let me check"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "calendar_list_events"
    assert result.tool_calls[0].id == "toolu_1"

    # Inspect the SDK payload we constructed.
    kwargs = mock_create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["max_tokens"] == 512
    assert kwargs["tools"][0]["name"] == "calendar_list_events"
    # System block uses prompt caching.
    assert kwargs["system"][0]["cache_control"]["type"] == "ephemeral"
    sdk_messages = kwargs["messages"]
    assert sdk_messages[0] == {"role": "user", "content": "check my day"}
    # Assistant turn with tool_use was translated to a list-of-blocks form.
    assert sdk_messages[1]["role"] == "assistant"
    blocks = sdk_messages[1]["content"]
    assert any(b.get("type") == "text" and b.get("text") == "checking..." for b in blocks)
    assert any(b.get("type") == "tool_use" and b.get("id") == "toolu_old" for b in blocks)
    # tool_result turn → user role with tool_result block.
    assert sdk_messages[2]["role"] == "user"
    tr = sdk_messages[2]["content"][0]
    assert tr["type"] == "tool_result"
    assert tr["tool_use_id"] == "toolu_old"
    assert tr["content"] == "ok"
    assert tr.get("is_error", False) is False


@pytest.mark.asyncio
async def test_anthropic_returns_text_variant_on_end_turn():
    provider = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6", usage_store=_usage_store())
    provider._client.messages.create = AsyncMock(return_value=_fake_response_text())  # type: ignore[method-assign]

    result = await provider.complete_with_tools(
        system="s", messages=[Msg(role="user", content="hi")], tools=[],
        agent="manager", purpose="tool_loop", envelope_id=None,
    )
    assert result.kind == "text"
    assert result.text == "all good"
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_anthropic_wraps_sdk_errors():
    provider = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6", usage_store=_usage_store())
    provider._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("boom")
    )
    with pytest.raises(LLMProviderError):
        await provider.complete_with_tools(
            system="s", messages=[Msg(role="user", content="hi")], tools=[],
            agent="manager", purpose="tool_loop", envelope_id=None,
        )
