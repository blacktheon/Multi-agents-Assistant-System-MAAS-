# tests/agents/test_manager_tool_loop.py
from unittest.mock import AsyncMock

import pytest

from project0.agents.manager import (
    Manager,
    ManagerConfig,
    ManagerPersona,
)
from project0.envelope import Envelope
from project0.llm.provider import FakeProvider, LLMProviderError
from project0.llm.tools import ToolCall, ToolUseResult


def _persona():
    return ManagerPersona(
        core="c", dm_mode="d", group_addressed_mode="g",
        pulse_mode="p", tool_use_guide="t",
    )


def _config(max_iter=4):
    return ManagerConfig(
        model="m", max_tokens_reply=100,
        max_tool_iterations=max_iter, transcript_window=5,
    )


def _mgr(llm, calendar=None, messages_store=None):
    return Manager(
        llm=llm, calendar=calendar, memory=None,
        messages_store=messages_store, persona=_persona(), config=_config(),
    )


class _FakeMessagesStore:
    def recent_for_chat(self, *, chat_id, limit):
        return []


def _env_dm(body="hi"):
    return Envelope(
        id=1, ts="2026-04-14T00:00:00Z", parent_id=None,
        source="telegram_dm", telegram_chat_id=42, telegram_msg_id=1,
        received_by_bot="manager", from_kind="user", from_agent=None,
        to_agent="manager", body=body, mentions=[], routing_reason="direct_dm",
    )


def _env_pulse():
    return Envelope(
        id=1, ts="2026-04-14T00:00:00Z", parent_id=None,
        source="pulse", telegram_chat_id=None, telegram_msg_id=None,
        received_by_bot=None, from_kind="system", from_agent=None,
        to_agent="manager", body="check_calendar", mentions=[],
        routing_reason="pulse",
        payload={"pulse_name": "check_calendar", "window_minutes": 60},
    )


@pytest.mark.asyncio
async def test_plain_text_turn_returns_reply():
    fake = FakeProvider(tool_responses=[
        ToolUseResult(kind="text", text="hello back", tool_calls=[], stop_reason="end_turn"),
    ])
    mgr = _mgr(fake, messages_store=_FakeMessagesStore())

    result = await mgr.handle(_env_dm("hi"))
    assert result is not None
    assert result.reply_text == "hello back"
    assert result.delegate_to is None


@pytest.mark.asyncio
async def test_tool_then_text_flow():
    cal = AsyncMock()
    cal.list_events = AsyncMock(return_value=[])

    fake = FakeProvider(tool_responses=[
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(id="t1", name="calendar_list_events",
                                  input={"time_min": "2026-04-14T00:00:00+00:00",
                                         "time_max": "2026-04-15T00:00:00+00:00",
                                         "max_results": 10})],
            stop_reason="tool_use",
        ),
        ToolUseResult(kind="text", text="没有事", tool_calls=[], stop_reason="end_turn"),
    ])
    mgr = _mgr(fake, calendar=cal, messages_store=_FakeMessagesStore())

    result = await mgr.handle(_env_dm("我明天有什么事"))
    assert result is not None
    assert result.reply_text == "没有事"
    assert len(fake.tool_calls_log) == 2
    # Second call must include the tool_result turn.
    second_msgs = fake.tool_calls_log[1]["messages"]
    assert any(
        getattr(m, "tool_use_id", None) == "t1"
        for m in second_msgs
    )


@pytest.mark.asyncio
async def test_delegation_tool_returns_delegation_result():
    fake = FakeProvider(tool_responses=[
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(
                id="t1", name="delegate_to_secretary",
                input={
                    "reminder_text": "下周三牙医",
                    "appointment": "牙医",
                    "when": "下周三 10:00",
                    "note": "",
                },
            )],
            stop_reason="tool_use",
        ),
        ToolUseResult(
            kind="text", text="(epilogue that should be suppressed)",
            tool_calls=[], stop_reason="end_turn",
        ),
    ])
    mgr = _mgr(fake, messages_store=_FakeMessagesStore())

    result = await mgr.handle(_env_dm("下周三别让我忘了牙医"))
    assert result is not None
    assert result.delegate_to == "secretary"
    assert result.reply_text is None  # suppressed in favor of delegation
    assert "下周三牙医" in result.handoff_text
    assert result.delegation_payload is not None
    assert result.delegation_payload["appointment"] == "牙医"


@pytest.mark.asyncio
async def test_max_iterations_raises():
    # Every response is tool_use → loop never terminates → hits the cap.
    endless = [
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(id=f"t{i}", name="calendar_list_events",
                                  input={"time_min": "2026-04-14T00:00:00+00:00",
                                         "time_max": "2026-04-15T00:00:00+00:00"})],
            stop_reason="tool_use",
        )
        for i in range(10)
    ]
    fake = FakeProvider(tool_responses=endless)
    cal = AsyncMock()
    cal.list_events = AsyncMock(return_value=[])
    mgr = _mgr(fake, calendar=cal, messages_store=_FakeMessagesStore())

    with pytest.raises(LLMProviderError, match="max_tool_iterations"):
        await mgr.handle(_env_dm("loop forever"))


@pytest.mark.asyncio
async def test_pulse_path_returns_none_on_empty_text():
    fake = FakeProvider(tool_responses=[
        ToolUseResult(kind="text", text="", tool_calls=[], stop_reason="end_turn"),
    ])
    mgr = _mgr(fake, messages_store=_FakeMessagesStore())

    result = await mgr.handle(_env_pulse())
    assert result is None


@pytest.mark.asyncio
async def test_unknown_routing_reason_returns_none():
    fake = FakeProvider(tool_responses=[])
    mgr = _mgr(fake, messages_store=_FakeMessagesStore())
    env = _env_dm()
    env.routing_reason = "listener_observation"
    assert await mgr.handle(env) is None
