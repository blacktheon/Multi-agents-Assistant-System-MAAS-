"""Pure render/parse helpers for Qwen3.5's native text tool protocol.

The expected strings mirror the GGUF's embedded Jinja chat_template, so a
mismatch here means the model would see a prompt it was not trained on.
"""

from __future__ import annotations

from project0.llm.qwen_tools import (
    parse_tool_calls,
    render_assistant_turn,
    render_tool_results,
    render_tools_system,
)
from project0.llm.tools import ToolCall, ToolResultMsg, ToolSpec

WEATHER = ToolSpec(
    name="get_weather",
    description="Forecast.",
    input_schema={
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "days": {"type": "integer"},
            "opts": {"type": "object"},
            "flag": {"type": ["boolean", "null"]},
        },
        "required": ["city"],
    },
)


def test_render_tools_system_layout() -> None:
    out = render_tools_system([WEATHER], "You are 林夕.")
    assert out.startswith("# Tools\n\nYou have access to the following functions:\n\n<tools>\n")
    assert '"name": "get_weather"' in out
    assert "</tools>\n\nIf you choose to call a function ONLY reply" in out
    assert out.endswith("</IMPORTANT>\n\nYou are 林夕.")


def test_render_tools_system_without_system_text_ends_at_instructions() -> None:
    out = render_tools_system([WEATHER], "   ")
    assert out.endswith("</IMPORTANT>")


def test_render_assistant_turn_with_and_without_text() -> None:
    call = ToolCall(
        id="call_1", name="get_weather", input={"city": "上海", "days": 3, "opts": {"a": 1}}
    )
    assert render_assistant_turn(None, [call]) == (
        "<tool_call>\n<function=get_weather>\n<parameter=city>\n上海\n</parameter>\n"
        '<parameter=days>\n3\n</parameter>\n<parameter=opts>\n{"a": 1}\n</parameter>\n'
        "</function>\n</tool_call>"
    )
    assert render_assistant_turn("查一下", [call]).startswith("查一下\n\n<tool_call>")


def test_render_assistant_turn_multiple_calls_joined_by_newline() -> None:
    a = ToolCall(id="call_1", name="get_weather", input={"city": "A"})
    b = ToolCall(id="call_2", name="get_weather", input={"city": "B"})
    out = render_assistant_turn(None, [a, b])
    assert out.count("<tool_call>") == 2
    assert "</tool_call>\n<tool_call>" in out


def test_render_tool_results_merges_and_marks_errors() -> None:
    out = render_tool_results([
        ToolResultMsg(tool_use_id="call_1", content="ok", is_error=False),
        ToolResultMsg(tool_use_id="call_2", content="boom", is_error=True),
    ])
    assert out == (
        "<tool_response>\nok\n</tool_response>\n"
        "<tool_response>\n[tool error] boom\n</tool_response>"
    )


def test_parse_single_call_with_ids() -> None:
    reply = (
        "<tool_call>\n<function=get_weather>\n<parameter=city>\n上海\n</parameter>\n"
        "</function>\n</tool_call>"
    )
    text, calls = parse_tool_calls(reply, [WEATHER])
    assert text is None
    assert calls == [ToolCall(id="call_1", name="get_weather", input={"city": "上海"})]


def test_parse_coerces_by_schema() -> None:
    reply = (
        "<tool_call>\n<function=get_weather>\n<parameter=city>\n上海\n</parameter>\n"
        "<parameter=days>\n3\n</parameter>\n"
        '<parameter=opts>\n{"a": 1}\n</parameter>\n'
        "<parameter=flag>\ntrue\n</parameter>\n</function>\n</tool_call>"
    )
    _, calls = parse_tool_calls(reply, [WEATHER])
    assert calls[0].input == {"city": "上海", "days": 3, "opts": {"a": 1}, "flag": True}


def test_parse_uncoercible_stays_string() -> None:
    reply = (
        "<tool_call>\n<function=get_weather>\n<parameter=days>\nthree\n</parameter>\n"
        "</function>\n</tool_call>"
    )
    _, calls = parse_tool_calls(reply, [WEATHER])
    assert calls[0].input == {"days": "three"}


def test_parse_text_before_call() -> None:
    reply = (
        "好的，我来查一下。\n\n<tool_call>\n<function=get_weather>\n"
        "<parameter=city>\n上海\n</parameter>\n</function>\n</tool_call>"
    )
    text, calls = parse_tool_calls(reply, [WEATHER])
    assert text == "好的，我来查一下。"
    assert len(calls) == 1


def test_parse_multiple_calls_numbered() -> None:
    reply = (
        "<tool_call>\n<function=get_weather>\n<parameter=city>\nA\n</parameter>\n"
        "</function>\n</tool_call>\n"
        "<tool_call>\n<function=get_weather>\n<parameter=city>\nB\n</parameter>\n"
        "</function>\n</tool_call>"
    )
    _, calls = parse_tool_calls(reply, [WEATHER])
    assert [c.id for c in calls] == ["call_1", "call_2"]
    assert calls[1].input == {"city": "B"}


def test_parse_plain_text_has_no_calls() -> None:
    assert parse_tool_calls("今天天气不错。", [WEATHER]) == ("今天天气不错。", [])


def test_parse_malformed_falls_back_to_text() -> None:
    broken = "<tool_call>\n<function=get_weather>\n<parameter=city>\n上海\n</parameter>\n"
    assert parse_tool_calls(broken, [WEATHER]) == (broken.strip(), [])
