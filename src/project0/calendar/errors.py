"""Exceptions raised by the Google Calendar integration."""

from __future__ import annotations


class GoogleCalendarError(Exception):
    """Raised by the GoogleCalendar client on any failure.

    Wraps the underlying exception (``googleapiclient.errors.HttpError``,
    auth failure, network error, etc.) via exception chaining. Callers
    catch this single type. Mirrors ``LLMProviderError`` from 6a.
    """
