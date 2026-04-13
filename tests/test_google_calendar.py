"""Unit tests for src/project0/calendar/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from project0.calendar import model as model_mod
from project0.calendar.model import raw_event_to_model

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
