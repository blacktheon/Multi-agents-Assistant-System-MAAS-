"""Translate between Google Calendar v3 event JSON and CalendarEvent."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Keys Google returns on events that the 6b translator deliberately does not
# consume. Anything outside this set (and outside the keys we do consume)
# triggers a one-shot warning so we notice API drift. If a future sub-project
# needs to start consuming one of these, move it out of this set and into the
# translator.
_IGNORABLE_KEYS: frozenset[str] = frozenset({
    "kind", "etag", "status", "htmlLink", "created", "updated",
    "creator", "organizer", "iCalUID", "sequence", "reminders",
    "eventType", "hangoutLink", "conferenceData", "attachments",
    "attendees", "recurrence", "recurringEventId", "originalStartTime",
    "guestsCanInviteOthers", "guestsCanModify", "guestsCanSeeOtherGuests",
    "privateCopy", "locked", "source", "colorId", "transparency",
    "visibility",
})

# Keys the translator actively reads from raw event dicts.
_CONSUMED_KEYS: frozenset[str] = frozenset({
    "id", "summary", "description", "location", "htmlLink",
    "start", "end",
})

# Module-level state: which unknown keys we have already warned about.
# Cleared by a pytest fixture between tests so test #8 is deterministic.
_warned_unknown_keys: set[str] = set()


@dataclass(frozen=True)
class CalendarEvent:
    """A Google Calendar event, normalized into Project 0's types.

    All datetimes are timezone-aware and expressed in the configured
    ``USER_TIMEZONE``. ``all_day`` is True when Google returned a ``date``
    payload instead of ``dateTime`` — in that case ``start`` and ``end``
    are midnight in the user's timezone, and ``end`` points at the start
    of the day *after* the event (matching Google's convention).
    """

    id: str
    summary: str
    start: datetime
    end: datetime
    all_day: bool
    description: str | None
    location: str | None
    html_link: str


def raw_event_to_model(raw: dict[str, Any], user_tz: ZoneInfo) -> CalendarEvent:
    """Translate a raw Google event dict into a :class:`CalendarEvent`.

    Implementation lands across Tasks 5–8.
    """
    raise NotImplementedError


def model_to_raw(
    *,
    summary: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    description: str | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    """Build a Google event body from explicit fields.

    Only fields the caller passed are emitted. ``None`` fields are omitted
    entirely so partial updates on Google's side never blank out existing
    values. Implementation lands in Task 9.
    """
    raise NotImplementedError
