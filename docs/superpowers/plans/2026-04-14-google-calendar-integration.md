# Sub-Project 6b — Google Calendar Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the Google Calendar substrate (OAuth 2.0 loopback flow, `GoogleCalendar` async client, typed `CalendarEvent` dataclass, manual smoke script) as a standalone, additive sub-project so that 6c's Manager can build on a proven client.

**Architecture:** New `src/project0/calendar/` package with four small modules — `errors.py` (one exception), `model.py` (frozen dataclass + bidirectional translator with defensive reads and rate-limited unknown-field warnings), `auth.py` (sync `load_or_acquire_credentials` that runs the OAuth loopback flow on first use and loads/refreshes on subsequent runs), and `client.py` (the `GoogleCalendar` class with five async methods, each wrapping the sync `google-api-python-client` SDK via `asyncio.to_thread`). One manual `scripts/calendar_smoke.py` exercises the full stack against a real Google account. Unit tests use `googleapiclient.http.HttpMockSequence` so they never hit the network. No changes to `main.py`, `config.py`, the orchestrator, the registry, or any existing agent — 6b is additive only.

**Tech Stack:** Python 3.12, `google-api-python-client`, `google-auth`, `google-auth-oauthlib`, stdlib `zoneinfo` / `asyncio` / `dataclasses` / `pathlib` / `logging`, `pytest` + `pytest-asyncio`, `ruff`, `mypy` (strict).

**Spec:** `docs/superpowers/specs/2026-04-14-google-calendar-integration-design.md` (commit `fb6b386`).

**Key decisions locked in during brainstorming:**
- Google Calendar is the only source of truth for appointments project-wide; no local calendar table, no sync layer (locked by memory `project_calendar_backend.md`)
- OAuth 2.0 Installed App flow with loopback redirect (not device flow, not service account)
- Plain JSON token file at `data/google_token.json`, chmod 600, gitignored (already covered by the existing `data/` rule)
- Single calendar selected by `GOOGLE_CALENDAR_ID` env var (default `primary`); no multi-calendar reads
- `google-api-python-client` (sync) wrapped in `asyncio.to_thread` — not `aiogoogle`, not hand-rolled httpx
- Frozen `CalendarEvent` dataclass with translator layer; all datetimes timezone-aware in `USER_TIMEZONE`; naive datetimes raise `ValueError`
- `USER_TIMEZONE=Asia/Shanghai` default, validated with `zoneinfo.ZoneInfo`, restart-to-change
- OAuth scope is `calendar.events` (least privilege; read + write events but no calendar-level management)
- `singleEvents=True` default on `list_events` (recurring events expanded to instances; recurring-series editing explicitly out of scope)
- Unknown-field warnings in the translator are rate-limited to once per key per process (module-level `set`)
- Tests use inline Python dicts mirroring Google's schema during TDD; real JSON fixture files are captured via `scripts/calendar_smoke.py --dump-fixtures` as an acceptance gate and tests are switched to load them at the end
- Manual smoke script is the only code path that constructs a `GoogleCalendar` in 6b; `main.py` / `config.py` wiring lands in 6c

---

## File Structure

**Create:**
- `src/project0/calendar/__init__.py` — re-exports `GoogleCalendar`, `CalendarEvent`, `GoogleCalendarError`
- `src/project0/calendar/errors.py` — `GoogleCalendarError` exception class
- `src/project0/calendar/model.py` — `CalendarEvent` dataclass, `raw_event_to_model`, `model_to_raw`, unknown-field warning state
- `src/project0/calendar/auth.py` — `load_or_acquire_credentials`
- `src/project0/calendar/client.py` — `GoogleCalendar` async client with five methods and their sync helpers
- `scripts/calendar_smoke.py` — seven-step manual smoke test with `--dump-fixtures` flag
- `tests/test_google_calendar.py` — unit tests for translator + client + auth (ten tests total)
- `tests/fixtures/google_calendar/list_single_dateTime.json` — golden fixture (bootstrap hand-crafted, overwritten with real data at acceptance)
- `tests/fixtures/google_calendar/list_all_day.json` — golden fixture
- `tests/fixtures/google_calendar/get_with_unknown_field.json` — golden fixture
- `tests/fixtures/google_calendar/error_404.json` — golden fixture

**Modify:**
- `pyproject.toml` — add `google-api-python-client`, `google-auth`, `google-auth-oauthlib` to `[project].dependencies`
- `.env.example` — add `USER_TIMEZONE=Asia/Shanghai` and `GOOGLE_CALENDAR_ID=primary` with README pointer
- `.gitignore` — add `tests/fixtures/google_calendar/*.local.json` pattern for ad-hoc captures (the `data/` rule already covers `google_token.json` and `google_client_secrets.json`)
- `README.md` — add "Google Cloud setup" section and "6b smoke test" section

**Unchanged** (verify at end of plan): `src/project0/main.py`, `src/project0/config.py`, `src/project0/orchestrator.py`, `src/project0/agents/*`, `src/project0/llm/*`, `src/project0/store.py`, `src/project0/envelope.py`, `src/project0/agents/registry.py`, `src/project0/telegram_io.py`, `src/project0/mentions.py`, `src/project0/errors.py`, `prompts/*`.

---

## Bootstrap Fixture Note

The spec mandates that unit tests load real Google JSON fixtures captured via `scripts/calendar_smoke.py --dump-fixtures`. This creates a chicken-and-egg during TDD: the translator and client need tests before the smoke script runs, but the fixtures don't exist yet.

**Resolution:** Task 2 creates hand-crafted bootstrap JSON fixture files that closely mirror Google's documented event shape. Tests load from these files throughout Tasks 3–15. At acceptance (Task 19), the smoke script is run with `--dump-fixtures` against a real Google account, overwriting the bootstrap files with real captured data. Tests are re-run; if any fail because the real Google shape differs from the bootstrap guess, fix the translator (the real data is ground truth). The final commit includes the real captured fixtures, not the bootstrap ones. From that point on, "hand-editing fixtures is not allowed" applies — refreshes go through `--dump-fixtures` only.

---

## Task 1: Dependencies and package scaffolding

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `src/project0/calendar/__init__.py` (empty placeholder)

- [ ] **Step 1: Add the three Google dependencies via uv**

Run:
```bash
uv add "google-api-python-client>=2.150,<3" "google-auth>=2.35,<3" "google-auth-oauthlib>=1.2,<2"
```

Expected: `uv` resolves and installs the three packages; `pyproject.toml` is updated; `uv.lock` is updated.

- [ ] **Step 2: Verify `pyproject.toml` shows the new deps**

Run: `grep -A 10 "dependencies = \[" pyproject.toml`

Expected: the `[project].dependencies` list now contains `google-api-python-client`, `google-auth`, and `google-auth-oauthlib` alongside the existing four.

- [ ] **Step 3: Append the local-fixtures ignore pattern to `.gitignore`**

Edit `.gitignore` to append at the end:

```
# Google Calendar (6b) — ad-hoc fixture captures that should not land in the repo
tests/fixtures/google_calendar/*.local.json
```

Note: `data/` is already gitignored from the skeleton, so `data/google_token.json` and `data/google_client_secrets.json` need no additional entries — they are covered automatically.

- [ ] **Step 4: Create the empty calendar package**

```bash
mkdir -p src/project0/calendar
```

Create `src/project0/calendar/__init__.py` with exactly:

```python
"""Google Calendar integration (sub-project 6b).

Public surface is populated as the submodules land; see errors.py, model.py,
auth.py, client.py.
"""
```

- [ ] **Step 5: Verify the existing test suite still passes**

Run: `uv run pytest`

Expected: all existing tests pass, zero failures. Dependency additions and the empty package must not regress anything.

- [ ] **Step 6: Verify mypy and ruff are clean**

Run: `uv run mypy src/project0 && uv run ruff check src/project0 tests`

Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .gitignore src/project0/calendar/__init__.py
git commit -m "feat(calendar): scaffold 6b — deps and empty package

Add google-api-python-client, google-auth, google-auth-oauthlib to the
main dependency list and create an empty src/project0/calendar/ package
ready for subsequent tasks to land modules into. Gitignore pattern for
ad-hoc test fixture captures. No behavior change."
```

---

## Task 2: Bootstrap fixture files and fixtures directory

**Files:**
- Create: `tests/fixtures/google_calendar/list_single_dateTime.json`
- Create: `tests/fixtures/google_calendar/list_all_day.json`
- Create: `tests/fixtures/google_calendar/get_with_unknown_field.json`
- Create: `tests/fixtures/google_calendar/error_404.json`

- [ ] **Step 1: Create the fixtures directory**

```bash
mkdir -p tests/fixtures/google_calendar
```

- [ ] **Step 2: Write `list_single_dateTime.json`**

This is the shape `events.list` returns for a single normal (dateTime) event. Create `tests/fixtures/google_calendar/list_single_dateTime.json`:

```json
{
  "kind": "calendar#events",
  "etag": "\"p33a9ecb3f6mb8\"",
  "summary": "user@example.com",
  "updated": "2026-04-14T10:00:00.000Z",
  "timeZone": "Asia/Shanghai",
  "accessRole": "owner",
  "items": [
    {
      "kind": "calendar#event",
      "etag": "\"3400000000000000\"",
      "id": "abc123def456",
      "status": "confirmed",
      "htmlLink": "https://www.google.com/calendar/event?eid=abc123",
      "created": "2026-04-10T09:00:00.000Z",
      "updated": "2026-04-10T09:00:00.000Z",
      "summary": "Coffee with prof",
      "description": "Discuss the April review schedule",
      "location": "Starbucks Nanjing West Rd",
      "creator": {"email": "user@example.com", "self": true},
      "organizer": {"email": "user@example.com", "self": true},
      "start": {"dateTime": "2026-04-15T14:00:00+08:00", "timeZone": "Asia/Shanghai"},
      "end": {"dateTime": "2026-04-15T15:00:00+08:00", "timeZone": "Asia/Shanghai"},
      "iCalUID": "abc123def456@google.com",
      "sequence": 0,
      "reminders": {"useDefault": true},
      "eventType": "default"
    }
  ]
}
```

- [ ] **Step 3: Write `list_all_day.json`**

Create `tests/fixtures/google_calendar/list_all_day.json`:

```json
{
  "kind": "calendar#events",
  "etag": "\"p33a9ecb3f6mb8\"",
  "summary": "user@example.com",
  "updated": "2026-04-14T10:00:00.000Z",
  "timeZone": "Asia/Shanghai",
  "accessRole": "owner",
  "items": [
    {
      "kind": "calendar#event",
      "etag": "\"3400000000000001\"",
      "id": "allday001",
      "status": "confirmed",
      "htmlLink": "https://www.google.com/calendar/event?eid=allday001",
      "created": "2026-04-10T09:00:00.000Z",
      "updated": "2026-04-10T09:00:00.000Z",
      "summary": "Tomb Sweeping Day",
      "creator": {"email": "user@example.com", "self": true},
      "organizer": {"email": "user@example.com", "self": true},
      "start": {"date": "2026-04-05"},
      "end": {"date": "2026-04-06"},
      "transparency": "transparent",
      "iCalUID": "allday001@google.com",
      "sequence": 0,
      "eventType": "default"
    }
  ]
}
```

- [ ] **Step 4: Write `get_with_unknown_field.json`**

This is a single `events.get` response body (not an `events.list` wrapper) containing a synthetic top-level field `futureFieldFromGoogle` that is not in the translator's ignorable set. Create `tests/fixtures/google_calendar/get_with_unknown_field.json`:

```json
{
  "kind": "calendar#event",
  "etag": "\"3400000000000002\"",
  "id": "unknownfield001",
  "status": "confirmed",
  "htmlLink": "https://www.google.com/calendar/event?eid=unknownfield001",
  "created": "2026-04-10T09:00:00.000Z",
  "updated": "2026-04-10T09:00:00.000Z",
  "summary": "Event with an unknown future field",
  "creator": {"email": "user@example.com", "self": true},
  "organizer": {"email": "user@example.com", "self": true},
  "start": {"dateTime": "2026-04-20T10:00:00+08:00", "timeZone": "Asia/Shanghai"},
  "end": {"dateTime": "2026-04-20T11:00:00+08:00", "timeZone": "Asia/Shanghai"},
  "iCalUID": "unknownfield001@google.com",
  "sequence": 0,
  "reminders": {"useDefault": true},
  "eventType": "default",
  "futureFieldFromGoogle": {"someValue": 42}
}
```

- [ ] **Step 5: Write `error_404.json`**

This is the body shape Google returns for "event not found". Create `tests/fixtures/google_calendar/error_404.json`:

```json
{
  "error": {
    "code": 404,
    "message": "Not Found",
    "errors": [
      {"domain": "global", "reason": "notFound", "message": "Not Found"}
    ]
  }
}
```

- [ ] **Step 6: Commit the bootstrap fixtures**

```bash
git add tests/fixtures/google_calendar/
git commit -m "test(calendar): bootstrap golden fixture files

Hand-crafted fixtures that mirror the documented Google Calendar v3 event
and error shapes. These are bootstrap placeholders so TDD can proceed
before the smoke script exists. At 6b acceptance the smoke script is run
with --dump-fixtures and these files are overwritten with real captured
Google responses, then committed as the final ground truth."
```

---

## Task 3: `errors.py` — `GoogleCalendarError`

**Files:**
- Create: `src/project0/calendar/errors.py`

- [ ] **Step 1: Write the module**

Create `src/project0/calendar/errors.py` with exactly:

```python
"""Exceptions raised by the Google Calendar integration."""

from __future__ import annotations


class GoogleCalendarError(Exception):
    """Raised by the GoogleCalendar client on any failure.

    Wraps the underlying exception (``googleapiclient.errors.HttpError``,
    auth failure, network error, etc.) via exception chaining. Callers
    catch this single type. Mirrors ``LLMProviderError`` from 6a.
    """
```

- [ ] **Step 2: Verify mypy and ruff are clean on the new module**

Run: `uv run mypy src/project0/calendar && uv run ruff check src/project0/calendar`

Expected: both clean.

- [ ] **Step 3: Commit**

```bash
git add src/project0/calendar/errors.py
git commit -m "feat(calendar): add GoogleCalendarError exception

Single exception class all calendar client failures funnel through.
Mirrors LLMProviderError from 6a."
```

---

## Task 4: `model.py` — `CalendarEvent` dataclass skeleton

**Files:**
- Create: `src/project0/calendar/model.py`
- Create: `tests/test_google_calendar.py` (initial skeleton with imports only)

This task introduces the dataclass and the empty translator stubs so later tasks can TDD behavior onto them. No translator logic yet — that lands in Tasks 5–9.

- [ ] **Step 1: Write the module skeleton**

Create `src/project0/calendar/model.py`:

```python
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
```

- [ ] **Step 2: Write the initial test file skeleton**

Create `tests/test_google_calendar.py`:

```python
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
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _reset_unknown_field_warnings() -> None:
    """Clear the translator's module-level 'warned keys' set before each test."""
    model_mod._warned_unknown_keys.clear()
```

- [ ] **Step 3: Verify the file is importable and the fixture resets**

Run: `uv run pytest tests/test_google_calendar.py -v`

Expected: zero tests collected (no `test_*` functions yet), the file imports cleanly, the autouse fixture is defined. Output should contain `collected 0 items`.

- [ ] **Step 4: Verify mypy**

Run: `uv run mypy src/project0/calendar tests/test_google_calendar.py`

Expected: clean. The strict mode requires the type annotations on `load_fixture` and `_reset_unknown_field_warnings`.

- [ ] **Step 5: Commit**

```bash
git add src/project0/calendar/model.py tests/test_google_calendar.py
git commit -m "feat(calendar): CalendarEvent dataclass + translator skeleton

Frozen dataclass with typed fields, translator stubs, and module-level
unknown-field warning state. Test file skeleton loads fixtures and
resets warning state between tests. No behavior yet — implementation
lands in Tasks 5–9."
```

---

## Task 5: Translator — dateTime event happy path

**Files:**
- Modify: `src/project0/calendar/model.py`
- Modify: `tests/test_google_calendar.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_google_calendar.py`:

```python
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
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `uv run pytest tests/test_google_calendar.py::test_list_events_dateTime_translates_to_user_tz -v`

Expected: `FAILED` with `NotImplementedError` from `raw_event_to_model`.

- [ ] **Step 3: Implement the happy path in `raw_event_to_model`**

Replace the body of `raw_event_to_model` in `src/project0/calendar/model.py`:

```python
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
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `uv run pytest tests/test_google_calendar.py::test_list_events_dateTime_translates_to_user_tz -v`

Expected: `PASSED`.

- [ ] **Step 5: Verify mypy + ruff still clean**

Run: `uv run mypy src/project0/calendar && uv run ruff check src/project0/calendar tests/test_google_calendar.py`

Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/project0/calendar/model.py tests/test_google_calendar.py
git commit -m "feat(calendar): translate dateTime events into CalendarEvent

Defensive raw.get() reads, split endpoint parser, astimezone() to the
user's configured tz, unknown-field warning helper. First green test."
```

---

## Task 6: Translator — empty list, all-day normalization, source tz conversion

**Files:**
- Modify: `tests/test_google_calendar.py`

No production code changes — the translator already handles these cases correctly. This task is TDD in the defensive direction: lock the behaviors down with tests so a future refactor can't regress them silently.

- [ ] **Step 1: Add three new tests**

Append to `tests/test_google_calendar.py`:

```python
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
```

- [ ] **Step 2: Run the new tests**

Run: `uv run pytest tests/test_google_calendar.py -v -k "empty_result or all_day or source_timezone"`

Expected: three `PASSED`. If any fails, the failure points at a translator bug you need to fix before committing.

- [ ] **Step 3: Run the whole test file to confirm no regressions**

Run: `uv run pytest tests/test_google_calendar.py -v`

Expected: four tests, all `PASSED`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_google_calendar.py
git commit -m "test(calendar): lock down empty/all-day/cross-tz translation

Three regression tests covering the empty items list, all-day event
normalization to midnight-in-user-tz, and source-timezone conversion
via astimezone()."
```

---

## Task 7: Translator — unknown-field warning and rate limiting

**Files:**
- Modify: `tests/test_google_calendar.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_google_calendar.py`:

```python
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
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_google_calendar.py::test_unknown_field_logs_warning_once tests/test_google_calendar.py::test_unknown_field_does_not_warn_for_ignorable_keys -v`

Expected: both `PASSED`. The warning logic was implemented in Task 5 — these tests exist to lock the behavior in and document the rate-limiting contract.

- [ ] **Step 3: Run the whole test file**

Run: `uv run pytest tests/test_google_calendar.py -v`

Expected: six tests, all `PASSED`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_google_calendar.py
git commit -m "test(calendar): unknown-field warning fires once per key

Verifies a synthetic futureFieldFromGoogle key triggers exactly one
warning on first translation and zero on repeat, and confirms known
ignorable keys like reminders stay silent."
```

---

## Task 8: Translator — `model_to_raw` partial-update safety

**Files:**
- Modify: `src/project0/calendar/model.py`
- Modify: `tests/test_google_calendar.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_google_calendar.py`:

```python
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
```

The first test requires `datetime` to be importable in the test module. Ensure the import is present:

```python
from datetime import datetime  # add near the top of the test file if missing
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `uv run pytest tests/test_google_calendar.py -v -k "model_to_raw"`

Expected: all three `FAILED` with `NotImplementedError` from `model_to_raw`.

- [ ] **Step 3: Implement `model_to_raw`**

Replace the body of `model_to_raw` in `src/project0/calendar/model.py`:

```python
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
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_google_calendar.py -v -k "model_to_raw"`

Expected: three `PASSED`.

- [ ] **Step 5: Run the whole file**

Run: `uv run pytest tests/test_google_calendar.py -v`

Expected: nine tests, all `PASSED`.

- [ ] **Step 6: Verify mypy and ruff**

Run: `uv run mypy src/project0/calendar && uv run ruff check src/project0/calendar tests/test_google_calendar.py`

Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add src/project0/calendar/model.py tests/test_google_calendar.py
git commit -m "feat(calendar): model_to_raw with partial-update safety

Emits only fields the caller passed — None fields are omitted so
update_event(event_id, summary=...) never blanks existing values.
Rejects naive datetimes at the boundary as programmer error."
```

---

## Task 9: `auth.py` — `load_or_acquire_credentials`

**Files:**
- Create: `src/project0/calendar/auth.py`
- Modify: `tests/test_google_calendar.py`

- [ ] **Step 1: Write the module**

Create `src/project0/calendar/auth.py`:

```python
"""OAuth 2.0 installed-app flow + token load/save for Google Calendar."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from project0.calendar.errors import GoogleCalendarError

logger = logging.getLogger(__name__)

SCOPES: list[str] = ["https://www.googleapis.com/auth/calendar.events"]


def load_or_acquire_credentials(
    token_path: Path,
    client_secrets_path: Path,
    scopes: list[str] | None = None,
) -> Credentials:
    """Return valid credentials, running OAuth loopback on first use.

    Behavior:
      1. If ``token_path`` exists and the token is valid, load and return it.
      2. If it exists but is expired and has a refresh token, refresh,
         rewrite ``token_path`` (mode 0600), return.
      3. If ``token_path`` does not exist, run the installed-app flow:
         opens a browser to Google's consent screen, captures the auth code
         on a random localhost port, exchanges for tokens, writes the token
         file with mode 0600, returns.
      4. If the refresh token has been revoked or is otherwise invalid,
         deletes ``token_path`` and raises ``GoogleCalendarError``.

    This function is synchronous — it blocks on browser interaction the
    first time. It is only called from scripts/calendar_smoke.py in 6b,
    and from main.py at startup in 6c. Never from inside an event loop.
    """
    scopes = scopes if scopes is not None else SCOPES

    creds: Credentials | None = None

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_info(
                json.loads(token_path.read_text(encoding="utf-8")),
                scopes,
            )
        except (ValueError, json.JSONDecodeError) as e:
            raise GoogleCalendarError(
                f"token file at {token_path} is corrupt — delete it and re-run "
                f"scripts/calendar_smoke.py to re-authorize"
            ) from e

    if creds is not None and creds.valid:
        return creds

    if creds is not None and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            # Refresh token revoked or otherwise invalid. Remove the stale
            # file so the next run triggers a fresh consent flow.
            try:
                token_path.unlink()
            except FileNotFoundError:
                pass
            raise GoogleCalendarError(
                "refresh token is invalid (likely revoked); re-run "
                "scripts/calendar_smoke.py to re-authorize"
            ) from e
        _write_token(token_path, creds)
        return creds

    # No valid credentials — run the installed-app flow.
    if not client_secrets_path.exists():
        raise GoogleCalendarError(
            f"client secrets file not found at {client_secrets_path}; "
            f"see README 'Google Cloud setup' to create and download one"
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets_path), scopes
    )
    creds = flow.run_local_server(port=0)
    _write_token(token_path, creds)
    return creds


def _write_token(token_path: Path, creds: Credentials) -> None:
    """Write ``creds`` to ``token_path`` with mode 0600."""
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(token_path, 0o600)
    logger.info("wrote Google Calendar token to %s", token_path)
```

- [ ] **Step 2: Write the failing test for chmod 600**

Append to `tests/test_google_calendar.py`:

```python
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
```

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/test_google_calendar.py::test_auth_writes_token_chmod_600 -v`

Expected: `PASSED`.

- [ ] **Step 4: Run the whole file**

Run: `uv run pytest tests/test_google_calendar.py -v`

Expected: ten tests, all `PASSED`.

- [ ] **Step 5: Verify mypy and ruff**

Run: `uv run mypy src/project0/calendar && uv run ruff check src/project0/calendar tests/test_google_calendar.py`

Expected: both clean. If mypy complains about `google.oauth2.credentials.Credentials` missing stubs, add `"google.*"` to `[[tool.mypy.overrides]]` with `ignore_missing_imports = true` in `pyproject.toml`. This is the conventional treatment for Google SDK packages that don't ship type stubs.

If you needed the mypy override, it looks like:

```toml
[[tool.mypy.overrides]]
module = ["google.*", "googleapiclient.*", "google_auth_oauthlib.*"]
ignore_missing_imports = true
```

- [ ] **Step 6: Commit**

```bash
git add src/project0/calendar/auth.py tests/test_google_calendar.py pyproject.toml
git commit -m "feat(calendar): OAuth loopback flow + token load/save

load_or_acquire_credentials runs the installed-app flow on first use,
loads and refreshes on subsequent runs, and cleans up stale token files
when the refresh token is revoked. Token file is written chmod 600.
Mypy overrides added for the Google SDK packages that lack stubs."
```

---

## Task 10: `client.py` — skeleton and `list_events`

**Files:**
- Create: `src/project0/calendar/client.py`
- Modify: `tests/test_google_calendar.py`

- [ ] **Step 1: Write the client skeleton with `list_events`**

Create `src/project0/calendar/client.py`:

```python
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
from googleapiclient.http import HttpRequest

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


def _require_aware(dt: datetime, field_name: str) -> None:
    """Reject naive datetimes at the client boundary."""
    if dt.tzinfo is None:
        raise ValueError(
            f"{field_name} must be a timezone-aware datetime; got a naive value"
        )
```

- [ ] **Step 2: Add a `build_test_client` helper and tests to `tests/test_google_calendar.py`**

Append to `tests/test_google_calendar.py`:

```python
from datetime import timedelta

from googleapiclient.discovery import build as discovery_build
from googleapiclient.http import HttpMockSequence

from project0.calendar.client import GoogleCalendar

# The Calendar discovery document is small enough to ship inside the SDK;
# HttpMockSequence still needs an HTTP layer to serve it. We point the
# first mock request at the SDK's local discovery cache to avoid an
# accidental network call during tests.
#
# Simpler alternative used below: build() with static_discovery=True and
# a pre-baked http mock that serves the discovery doc as its first entry.


def build_test_client(responses: list[tuple[dict[str, str], bytes]]) -> GoogleCalendar:
    """Construct a GoogleCalendar whose SDK service uses HttpMockSequence.

    Each ``(headers, body)`` tuple in ``responses`` is returned by the
    mock HTTP transport in order. The first response is consumed by the
    SDK when fetching the discovery document; subsequent responses match
    the real API calls the test makes.
    """
    from googleapiclient.discovery import build as _build

    # Minimal discovery document: the SDK accepts a static copy.
    # We read the one shipped with google-api-python-client.
    import googleapiclient.discovery_cache as _cache
    from pathlib import Path as _Path
    discovery_path = _Path(_cache.__file__).parent / "documents" / "calendar.v3.json"
    discovery_doc = discovery_path.read_bytes()

    http = HttpMockSequence([
        ({"status": "200"}, discovery_doc),
        *responses,
    ])
    service = _build("calendar", "v3", http=http, developerKey="fake")
    # Sentinel credentials object is not used because we inject _service directly.
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
```

Add `import asyncio` near the top of the test file if it isn't already there.

- [ ] **Step 3: Run the new tests**

Run: `uv run pytest tests/test_google_calendar.py -v -k "list_events_returns or list_events_rejects_naive"`

Expected: both `PASSED`. If the discovery-doc path is wrong for your installed SDK version, the test will fail with `FileNotFoundError` — in that case, run `python -c "import googleapiclient.discovery_cache as c; import pathlib; print(pathlib.Path(c.__file__).parent / 'documents')"` to find the real path and update `build_test_client` accordingly.

- [ ] **Step 4: Run the whole file**

Run: `uv run pytest tests/test_google_calendar.py -v`

Expected: twelve tests, all `PASSED`.

- [ ] **Step 5: Verify mypy and ruff**

Run: `uv run mypy src/project0/calendar && uv run ruff check src/project0/calendar tests/test_google_calendar.py`

Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/project0/calendar/client.py tests/test_google_calendar.py
git commit -m "feat(calendar): GoogleCalendar.list_events via asyncio.to_thread

Async wrapper around google-api-python-client. list_events uses
singleEvents=True and orderBy='startTime', rejects naive datetimes at
the boundary, and translates all results through raw_event_to_model.
Tests use HttpMockSequence so they never hit the network."
```

---

## Task 11: `client.py` — `get_event`

**Files:**
- Modify: `src/project0/calendar/client.py`
- Modify: `tests/test_google_calendar.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_google_calendar.py`:

```python
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
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `uv run pytest tests/test_google_calendar.py::test_get_event_returns_translated_event -v`

Expected: `AttributeError` — `GoogleCalendar` has no `get_event` yet.

- [ ] **Step 3: Add `get_event` to `GoogleCalendar`**

Append to the `GoogleCalendar` class body in `src/project0/calendar/client.py`, below `_sync_list_events`:

```python
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
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `uv run pytest tests/test_google_calendar.py::test_get_event_returns_translated_event -v`

Expected: `PASSED`.

- [ ] **Step 5: Run the whole file**

Run: `uv run pytest tests/test_google_calendar.py -v`

Expected: thirteen tests, all `PASSED`.

- [ ] **Step 6: Commit**

```bash
git add src/project0/calendar/client.py tests/test_google_calendar.py
git commit -m "feat(calendar): GoogleCalendar.get_event

Round-trips through the same translator as list_events. HTTP errors
funnel into GoogleCalendarError with the original status included."
```

---

## Task 12: `client.py` — `create_event`

**Files:**
- Modify: `src/project0/calendar/client.py`
- Modify: `tests/test_google_calendar.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_google_calendar.py`:

```python
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
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `uv run pytest tests/test_google_calendar.py::test_create_event_posts_expected_body -v`

Expected: `AttributeError` — `create_event` not defined.

- [ ] **Step 3: Add `create_event`**

Append to the `GoogleCalendar` class body in `src/project0/calendar/client.py`:

```python
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
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_google_calendar.py::test_create_event_posts_expected_body -v`

Expected: `PASSED`.

- [ ] **Step 5: Run the whole file**

Run: `uv run pytest tests/test_google_calendar.py -v`

Expected: fourteen tests, all `PASSED`.

- [ ] **Step 6: Commit**

```bash
git add src/project0/calendar/client.py tests/test_google_calendar.py
git commit -m "feat(calendar): GoogleCalendar.create_event

Uses model_to_raw to build the insert body, translates the echoed
event back through raw_event_to_model. Rejects naive datetimes."
```

---

## Task 13: `client.py` — `update_event` (partial-update safety)

**Files:**
- Modify: `src/project0/calendar/client.py`
- Modify: `tests/test_google_calendar.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_google_calendar.py`:

```python
def test_update_event_partial_only_patches_provided_fields() -> None:
    # Echo the post-edit event back as Google would.
    edited_body = {
        "kind": "calendar#event",
        "id": "abc123def456",
        "status": "confirmed",
        "htmlLink": "https://example.invalid/abc123def456",
        "summary": "Coffee with prof (edited)",
        "description": "Discuss the April review schedule",
        "location": "Starbucks Nanjing West Rd",
        "start": {"dateTime": "2026-04-15T14:00:00+08:00", "timeZone": "Asia/Shanghai"},
        "end": {"dateTime": "2026-04-15T15:00:00+08:00", "timeZone": "Asia/Shanghai"},
    }
    client = build_test_client([
        ({"status": "200"}, json.dumps(edited_body).encode("utf-8")),
    ])

    async def run() -> CalendarEvent:
        return await client.update_event(
            "abc123def456",
            summary="Coffee with prof (edited)",
        )

    event = asyncio.run(run())
    assert event.summary == "Coffee with prof (edited)"
    # Defensive: calling model_to_raw with only summary=... must produce
    # a single-key body. The unit test on model_to_raw already covers
    # this, but we re-assert it here to lock the client contract.
    from project0.calendar.model import model_to_raw as _m
    assert _m(summary="Coffee with prof (edited)") == {
        "summary": "Coffee with prof (edited)",
    }
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `uv run pytest tests/test_google_calendar.py::test_update_event_partial_only_patches_provided_fields -v`

Expected: `AttributeError` on `update_event`.

- [ ] **Step 3: Add `update_event`**

Append to the `GoogleCalendar` class body in `src/project0/calendar/client.py`:

```python
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
```

Note the use of `events().patch()` rather than `events().update()`. `patch` is the Google Calendar v3 method for partial updates; `update` requires a full event body. Since `model_to_raw` already drops `None` fields, pairing it with `patch` is what makes the "update only the summary without blanking description/location" contract work.

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_google_calendar.py::test_update_event_partial_only_patches_provided_fields -v`

Expected: `PASSED`.

- [ ] **Step 5: Run the whole file**

Run: `uv run pytest tests/test_google_calendar.py -v`

Expected: fifteen tests, all `PASSED`.

- [ ] **Step 6: Commit**

```bash
git add src/project0/calendar/client.py tests/test_google_calendar.py
git commit -m "feat(calendar): GoogleCalendar.update_event via events().patch()

Uses patch() (not update()) paired with model_to_raw's None-dropping
so partial updates never blank existing values on Google's side."
```

---

## Task 14: `client.py` — `delete_event`

**Files:**
- Modify: `src/project0/calendar/client.py`
- Modify: `tests/test_google_calendar.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_google_calendar.py`:

```python
def test_delete_event_returns_none_on_success() -> None:
    # Google returns an empty body with status 204 on successful delete.
    client = build_test_client([({"status": "204"}, b"")])

    async def run() -> None:
        await client.delete_event("abc123def456")

    result = asyncio.run(run())
    assert result is None
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `uv run pytest tests/test_google_calendar.py::test_delete_event_returns_none_on_success -v`

Expected: `AttributeError` on `delete_event`.

- [ ] **Step 3: Add `delete_event`**

Append to the `GoogleCalendar` class body in `src/project0/calendar/client.py`:

```python
    async def delete_event(self, event_id: str) -> None:
        await asyncio.to_thread(self._sync_delete_event, event_id)

    def _sync_delete_event(self, event_id: str) -> None:
        try:
            self._service.events().delete(
                calendarId=self._calendar_id,
                eventId=event_id,
            ).execute()
        except HttpError as e:
            raise GoogleCalendarError(
                f"delete_event({event_id!r}) failed: HTTP {e.resp.status}"
            ) from e
        except Exception as e:  # noqa: BLE001
            raise GoogleCalendarError(
                f"delete_event({event_id!r}) failed: {e}"
            ) from e
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_google_calendar.py::test_delete_event_returns_none_on_success -v`

Expected: `PASSED`.

- [ ] **Step 5: Run the whole file**

Run: `uv run pytest tests/test_google_calendar.py -v`

Expected: sixteen tests, all `PASSED`.

- [ ] **Step 6: Commit**

```bash
git add src/project0/calendar/client.py tests/test_google_calendar.py
git commit -m "feat(calendar): GoogleCalendar.delete_event

Returns None on success; HTTP errors funnel into GoogleCalendarError."
```

---

## Task 15: `client.py` — HTTP error translation

**Files:**
- Modify: `tests/test_google_calendar.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_google_calendar.py`:

```python
def test_http_error_translates_to_GoogleCalendarError() -> None:
    from project0.calendar.errors import GoogleCalendarError

    body = json.dumps(load_fixture("error_404.json")).encode("utf-8")
    client = build_test_client([({"status": "404"}, body)])

    async def run() -> CalendarEvent:
        return await client.get_event("definitely-not-a-real-id-zzz")

    with pytest.raises(GoogleCalendarError, match="404"):
        asyncio.run(run())
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_google_calendar.py::test_http_error_translates_to_GoogleCalendarError -v`

Expected: `PASSED`. The error-translation path was implemented in Task 11 already; this test locks the contract down.

- [ ] **Step 3: Run the whole file**

Run: `uv run pytest tests/test_google_calendar.py -v`

Expected: seventeen tests, all `PASSED`.

- [ ] **Step 4: Verify mypy + ruff + full test suite**

Run: `uv run pytest && uv run mypy src/project0 && uv run ruff check src tests`

Expected: all green. Any regression in an existing test file at this point means a module-level import in `tests/test_google_calendar.py` leaked into collection and broke something — diagnose before proceeding.

- [ ] **Step 5: Commit**

```bash
git add tests/test_google_calendar.py
git commit -m "test(calendar): lock HTTP error → GoogleCalendarError contract

404 from events().get() raises GoogleCalendarError carrying the status."
```

---

## Task 16: `__init__.py` re-exports

**Files:**
- Modify: `src/project0/calendar/__init__.py`

- [ ] **Step 1: Replace the package docstring-only `__init__.py`**

Replace the contents of `src/project0/calendar/__init__.py` with:

```python
"""Google Calendar integration (sub-project 6b).

Public surface: :class:`GoogleCalendar`, :class:`CalendarEvent`,
:class:`GoogleCalendarError`. Everything else in the submodules is an
implementation detail.
"""

from project0.calendar.client import GoogleCalendar
from project0.calendar.errors import GoogleCalendarError
from project0.calendar.model import CalendarEvent

__all__ = ["CalendarEvent", "GoogleCalendar", "GoogleCalendarError"]
```

- [ ] **Step 2: Verify the re-exports are importable**

Run:
```bash
uv run python -c "from project0.calendar import GoogleCalendar, CalendarEvent, GoogleCalendarError; print(GoogleCalendar, CalendarEvent, GoogleCalendarError)"
```

Expected: three class reprs printed without import errors.

- [ ] **Step 3: Run the full test suite and type check**

Run: `uv run pytest && uv run mypy src/project0 && uv run ruff check src tests`

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add src/project0/calendar/__init__.py
git commit -m "feat(calendar): re-export public surface from package

GoogleCalendar, CalendarEvent, GoogleCalendarError are now importable
directly from project0.calendar."
```

---

## Task 17: `scripts/calendar_smoke.py` — manual smoke test

**Files:**
- Create: `scripts/calendar_smoke.py`

- [ ] **Step 1: Write the smoke script**

Create `scripts/calendar_smoke.py`:

```python
#!/usr/bin/env python
"""Manual smoke test for the Google Calendar integration (sub-project 6b).

Run once as part of 6b acceptance (criteria I–K in the spec). Creates a
real event on your real calendar, updates it, reads it, deletes it, and
exits. Optionally dumps raw responses to a fixtures directory so the
unit tests can be backed by real Google JSON.

WARNING: This script hits the real Google Calendar API. It creates an
event at now+1h on the configured GOOGLE_CALENDAR_ID (default primary).
The event is deleted in step 6, but if the script crashes before that
point the atexit handler makes a best-effort cleanup attempt; a network
failure at exit time could leave the test event on your calendar. Delete
it manually via Google Calendar in that case.

Usage:
    uv run python scripts/calendar_smoke.py
    uv run python scripts/calendar_smoke.py --dump-fixtures tests/fixtures/google_calendar/
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from project0.calendar import CalendarEvent, GoogleCalendar, GoogleCalendarError
from project0.calendar.auth import SCOPES, load_or_acquire_credentials

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKEN_PATH = REPO_ROOT / "data" / "google_token.json"
CLIENT_SECRETS_PATH = REPO_ROOT / "data" / "google_client_secrets.json"

# Global state for atexit cleanup.
_created_event_id: str | None = None
_cleanup_client: GoogleCalendar | None = None


def _fatal(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dump-fixtures",
        type=Path,
        default=None,
        help="Also write raw Google responses to this directory (one JSON per step).",
    )
    parser.add_argument(
        "--i-know-this-is-not-primary",
        action="store_true",
        help="Opt-in to running the smoke test against a non-primary calendar.",
    )
    return parser.parse_args()


def _check_env() -> tuple[str, ZoneInfo]:
    """Validate env vars and return (calendar_id, user_tz)."""
    user_tz_name = os.environ.get("USER_TIMEZONE")
    if not user_tz_name:
        _fatal("USER_TIMEZONE is not set. Add it to .env (e.g. Asia/Shanghai).")
    try:
        user_tz = ZoneInfo(user_tz_name)
    except Exception as e:
        _fatal(f"USER_TIMEZONE={user_tz_name!r} is not a valid zoneinfo name: {e}")
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
    return calendar_id, user_tz


def _cleanup_atexit() -> None:
    """Best-effort cleanup if the script exits before step 6."""
    if _created_event_id is None or _cleanup_client is None:
        return
    try:
        asyncio.run(_cleanup_client.delete_event(_created_event_id))
        print(f"[atexit] cleanup deleted {_created_event_id}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(
            f"[atexit] WARNING: could not clean up {_created_event_id}: {e}. "
            f"Delete it manually from Google Calendar.",
            file=sys.stderr,
        )


def _dump(
    fixtures_dir: Path | None,
    name: str,
    body: dict[str, Any],
) -> None:
    if fixtures_dir is None:
        return
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    target = fixtures_dir / name
    target.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"   [dump-fixtures] wrote {target}")


async def main() -> int:
    global _created_event_id, _cleanup_client

    args = _parse_args()
    load_dotenv(REPO_ROOT / ".env")

    calendar_id, user_tz = _check_env()

    if calendar_id != "primary" and not args.i_know_this_is_not_primary:
        _fatal(
            f"GOOGLE_CALENDAR_ID={calendar_id!r} is not 'primary'. "
            f"Pass --i-know-this-is-not-primary if that is intentional."
        )

    if not CLIENT_SECRETS_PATH.exists():
        _fatal(
            f"{CLIENT_SECRETS_PATH} is missing. See README 'Google Cloud setup' "
            f"to create and download one."
        )

    print(f"calendar_smoke.py — calendar={calendar_id} tz={user_tz.key}")
    print(
        "WARNING: this script creates a real event on your real calendar. "
        "It will be deleted in step 6."
    )

    # Step 1: Authorize.
    creds = load_or_acquire_credentials(TOKEN_PATH, CLIENT_SECRETS_PATH, SCOPES)
    print(f"[1/7] authorized, token at {TOKEN_PATH}")

    client = GoogleCalendar(creds, calendar_id, user_tz)
    _cleanup_client = client
    atexit.register(_cleanup_atexit)

    # Step 2: Read upcoming week.
    now = datetime.now(user_tz)
    week_out = now + timedelta(days=7)
    events = await client.list_events(now, week_out)
    print(f"[2/7] found {len(events)} events in next 7 days:")
    for e in events:
        print(f"   {e.start.isoformat()}  {e.summary}  ({e.id})")
    _dump_list_response_if_requested(client, now, week_out, args.dump_fixtures)

    # Step 3: Create.
    create_start = now + timedelta(hours=1)
    create_end = now + timedelta(hours=2)
    created = await client.create_event(
        summary="Project 0 smoke test",
        start=create_start,
        end=create_end,
        description="created by calendar_smoke.py — safe to delete",
    )
    _created_event_id = created.id
    print(f"[3/7] created event {created.id}")
    print(
        "       → open Google Calendar web UI and confirm the event exists, "
        "then press Enter to continue"
    )
    input()

    # Step 4: Update.
    updated = await client.update_event(
        created.id, summary="Project 0 smoke test (edited)"
    )
    print(f"[4/7] updated summary to {updated.summary!r}")
    print("       → refresh Google Calendar and confirm, then press Enter")
    input()

    # Step 5: Read by id.
    fetched = await client.get_event(created.id)
    assert fetched.summary == "Project 0 smoke test (edited)", (
        f"round-trip mismatch: {fetched.summary!r}"
    )
    print(f"[5/7] get_event round-trip verified: {fetched.summary!r}")
    _dump_get_response_if_requested(client, created.id, args.dump_fixtures)

    # Step 6: Delete.
    await client.delete_event(created.id)
    _created_event_id = None  # disarm atexit cleanup
    print("[6/7] deleted")
    print("       → refresh Google Calendar and confirm the event is gone, then press Enter")
    input()

    # Step 7: Error path.
    try:
        await client.get_event("definitely-not-a-real-id-zzz")
    except GoogleCalendarError as e:
        print(f"[7/7] error path verified: {e}")
        _dump_error_response_if_requested(client, args.dump_fixtures)
    else:
        _fatal("step 7 expected GoogleCalendarError but nothing was raised")

    print("smoke test complete — all 7 steps passed")
    return 0


def _dump_list_response_if_requested(
    client: GoogleCalendar,
    time_min: datetime,
    time_max: datetime,
    fixtures_dir: Path | None,
) -> None:
    """Re-issue the raw list call and dump the untranslated response."""
    if fixtures_dir is None:
        return
    # We re-hit the API once to capture the raw dict for fixture seeding.
    # Intentionally uses the private SDK handle.
    raw = (
        client._service.events()  # type: ignore[attr-defined]
        .list(
            calendarId=client._calendar_id,  # type: ignore[attr-defined]
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=1,
        )
        .execute()
    )
    # Decide whether it's an all-day event or a dateTime event based on
    # the first item's start shape and write to the correct filename.
    if raw.get("items"):
        first = raw["items"][0]
        if "date" in first.get("start", {}):
            _dump(fixtures_dir, "list_all_day.json", raw)
        else:
            _dump(fixtures_dir, "list_single_dateTime.json", raw)
    else:
        print("   [dump-fixtures] skipped list fixture: no events in window")


def _dump_get_response_if_requested(
    client: GoogleCalendar, event_id: str, fixtures_dir: Path | None,
) -> None:
    if fixtures_dir is None:
        return
    raw = (
        client._service.events()  # type: ignore[attr-defined]
        .get(
            calendarId=client._calendar_id,  # type: ignore[attr-defined]
            eventId=event_id,
        )
        .execute()
    )
    # Add a synthetic unknown field so the test that exercises the
    # unknown-field warning has real backbone data.
    raw["futureFieldFromGoogle"] = {"someValue": 42}
    _dump(fixtures_dir, "get_with_unknown_field.json", raw)


def _dump_error_response_if_requested(
    client: GoogleCalendar, fixtures_dir: Path | None,
) -> None:
    if fixtures_dir is None:
        return
    # Capture the raw 404 body via the SDK's low-level HTTP handle.
    from googleapiclient.errors import HttpError

    try:
        client._service.events().get(  # type: ignore[attr-defined]
            calendarId=client._calendar_id,  # type: ignore[attr-defined]
            eventId="definitely-not-a-real-id-zzz-fixtures",
        ).execute()
    except HttpError as e:
        body = e.content
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"error": {"code": 404, "message": body.decode("utf-8", "replace")}}
        _dump(fixtures_dir, "error_404.json", parsed)
    else:
        print("   [dump-fixtures] skipped error fixture: expected 404 but got 200")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: Verify the script imports cleanly**

Run: `uv run python -c "import ast; ast.parse(open('scripts/calendar_smoke.py').read())"`

Expected: no output (successful parse).

- [ ] **Step 3: Verify mypy and ruff on the script**

Run: `uv run mypy scripts/calendar_smoke.py && uv run ruff check scripts/calendar_smoke.py`

Expected: clean. If mypy complains about the private-attribute access in the `_dump_*` helpers, the `# type: ignore[attr-defined]` comments should suppress it; if not, add `--ignore-missing-imports` equivalents.

- [ ] **Step 4: Dry-run the argument parser**

Run: `uv run python scripts/calendar_smoke.py --help`

Expected: argparse help text printed, no connection attempts.

- [ ] **Step 5: Commit**

```bash
git add scripts/calendar_smoke.py
git commit -m "feat(scripts): calendar_smoke.py manual smoke test

Seven-step smoke test against real Google Calendar: authorize, read
upcoming week, create, update, round-trip, delete, verify error path.
Optional --dump-fixtures flag captures raw responses to
tests/fixtures/google_calendar/ for seeding unit tests. atexit handler
makes a best-effort cleanup if the script aborts mid-run. Refuses to
run without USER_TIMEZONE set or against a non-primary calendar
without explicit opt-in."
```

---

## Task 18: `.env.example` + README documentation

**Files:**
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Append new env var entries to `.env.example`**

Open `.env.example` and append:

```
# Google Calendar (sub-project 6b)
# Required for scripts/calendar_smoke.py and for Manager in 6c.
# USER_TIMEZONE must be a valid IANA zoneinfo name.
USER_TIMEZONE=Asia/Shanghai

# Which Google Calendar to read and write. Default "primary" is your
# default calendar. Change only if you keep real appointments elsewhere.
GOOGLE_CALENDAR_ID=primary

# Place your OAuth client secrets JSON at data/google_client_secrets.json.
# See README "Google Cloud setup" for the one-time steps to create it.
```

- [ ] **Step 2: Append a "Google Cloud setup" section to `README.md`**

Open `README.md` and append at the end:

```markdown
## Google Cloud setup (sub-project 6b)

The Google Calendar integration needs a personal OAuth client. This is
a one-time setup done through the Google Cloud Console and takes about
five minutes.

1. Go to https://console.cloud.google.com, create a new project called
   `project-0` (or reuse an existing personal project).
2. In the project, open the API Library and enable the **Google Calendar
   API**.
3. Open the OAuth consent screen page:
   - User type: **External**
   - App name: `Project 0`
   - User support email: your own email
   - Scopes: add `https://www.googleapis.com/auth/calendar.events`
   - Test users: add your own Google account
   The app stays in **Testing** mode permanently. This is correct for a
   personal tool — no Google verification review is required and your
   token stays valid.
4. Open the Credentials page and create OAuth 2.0 credentials:
   - Type: **Desktop app**
   - Name: `Project 0 local`
   Download the resulting JSON file.
5. Save the downloaded file as `data/google_client_secrets.json` in this
   repo. `data/` is already gitignored so this file cannot land in the
   repo by accident.

You only do these steps once per personal Google account. After that,
the OAuth token file at `data/google_token.json` is created automatically
the first time you run `scripts/calendar_smoke.py`.

## 6b smoke test

After completing Google Cloud setup above, run:

```bash
uv run python scripts/calendar_smoke.py
```

On first run a browser window opens asking you to authorize Project 0
to access your calendar. Approve, then return to the terminal. The
script walks through seven steps:

1. Authorize (browser flow on first run; silent load on later runs).
2. Read upcoming 7 days — prints a table of your real events.
3. Create a test event 1 hour from now. Pauses for you to confirm
   visually in Google Calendar on any device, then press Enter.
4. Update the test event's title. Pauses again.
5. Round-trip the edit through `get_event` and assert it matches.
6. Delete the test event. Pauses for visual confirmation.
7. Verify that a bogus event ID raises `GoogleCalendarError`.

If the script crashes between steps 3 and 6, an `atexit` handler makes
a best-effort attempt to delete the test event. If the network is down
at exit time, delete it manually in Google Calendar.

To refresh the golden test fixtures after an SDK upgrade or a Google
API change, delete `data/google_token.json` and run:

```bash
uv run python scripts/calendar_smoke.py --dump-fixtures tests/fixtures/google_calendar/
```

Then commit the updated fixtures.

To revoke Project 0's access entirely, visit
https://myaccount.google.com/permissions, find "Project 0" in the list,
and remove its access. Delete `data/google_token.json` locally afterwards.
```

- [ ] **Step 3: Verify the README renders as valid Markdown**

Run: `uv run python -c "import pathlib; print(pathlib.Path('README.md').read_text()[-800:])"`

Expected: the last 800 characters of the README print, containing the new sections. Sanity check that fenced code blocks are balanced by eye.

- [ ] **Step 4: Commit**

```bash
git add .env.example README.md
git commit -m "docs(calendar): Google Cloud setup + 6b smoke test runbook

New .env.example entries for USER_TIMEZONE and GOOGLE_CALENDAR_ID.
README section walking through the one-time Google Cloud Console setup
and the seven-step manual smoke test, including fixture regeneration
and how to revoke access."
```

---

## Task 19: Manual acceptance — run the smoke test and capture real fixtures

**Files:**
- Modify: `tests/fixtures/google_calendar/list_single_dateTime.json`
- Modify: `tests/fixtures/google_calendar/list_all_day.json`
- Modify: `tests/fixtures/google_calendar/get_with_unknown_field.json`
- Modify: `tests/fixtures/google_calendar/error_404.json`

This task is the first one that touches the real Google API. It must be executed by a human (the repo owner) with a real Google account. The agentic executor stops here and hands control back for manual steps.

- [ ] **Step 1: Complete Google Cloud setup**

Follow the README "Google Cloud setup" section. You must finish with a
real `data/google_client_secrets.json` file. Do not commit it — `data/`
is gitignored, but double-check with `git status` to confirm it does
not appear as an untracked file.

- [ ] **Step 2: Ensure `USER_TIMEZONE` is set in `.env`**

Run: `grep USER_TIMEZONE .env || echo "NOT SET"`

If the grep reports `NOT SET`, append `USER_TIMEZONE=Asia/Shanghai` to
`.env` (or your preferred timezone).

- [ ] **Step 3: Run the smoke test for the first time (criterion I)**

Run: `uv run python scripts/calendar_smoke.py`

Walk through all seven steps. For steps 3, 4, and 6, open Google
Calendar in a web browser on any device and confirm the event state
visually before pressing Enter. Expected final line:
`smoke test complete — all 7 steps passed`.

Verify the token file exists and has mode 0600:

```bash
stat -c "%a %n" data/google_token.json
```

Expected: `600 data/google_token.json`.

- [ ] **Step 4: Run the smoke test a second time (criterion J)**

Run: `uv run python scripts/calendar_smoke.py`

Expected: step 1 completes silently (no browser opens) because the
existing token is loaded. All seven steps pass again. This verifies the
token-load path, not just the token-acquire path.

- [ ] **Step 5: Dump real fixtures (criterion K)**

Delete the token file first so the next run exercises the full auth
path again:

```bash
rm data/google_token.json
```

Then run with the dump flag:

```bash
uv run python scripts/calendar_smoke.py --dump-fixtures tests/fixtures/google_calendar/
```

Walk through all seven steps as before. In addition to the normal
output, the `tests/fixtures/google_calendar/` directory now contains
real Google JSON captured during steps 2 (list), 5 (get, with the
synthetic `futureFieldFromGoogle` injected), and 7 (error 404).

Note: whether `list_single_dateTime.json` or `list_all_day.json` gets
overwritten depends on what your first upcoming event actually is. If
only one of the two files has been overwritten, repeat the dump run at
a different time when the other kind of event exists in your calendar
— or edit your calendar temporarily to produce one, then dump again.
Both files must end up with real captured Google data before this task
is considered complete.

- [ ] **Step 6: Re-run the unit tests against the real fixtures**

Run: `uv run pytest tests/test_google_calendar.py -v`

Expected: all tests still pass. If any fail, the real Google response
shape differs from the bootstrap guess in a way the translator doesn't
handle. Fix the translator (real data is ground truth) and re-run.

Common failure modes:
- The real `list_single_dateTime.json` has `"items": []` because your
  calendar window was genuinely empty — re-dump at a time with events.
- The real `get_with_unknown_field.json` is missing `futureFieldFromGoogle`
  because the fixture-dump helper did not inject it — edit the fixture
  file by hand to add the synthetic key back in. This is the only
  hand-edit allowed.
- The real `error_404.json` has additional fields inside the `error`
  dict — this is fine, tests only match the status code.

- [ ] **Step 7: Commit the real fixtures**

```bash
git add tests/fixtures/google_calendar/
git commit -m "test(calendar): replace bootstrap fixtures with real Google data

Captured via scripts/calendar_smoke.py --dump-fixtures against a real
Google Calendar account. These are the canonical test fixtures going
forward — future refreshes go through --dump-fixtures, not hand edits."
```

---

## Task 20: Final verification and self-review

**Files:** none modified

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest`

Expected: all tests pass, zero failures. Record the total count so a
future regression check has a baseline.

- [ ] **Step 2: Run mypy on the whole project**

Run: `uv run mypy src/project0`

Expected: clean. If new mypy errors appeared after adding the `[[tool.mypy.overrides]]` block in Task 9, inspect them — likely a missed `type: ignore` somewhere in `client.py` or `auth.py`.

- [ ] **Step 3: Run ruff on src/, tests/, scripts/**

Run: `uv run ruff check src tests scripts`

Expected: clean.

- [ ] **Step 4: Verify no unintended files were modified**

Run: `git diff --stat $(git log --format=%H --all | tail -1) HEAD -- src/project0/main.py src/project0/config.py src/project0/orchestrator.py src/project0/agents/ src/project0/store.py src/project0/envelope.py src/project0/llm/ src/project0/telegram_io.py src/project0/mentions.py src/project0/errors.py prompts/`

Expected: no output (none of these paths were touched in 6b). If any
show up, something leaked from 6c planning into 6b and must be reverted
or justified.

- [ ] **Step 5: Verify the 6a Secretary still works end-to-end**

Re-run the 6a Telegram smoke test from `docs/superpowers/specs/2026-04-13-secretary-design.md` section 12 (G.1–G.6). The calendar sub-project is additive and should not affect Secretary, but this is the acceptance criterion that "6b does not regress 6a."

Run the Telegram group tests manually — send a few group messages, verify Secretary observes / chimes in / stays silent as configured, `@secretary` a message and verify a reply, DM Secretary's bot and verify a reply, run `scripts/inject_reminder.py` and verify the reminder lands.

Expected: all 6a flows unchanged.

- [ ] **Step 6: Verify the spec acceptance criteria F–K are satisfied**

Walk through section 7 of the spec one checkpoint at a time:

- **F:** `uv run pytest` — confirmed green in Step 1.
- **G:** `uv run mypy src/project0/calendar` — confirmed green in Step 2 (narrowed scope).
- **H:** `uv run ruff check src/project0/calendar scripts/calendar_smoke.py tests/test_google_calendar.py` — run this narrower command explicitly:

  ```bash
  uv run ruff check src/project0/calendar scripts/calendar_smoke.py tests/test_google_calendar.py
  ```

  Expected: clean.
- **I:** manual smoke walk completed in Task 19 Step 3 with mode-0600 token verified.
- **J:** re-run completed in Task 19 Step 4.
- **K:** `--dump-fixtures` run completed in Task 19 Step 5 and fixtures committed in Task 19 Step 7.

If any gate is unsatisfied, return to the relevant task and fix before declaring 6b done.

- [ ] **Step 7: Announce completion**

Report to the user:

> 6b is complete. Spec acceptance criteria F–K all green. The
> `GoogleCalendar` client is ready for 6c (Manager with tool use +
> pulse) to build on. Real fixtures committed.

No final commit for this task — it only verifies.

---

## Self-Review (done while writing this plan)

**Spec coverage:**
- Spec §2 in-scope items — every listed file is created or modified in Tasks 1–18. The `tests/fixtures/google_calendar/` directory is seeded in Task 2 (bootstrap) and refreshed with real data in Task 19. `.env.example` and `README.md` are updated in Task 18. `pyproject.toml` new deps in Task 1.
- Spec §2 out-of-scope — no task touches `main.py`, `config.py`, the orchestrator, the registry, existing agents, the `messages` table, `Envelope`, `AgentResult`, LLM tool use, or the pulse primitive. Task 20 Step 4 verifies this explicitly.
- Spec §3 locked-in decisions — each decision is encoded in the relevant task's code. Defensive `.get()` translator reads (Task 5), rate-limited unknown-field warnings (Task 5, Task 7), chmod 600 (Task 9), `singleEvents=True` (Task 10), `_require_aware` programmer-error raise (Tasks 8, 10–13), OAuth scope pinned to `calendar.events` (Task 9), `google-api-python-client` sync + `asyncio.to_thread` (Tasks 10–14).
- Spec §4 module internals — every field, method, and helper in the spec is represented: `GoogleCalendarError` (Task 3), `CalendarEvent` dataclass (Task 4), `raw_event_to_model` + `_parse_endpoint` + `_warn_unknown_fields` (Task 5), `model_to_raw` + `_require_aware` (Task 8), `load_or_acquire_credentials` + `_write_token` (Task 9), `GoogleCalendar` with five methods plus `_require_aware` boundary check (Tasks 10–14).
- Spec §5 testing strategy — nine unit tests + one auth chmod test land across Tasks 5–15: dateTime translate, empty list, all-day, source-tz conversion, unknown-field warning + ignorable-keys, full `model_to_raw`, partial `model_to_raw`, naive-datetime rejection, `list_events`, `get_event`, `create_event`, `update_event` partial patch, `delete_event`, HTTP error translate. (The plan ends up with ~17 tests, more than the spec's 9 — the extras are additional coverage tests on `model_to_raw` which the spec didn't enumerate but which the translator clearly needs.)
- Spec §6 smoke script — Task 17 implements all seven steps, both `--dump-fixtures` and `--i-know-this-is-not-primary` flags, env var validation, atexit cleanup. Task 18 documents it in README. Task 19 runs it.
- Spec §7 acceptance criteria F–K — mapped one-to-one in Task 20 Step 6.

No spec sections left unaddressed.

**Placeholder scan:** no TBD / TODO / "implement later" / placeholder code blocks. Every step that changes code shows the exact code to write. File paths are absolute within the repo. All grep / pytest / uv commands include expected output.

**Type consistency:**
- `raw_event_to_model(raw: dict[str, Any], user_tz: ZoneInfo) -> CalendarEvent` — consistent across Tasks 4, 5, 6, 7 (tests) and 10, 11, 12, 13 (client calls).
- `model_to_raw(*, summary=..., start=..., end=..., description=..., location=...) -> dict[str, Any]` — consistent in Task 4 (stub), Task 8 (impl), Tasks 12–13 (callers).
- `GoogleCalendar.__init__(self, credentials, calendar_id, user_tz, *, _service=None)` — consistent in Task 10 (introduction) and `build_test_client` helper.
- `GoogleCalendarError` raised from the same broad-catch pattern in every `_sync_*` method in Tasks 10–14.
- `load_or_acquire_credentials(token_path, client_secrets_path, scopes=None) -> Credentials` — consistent between Task 9 impl and Task 17 smoke script call site.

No naming or signature drift found.

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-04-14-google-calendar-integration.md`.
