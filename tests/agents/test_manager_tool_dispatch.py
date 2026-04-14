# tests/agents/test_manager_tool_dispatch.py
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from project0.agents.manager import Manager, ManagerConfig, ManagerPersona, TurnState
from project0.calendar.errors import GoogleCalendarError
from project0.calendar.model import CalendarEvent
from project0.llm.tools import ToolCall


def _persona():
    return ManagerPersona(
        core="c", dm_mode="d", group_addressed_mode="g",
        pulse_mode="p", tool_use_guide="t",
    )


def _config():
    return ManagerConfig(
        model="m", max_tokens_reply=100, max_tool_iterations=8, transcript_window=10,
    )


def _mgr(calendar):
    return Manager(
        llm=None,  # not used by dispatch tests
        calendar=calendar,
        memory=None,
        messages_store=None,
        persona=_persona(),
        config=_config(),
    )


def test_tool_specs_include_all_six_tools():
    mgr = _mgr(calendar=None)
    names = {t.name for t in mgr._tool_specs}
    assert names == {
        "calendar_list_events",
        "calendar_create_event",
        "calendar_update_event",
        "calendar_delete_event",
        "delegate_to_secretary",
        "delegate_to_intelligence",
    }


@pytest.mark.asyncio
async def test_dispatch_list_events_success():
    fake_event = CalendarEvent(
        id="e1", summary="牙医", start=datetime(2026, 4, 15, 10, tzinfo=ZoneInfo("UTC")),
        end=datetime(2026, 4, 15, 11, tzinfo=ZoneInfo("UTC")),
        all_day=False, description=None, location=None, html_link="https://x",
    )
    cal = AsyncMock()
    cal.list_events = AsyncMock(return_value=[fake_event])
    mgr = _mgr(cal)
    ts = TurnState()

    content, is_err = await mgr._dispatch_tool(
        ToolCall(
            id="1", name="calendar_list_events",
            input={
                "time_min": "2026-04-14T00:00:00+00:00",
                "time_max": "2026-04-20T00:00:00+00:00",
                "max_results": 10,
            },
        ),
        ts,
    )
    assert is_err is False
    assert "牙医" in content
    cal.list_events.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_list_events_calendar_error():
    cal = AsyncMock()
    cal.list_events = AsyncMock(side_effect=GoogleCalendarError("boom"))
    mgr = _mgr(cal)
    ts = TurnState()

    content, is_err = await mgr._dispatch_tool(
        ToolCall(
            id="1", name="calendar_list_events",
            input={
                "time_min": "2026-04-14T00:00:00+00:00",
                "time_max": "2026-04-20T00:00:00+00:00",
                "max_results": 10,
            },
        ),
        ts,
    )
    assert is_err is True
    assert "boom" in content


@pytest.mark.asyncio
async def test_dispatch_delegate_to_secretary_sets_turn_state():
    mgr = _mgr(calendar=None)
    ts = TurnState()

    content, is_err = await mgr._dispatch_tool(
        ToolCall(
            id="1", name="delegate_to_secretary",
            input={
                "reminder_text": "下周三牙医",
                "appointment": "牙医",
                "when": "下周三 10:00",
                "note": "记得带病历",
            },
        ),
        ts,
    )
    assert is_err is False
    assert content == "delegated"
    assert ts.delegation_target == "secretary"
    assert "下周三牙医" in ts.delegation_handoff
    assert ts.delegation_payload == {
        "kind": "reminder_request",
        "appointment": "牙医",
        "when": "下周三 10:00",
        "note": "记得带病历",
    }


@pytest.mark.asyncio
async def test_dispatch_delegate_to_intelligence():
    mgr = _mgr(calendar=None)
    ts = TurnState()

    content, is_err = await mgr._dispatch_tool(
        ToolCall(id="1", name="delegate_to_intelligence", input={"query": "OpenAI news"}),
        ts,
    )
    assert content == "delegated"
    assert ts.delegation_target == "intelligence"
    assert ts.delegation_payload == {"kind": "query", "query": "OpenAI news"}


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_returns_error():
    mgr = _mgr(calendar=None)
    ts = TurnState()
    content, is_err = await mgr._dispatch_tool(
        ToolCall(id="1", name="bogus", input={}), ts
    )
    assert is_err is True
    assert "unknown tool" in content


@pytest.mark.asyncio
async def test_dispatch_invalid_input_returns_error():
    mgr = _mgr(calendar=None)
    ts = TurnState()
    content, is_err = await mgr._dispatch_tool(
        ToolCall(id="1", name="delegate_to_secretary", input={}),  # missing reminder_text
        ts,
    )
    assert is_err is True
