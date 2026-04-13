"""Unit tests for src/project0/calendar/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from project0.calendar import model as model_mod
from project0.calendar.model import (
    CalendarEvent,
    model_to_raw,
    raw_event_to_model,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "google_calendar"
BEIJING = ZoneInfo("Asia/Shanghai")


def load_fixture(name: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    return result


@pytest.fixture(autouse=True)
def _reset_unknown_field_warnings() -> None:
    """Clear the translator's module-level 'warned keys' set before each test."""
    model_mod._warned_unknown_keys.clear()
