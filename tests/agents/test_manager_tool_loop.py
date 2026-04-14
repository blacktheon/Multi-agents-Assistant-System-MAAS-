# tests/agents/test_manager_tool_loop.py
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

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


def _mgr(llm, calendar=None, messages_store=None, clock=None, user_tz=None):
    return Manager(
        llm=llm, calendar=calendar, memory=None,
        messages_store=messages_store, persona=_persona(), config=_config(),
        user_tz=user_tz or ZoneInfo("UTC"),
        clock=clock,
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


@pytest.mark.asyncio
async def test_pulse_path_returns_none_on_nonempty_text_without_delegation():
    """Pulse mode: any plain-text result (including non-empty) must return
    None so the orchestrator does NOT emit a visible Telegram message. The
    pulse envelope is already persisted by handle_pulse as the audit trail.
    Only a queued delegation should cause a visible outbound message."""
    fake = FakeProvider(tool_responses=[
        ToolUseResult(
            kind="text",
            text="未来 60 分钟无事",
            tool_calls=[],
            stop_reason="end_turn",
        ),
    ])
    mgr = _mgr(fake, messages_store=_FakeMessagesStore())

    result = await mgr.handle(_env_pulse())
    assert result is None


@pytest.mark.asyncio
async def test_preamble_predawn_tomorrow_is_today_from_7am():
    """At 00:45 local time the user is still in last night's tail, so
    「明天」should resolve to today's calendar date from 07:00, not the
    next calendar date. The preamble must contain those literal strings
    so the model can't compute a different window from its training prior.
    """
    fake = FakeProvider(tool_responses=[
        ToolUseResult(kind="text", text="ok", tool_calls=[], stop_reason="end_turn"),
    ])
    # 00:45 Shanghai = 16:45 UTC the previous day
    predawn = datetime(2026, 4, 14, 16, 45, tzinfo=UTC)
    mgr = _mgr(
        fake,
        messages_store=_FakeMessagesStore(),
        clock=lambda: predawn,
        user_tz=ZoneInfo("Asia/Shanghai"),
    )

    await mgr.handle(_env_dm("明天我都有什么活动"))

    user_text = fake.tool_calls_log[0]["messages"][0].content
    # Pre-dawn → "明天" resolves to today's calendar date (2026-04-15)
    # from 07:00, NOT 2026-04-16.
    assert "2026-04-15 00:45" in user_text  # current time
    assert "「明天」= 2026-04-15 07:00" in user_text
    assert "2026-04-16" not in user_text
    # Preamble carries the pre-dawn note.
    assert "昨晚的延长线" in user_text


@pytest.mark.asyncio
async def test_preamble_daytime_tomorrow_is_next_calendar_day():
    """At 10:00 local time '明天' resolves to the next calendar date
    from 07:00, the normal case."""
    fake = FakeProvider(tool_responses=[
        ToolUseResult(kind="text", text="ok", tool_calls=[], stop_reason="end_turn"),
    ])
    # 10:00 Shanghai = 02:00 UTC same day
    daytime = datetime(2026, 4, 15, 2, 0, tzinfo=UTC)
    mgr = _mgr(
        fake,
        messages_store=_FakeMessagesStore(),
        clock=lambda: daytime,
        user_tz=ZoneInfo("Asia/Shanghai"),
    )

    await mgr.handle(_env_dm("明天我都有什么活动"))

    user_text = fake.tool_calls_log[0]["messages"][0].content
    assert "2026-04-15 10:00" in user_text
    assert "「明天」= 2026-04-16 07:00" in user_text
    assert "昨晚的延长线" not in user_text


@pytest.mark.asyncio
async def test_initial_user_text_includes_current_time_preamble():
    """Chat turns must include a 'current time' preamble so the model
    doesn't hallucinate 'tomorrow' based on its training cutoff."""
    fake = FakeProvider(tool_responses=[
        ToolUseResult(kind="text", text="ok", tool_calls=[], stop_reason="end_turn"),
    ])
    fixed_now = datetime(2026, 4, 14, 16, 30, tzinfo=UTC)
    mgr = _mgr(
        fake,
        messages_store=_FakeMessagesStore(),
        clock=lambda: fixed_now,
        user_tz=ZoneInfo("Asia/Shanghai"),
    )

    await mgr.handle(_env_dm("你好"))

    assert len(fake.tool_calls_log) == 1
    initial_msgs = fake.tool_calls_log[0]["messages"]
    user_text = initial_msgs[0].content
    assert "当前时间" in user_text
    # fixed_now = 16:30 UTC → 00:30 on 2026-04-15 Asia/Shanghai
    assert "2026-04-15" in user_text
    assert "00:30" in user_text
    assert "Asia/Shanghai" in user_text
