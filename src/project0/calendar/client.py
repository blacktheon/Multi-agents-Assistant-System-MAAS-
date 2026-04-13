"""Async wrapper around the Google Calendar v3 SDK."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from project0.calendar.errors import GoogleCalendarError
from project0.calendar.model import (
    CalendarEvent,
    model_to_raw,
    raw_event_to_model,
)

logger = logging.getLogger(__name__)


class GoogleCalendar:
    """Async client for one Google Calendar.

    All methods are coroutines; each delegates to a synchronous helper via
    :func:`asyncio.to_thread` because ``google-api-python-client`` is sync.
    """

    def __init__(
        self,
        credentials: Credentials,
        calendar_id: str,
        user_tz: ZoneInfo,
        *,
        _service: Resource | None = None,
    ) -> None:
        if _service is not None:
            # Test seam: allow tests to inject a pre-built service that
            # uses HttpMockSequence. Production callers never pass this.
            self._service = _service
        else:
            self._service = build(
                "calendar", "v3",
                credentials=credentials,
                cache_discovery=False,
            )
        self._calendar_id = calendar_id
        self._user_tz = user_tz

    async def list_events(
        self,
        time_min: datetime,
        time_max: datetime,
        max_results: int = 250,
    ) -> list[CalendarEvent]:
        _require_aware(time_min, "time_min")
        _require_aware(time_max, "time_max")
        return await asyncio.to_thread(
            self._sync_list_events, time_min, time_max, max_results
        )

    def _sync_list_events(
        self,
        time_min: datetime,
        time_max: datetime,
        max_results: int,
    ) -> list[CalendarEvent]:
        try:
            response: dict[str, Any] = (
                self._service.events().list(
                    calendarId=self._calendar_id,
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=max_results,
                ).execute()
            )
        except HttpError as e:
            raise GoogleCalendarError(
                f"list_events failed: HTTP {e.resp.status}"
            ) from e
        except Exception as e:  # noqa: BLE001 - broad by design, see spec §4
            raise GoogleCalendarError(f"list_events failed: {e}") from e

        if response.get("nextPageToken"):
            logger.warning(
                "list_events truncated at max_results=%d", max_results,
            )

        items: list[dict[str, Any]] = response.get("items", [])
        return [raw_event_to_model(item, self._user_tz) for item in items]


    async def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: str | None = None,
        location: str | None = None,
    ) -> CalendarEvent:
        _require_aware(start, "start")
        _require_aware(end, "end")
        return await asyncio.to_thread(
            self._sync_create_event, summary, start, end, description, location,
        )

    def _sync_create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: str | None,
        location: str | None,
    ) -> CalendarEvent:
        body = model_to_raw(
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
        )
        try:
            raw: dict[str, Any] = (
                self._service.events().insert(
                    calendarId=self._calendar_id,
                    body=body,
                ).execute()
            )
        except HttpError as e:
            raise GoogleCalendarError(
                f"create_event failed: HTTP {e.resp.status}"
            ) from e
        except Exception as e:  # noqa: BLE001
            raise GoogleCalendarError(f"create_event failed: {e}") from e
        return raw_event_to_model(raw, self._user_tz)

    async def update_event(
        self,
        event_id: str,
        *,
        summary: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> CalendarEvent:
        if start is not None:
            _require_aware(start, "start")
        if end is not None:
            _require_aware(end, "end")
        return await asyncio.to_thread(
            self._sync_update_event,
            event_id, summary, start, end, description, location,
        )

    def _sync_update_event(
        self,
        event_id: str,
        summary: str | None,
        start: datetime | None,
        end: datetime | None,
        description: str | None,
        location: str | None,
    ) -> CalendarEvent:
        body = model_to_raw(
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
        )
        try:
            raw: dict[str, Any] = (
                self._service.events().patch(
                    calendarId=self._calendar_id,
                    eventId=event_id,
                    body=body,
                ).execute()
            )
        except HttpError as e:
            raise GoogleCalendarError(
                f"update_event({event_id!r}) failed: HTTP {e.resp.status}"
            ) from e
        except Exception as e:  # noqa: BLE001
            raise GoogleCalendarError(
                f"update_event({event_id!r}) failed: {e}"
            ) from e
        return raw_event_to_model(raw, self._user_tz)

    async def get_event(self, event_id: str) -> CalendarEvent:
        return await asyncio.to_thread(self._sync_get_event, event_id)

    def _sync_get_event(self, event_id: str) -> CalendarEvent:
        try:
            raw: dict[str, Any] = (
                self._service.events().get(
                    calendarId=self._calendar_id,
                    eventId=event_id,
                ).execute()
            )
        except HttpError as e:
            raise GoogleCalendarError(
                f"get_event({event_id!r}) failed: HTTP {e.resp.status}"
            ) from e
        except Exception as e:  # noqa: BLE001
            raise GoogleCalendarError(
                f"get_event({event_id!r}) failed: {e}"
            ) from e
        return raw_event_to_model(raw, self._user_tz)


def _require_aware(dt: datetime, field_name: str) -> None:
    """Reject naive datetimes at the client boundary."""
    if dt.tzinfo is None:
        raise ValueError(
            f"{field_name} must be a timezone-aware datetime; got a naive value"
        )
