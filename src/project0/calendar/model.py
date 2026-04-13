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
    """Translate a raw Google event dict into a :class:`CalendarEvent`."""
    start_raw = raw.get("start", {})
    end_raw = raw.get("end", {})

    start_dt, all_day = _parse_endpoint(start_raw, user_tz)
    end_dt, _ = _parse_endpoint(end_raw, user_tz)

    _warn_unknown_fields(raw)

    return CalendarEvent(
        id=raw.get("id", ""),
        summary=raw.get("summary", ""),
        start=start_dt,
        end=end_dt,
        all_day=all_day,
        description=raw.get("description"),
        location=raw.get("location"),
        html_link=raw.get("htmlLink", ""),
    )


def _parse_endpoint(
    endpoint: dict[str, Any],
    user_tz: ZoneInfo,
) -> tuple[datetime, bool]:
    """Parse one side of a Google event's start/end payload.

    Returns (datetime-in-user_tz, all_day). Handles both the ``dateTime``
    shape (normal events) and the ``date`` shape (all-day events).
    """
    if "dateTime" in endpoint:
        parsed = datetime.fromisoformat(endpoint["dateTime"])
        return parsed.astimezone(user_tz), False
    if "date" in endpoint:
        # All-day events: Google gives us a calendar date with no tz.
        # Materialize it as midnight in the user's local timezone.
        date_str: str = endpoint["date"]
        naive = datetime.fromisoformat(date_str)
        return naive.replace(tzinfo=user_tz), True
    raise ValueError(f"event endpoint has neither dateTime nor date: {endpoint!r}")


def _warn_unknown_fields(raw: dict[str, Any]) -> None:
    """Log a rate-limited warning for any top-level key we don't recognise."""
    unknown = set(raw.keys()) - _CONSUMED_KEYS - _IGNORABLE_KEYS
    for key in unknown:
        if key in _warned_unknown_keys:
            continue
        _warned_unknown_keys.add(key)
        logger.warning("unknown GCal event field: %s", key)


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
    values.
    """
    body: dict[str, Any] = {}
    if summary is not None:
        body["summary"] = summary
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location
    if start is not None:
        _require_aware(start, "start")
        body["start"] = {"dateTime": start.isoformat()}
    if end is not None:
        _require_aware(end, "end")
        body["end"] = {"dateTime": end.isoformat()}
    return body


def _require_aware(dt: datetime, field_name: str) -> None:
    """Reject naive datetimes at the boundary.

    Naive datetimes are the #1 cause of calendar bugs — they silently
    adopt whatever timezone the serializer assumes, which is rarely the
    one the caller thought. Raising here is programmer-error loud.
    """
    if dt.tzinfo is None:
        raise ValueError(
            f"{field_name} must be a timezone-aware datetime; got a naive value"
        )
