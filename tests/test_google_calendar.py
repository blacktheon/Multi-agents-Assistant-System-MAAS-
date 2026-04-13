"""Unit tests for src/project0/calendar/."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from googleapiclient.http import HttpMockSequence

from project0.calendar import model as model_mod
from project0.calendar.client import GoogleCalendar
from project0.calendar.model import CalendarEvent, model_to_raw, raw_event_to_model

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "google_calendar"
BEIJING = ZoneInfo("Asia/Shanghai")


def load_fixture(name: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    return result


@pytest.fixture(autouse=True)
def _reset_unknown_field_warnings() -> None:
    """Clear the translator's module-level 'warned keys' set before each test."""
    model_mod._warned_unknown_keys.clear()


def test_list_events_dateTime_translates_to_user_tz() -> None:
    raw = load_fixture("list_single_dateTime.json")["items"][0]

    event = raw_event_to_model(raw, BEIJING)

    assert event.id == "abc123def456"
    assert event.summary == "Coffee with prof"
    assert event.description == "Discuss the April review schedule"
    assert event.location == "Starbucks Nanjing West Rd"
    assert event.html_link == "https://www.google.com/calendar/event?eid=abc123"
    assert event.all_day is False
    assert event.start.tzinfo is not None
    assert event.end.tzinfo is not None
    assert event.start.isoformat() == "2026-04-15T14:00:00+08:00"
    assert event.end.isoformat() == "2026-04-15T15:00:00+08:00"


def test_list_events_empty_result_returns_no_events() -> None:
    # This test exercises the caller shape: an empty items list yields zero
    # translated events without error. The translator itself is never called.
    raw = {"kind": "calendar#events", "items": []}
    items: list[dict[str, Any]] = raw.get("items", [])
    result = [raw_event_to_model(item, BEIJING) for item in items]
    assert result == []


def test_list_events_all_day_normalization() -> None:
    raw = load_fixture("list_all_day.json")["items"][0]

    event = raw_event_to_model(raw, BEIJING)

    assert event.all_day is True
    assert event.summary == "Tomb Sweeping Day"
    assert event.start.tzinfo is not None
    assert event.end.tzinfo is not None
    # Midnight Beijing, not UTC, not naive.
    assert event.start.isoformat() == "2026-04-05T00:00:00+08:00"
    assert event.end.isoformat() == "2026-04-06T00:00:00+08:00"


def test_list_events_source_timezone_conversion() -> None:
    # An event whose source tz is Los Angeles should be converted to Beijing
    # wall clock while preserving the instant.
    raw: dict[str, Any] = {
        "id": "la001",
        "summary": "Standup with LA team",
        "htmlLink": "https://example.invalid/la001",
        "start": {"dateTime": "2026-04-15T09:00:00-07:00", "timeZone": "America/Los_Angeles"},
        "end": {"dateTime": "2026-04-15T09:30:00-07:00", "timeZone": "America/Los_Angeles"},
    }

    event = raw_event_to_model(raw, BEIJING)

    # 9:00 LA = 00:00 next day Beijing (LA is UTC-7, Beijing UTC+8).
    assert event.start.isoformat() == "2026-04-16T00:00:00+08:00"
    assert event.end.isoformat() == "2026-04-16T00:30:00+08:00"
    assert event.all_day is False


def test_unknown_field_logs_warning_once(caplog: pytest.LogCaptureFixture) -> None:
    raw = load_fixture("get_with_unknown_field.json")

    caplog.set_level("WARNING", logger="project0.calendar.model")

    raw_event_to_model(raw, BEIJING)
    first_pass_warnings = [
        rec.message for rec in caplog.records
        if "futureFieldFromGoogle" in rec.message
    ]
    assert len(first_pass_warnings) == 1
    assert first_pass_warnings[0] == "unknown GCal event field: futureFieldFromGoogle"

    caplog.clear()
    raw_event_to_model(raw, BEIJING)
    second_pass_warnings = [
        rec.message for rec in caplog.records
        if "futureFieldFromGoogle" in rec.message
    ]
    assert second_pass_warnings == []  # rate-limited: no second warning


def test_model_to_raw_full_body() -> None:
    start = datetime.fromisoformat("2026-04-20T10:00:00+08:00")
    end = datetime.fromisoformat("2026-04-20T11:00:00+08:00")

    body = model_to_raw(
        summary="Meeting",
        start=start,
        end=end,
        description="Project 0 sync",
        location="Room 3",
    )

    assert body == {
        "summary": "Meeting",
        "description": "Project 0 sync",
        "location": "Room 3",
        "start": {"dateTime": "2026-04-20T10:00:00+08:00"},
        "end": {"dateTime": "2026-04-20T11:00:00+08:00"},
    }


def test_model_to_raw_partial_update_omits_nones() -> None:
    # Simulating an update_event(event_id, summary="new") call: only summary
    # should appear in the resulting body. Nothing else, especially no
    # null-valued description/location/start/end that would blank existing
    # values on Google's side.
    body = model_to_raw(summary="new title")
    assert body == {"summary": "new title"}


def test_model_to_raw_rejects_naive_datetime() -> None:
    naive = datetime(2026, 4, 20, 10, 0, 0)  # no tzinfo
    with pytest.raises(ValueError, match="timezone"):
        model_to_raw(start=naive)


def test_unknown_field_does_not_warn_for_ignorable_keys() -> None:
    # "reminders" is in the ignorable set — no warning should fire.
    raw: dict[str, Any] = {
        "id": "quiet001",
        "summary": "Quiet event",
        "htmlLink": "https://example.invalid/quiet001",
        "start": {"dateTime": "2026-04-20T10:00:00+08:00", "timeZone": "Asia/Shanghai"},
        "end": {"dateTime": "2026-04-20T11:00:00+08:00", "timeZone": "Asia/Shanghai"},
        "reminders": {"useDefault": True},
    }

    assert model_mod._warned_unknown_keys == set()
    raw_event_to_model(raw, BEIJING)
    assert model_mod._warned_unknown_keys == set()


def build_test_client(responses: list[tuple[dict[str, str], bytes]]) -> GoogleCalendar:
    """Construct a GoogleCalendar whose SDK service uses HttpMockSequence.

    Each ``(headers, body)`` tuple in ``responses`` is returned by the
    mock HTTP transport in order. Responses map 1:1 to the API calls the
    test makes (the discovery document is already bundled locally and is
    not fetched over HTTP when ``developerKey`` is supplied to ``build``).
    """
    from googleapiclient.discovery import build as _build

    http = HttpMockSequence(list(responses))
    service = _build("calendar", "v3", http=http, developerKey="fake")
    return GoogleCalendar(
        credentials=None,  # type: ignore[arg-type]
        calendar_id="primary",
        user_tz=BEIJING,
        _service=service,
    )


def test_list_events_returns_translated_events() -> None:
    body = json.dumps(load_fixture("list_single_dateTime.json")).encode("utf-8")
    client = build_test_client([({"status": "200"}, body)])

    async def run() -> list[CalendarEvent]:
        now = datetime.fromisoformat("2026-04-14T00:00:00+08:00")
        return await client.list_events(now, now + timedelta(days=7))

    events = asyncio.run(run())

    assert len(events) == 1
    assert events[0].id == "abc123def456"
    assert events[0].summary == "Coffee with prof"


def test_list_events_rejects_naive_datetime() -> None:
    client = build_test_client([])
    naive = datetime(2026, 4, 14, 0, 0, 0)
    aware = datetime.fromisoformat("2026-04-14T00:00:00+08:00")

    async def run() -> None:
        await client.list_events(naive, aware)

    with pytest.raises(ValueError, match="time_min"):
        asyncio.run(run())


def test_get_event_returns_translated_event() -> None:
    # The get-response shape is a single event dict, not the wrapped
    # list response. Our list_single_dateTime fixture's first item is
    # exactly that shape.
    body = json.dumps(load_fixture("list_single_dateTime.json")["items"][0]).encode("utf-8")
    client = build_test_client([({"status": "200"}, body)])

    async def run() -> CalendarEvent:
        return await client.get_event("abc123def456")

    event = asyncio.run(run())
    assert event.id == "abc123def456"
    assert event.summary == "Coffee with prof"
    assert event.all_day is False


def test_create_event_posts_expected_body() -> None:
    # Google returns the created event as the response body.
    created_body = {
        "kind": "calendar#event",
        "id": "newevent001",
        "status": "confirmed",
        "htmlLink": "https://example.invalid/newevent001",
        "summary": "Smoke test",
        "description": "created by test",
        "location": "Room 1",
        "start": {"dateTime": "2026-04-20T14:00:00+08:00", "timeZone": "Asia/Shanghai"},
        "end": {"dateTime": "2026-04-20T15:00:00+08:00", "timeZone": "Asia/Shanghai"},
    }
    client = build_test_client([
        ({"status": "200"}, json.dumps(created_body).encode("utf-8")),
    ])

    async def run() -> CalendarEvent:
        start = datetime.fromisoformat("2026-04-20T14:00:00+08:00")
        end = datetime.fromisoformat("2026-04-20T15:00:00+08:00")
        return await client.create_event(
            summary="Smoke test",
            start=start,
            end=end,
            description="created by test",
            location="Room 1",
        )

    event = asyncio.run(run())
    assert event.id == "newevent001"
    assert event.summary == "Smoke test"


def test_auth_writes_token_chmod_600(tmp_path: Path) -> None:
    # We call the private helper directly: the full flow requires a real
    # Google consent dance and cannot be unit-tested. The chmod step is
    # the only piece we can deterministically verify without the network.
    from unittest.mock import MagicMock

    from project0.calendar import auth as auth_mod

    fake_creds = MagicMock()
    fake_creds.to_json.return_value = '{"token": "fake"}'

    token_path = tmp_path / "subdir" / "google_token.json"
    auth_mod._write_token(token_path, fake_creds)

    assert token_path.exists()
    assert token_path.read_text(encoding="utf-8") == '{"token": "fake"}'
    # On POSIX, check the file mode is exactly 0o600.
    mode = token_path.stat().st_mode & 0o777
    assert mode == 0o600, f"expected mode 0o600, got {oct(mode)}"
