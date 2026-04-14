from project0.llm.tools import (
    AssistantToolUseMsg,
    ToolCall,
    ToolResultMsg,
    ToolSpec,
    ToolUseResult,
)


def test_tool_spec_is_hashable_frozen_dataclass():
    spec = ToolSpec(
        name="calendar_list_events",
        description="List events",
        input_schema={"type": "object", "properties": {}},
    )
    assert spec.name == "calendar_list_events"


def test_tool_call_fields():
    call = ToolCall(id="toolu_1", name="foo", input={"x": 1})
    assert call.id == "toolu_1"
    assert call.input == {"x": 1}


def test_tool_use_result_text_variant():
    r = ToolUseResult(kind="text", text="done", tool_calls=[], stop_reason="end_turn")
    assert r.kind == "text"
    assert r.text == "done"
    assert r.tool_calls == []


def test_tool_use_result_tool_use_variant():
    r = ToolUseResult(
        kind="tool_use",
        text=None,
        tool_calls=[ToolCall(id="toolu_1", name="foo", input={})],
        stop_reason="tool_use",
    )
    assert r.kind == "tool_use"
    assert len(r.tool_calls) == 1


def test_assistant_tool_use_msg_and_tool_result_msg():
    assistant = AssistantToolUseMsg(
        tool_calls=[ToolCall(id="a", name="foo", input={})],
        text="thinking...",
    )
    result = ToolResultMsg(tool_use_id="a", content="42", is_error=False)
    assert assistant.text == "thinking..."
    assert result.is_error is False
