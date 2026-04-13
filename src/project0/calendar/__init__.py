"""Google Calendar integration (sub-project 6b).

Public surface: :class:`GoogleCalendar`, :class:`CalendarEvent`,
:class:`GoogleCalendarError`. Everything else in the submodules is an
implementation detail.
"""

from project0.calendar.client import GoogleCalendar
from project0.calendar.errors import GoogleCalendarError
from project0.calendar.model import CalendarEvent

__all__ = ["CalendarEvent", "GoogleCalendar", "GoogleCalendarError"]
