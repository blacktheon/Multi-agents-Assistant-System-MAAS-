# Sub-Project 6b — Google Calendar Integration (Standalone)

**Date:** 2026-04-14
**Parent project:** Project 0: Multi-agent assistant system
**Predecessor sub-project:** 6a — Secretary (`2026-04-13-secretary-design.md`)
**Successor sub-project:** 6c — Manager as real LLM agent with tool use + pulse
**Sub-project scope:** Land the Google Calendar substrate — OAuth, a tested `GoogleCalendar` client module, a typed `CalendarEvent` dataclass, and a manually-verified end-to-end path from Project 0's code to a real Google account — in isolation. No Manager changes, no LLM tool use, no pulse primitive, no wiring into the running process. Those all land in 6c on top of the client built here.

---

## 1. Purpose and Framing

The final Project 0 product needs Manager to read and write calendar events so it can record appointments, check for upcoming ones, and (via a pulse loop in 6c) proactively ask Secretary to deliver warm reminders. Manager talks to Google Calendar directly — **Google Calendar is the only source of truth for appointments in Project 0**. There is no local calendar table, no sync layer, no reconciliation. The user accepts the corresponding cost: if Google is unreachable or the token is revoked, Manager cannot save or read appointments and must surface the failure; the user will view and edit the calendar through other apps in that case.

6b lands the substrate Manager will sit on: OAuth 2.0 loopback flow, token storage, a `GoogleCalendar` client class, the `CalendarEvent` dataclass and translator, and a manual smoke script that proves end-to-end reachability against a real account. 6b deliberately stops before introducing tool use, the pulse primitive, or the Manager agent itself — those all land in 6c, built on a client that 6b has already proven to work.

### Design stance

6b is a *substrate* sub-project. Its job is to isolate one new external-API seam (OAuth + the Google Calendar v3 REST API via the official SDK) so that when 6c introduces the next seam (LLM tool use), any bug is clearly either in the new seam or the old one, never entangled. This mirrors 6a's discipline of isolating one new seam at a time.

6b's diff is almost entirely additive. No existing file under `src/project0/` is modified except `pyproject.toml` (new pinned dependencies) and `.env.example` (new `USER_TIMEZONE` and `GOOGLE_CALENDAR_ID` entries). `main.py`, `config.py`, the orchestrator, the registry, all agents, the database schema, and all existing tests are untouched. Secretary from 6a continues to work exactly as it does now. `manager_stub` keeps stubbing. The running process does not construct a `GoogleCalendar` instance at all in 6b — only the smoke script does. This is deliberate: it preserves the existing dev loop (fresh clones boot without any Google credentials) and keeps the 6b PR easy to review. Startup wiring lands in 6c, the sub-project that actually needs it.

---

## 2. Scope and Non-Goals

### In scope

- A new package `src/project0/calendar/` containing `auth.py`, `client.py`, `model.py`, `errors.py`, and `__init__.py` re-exporting the public surface.
- `scripts/calendar_smoke.py` — the seven-step manual smoke test script, with a `--dump-fixtures` flag that additionally writes raw Google responses to disk for fixture seeding.
- `tests/test_google_calendar.py` — nine unit tests plus a small `auth.py` tempdir/chmod test, all using `googleapiclient.http.HttpMockSequence`, all offline and deterministic.
- `tests/fixtures/google_calendar/` — four golden JSON fixtures captured from real Google responses via the `--dump-fixtures` flag.
- Pinned dependency additions in `pyproject.toml`: `google-api-python-client`, `google-auth`, `google-auth-oauthlib`.
- New `.env.example` entries: `USER_TIMEZONE=Asia/Shanghai`, `GOOGLE_CALENDAR_ID=primary`, with a comment pointing at the README section on one-time Google Cloud Console setup.
- `.gitignore` additions for `data/google_client_secrets.json`, `data/google_token.json`, and `tests/fixtures/google_calendar/*.local.json`.
- README updates: a new "Google Cloud setup" section documenting the one-time Cloud Console clickwork; a new "6b smoke test" section documenting how to run `scripts/calendar_smoke.py`, what each of the seven steps does, and how to revoke the token in Google's account UI.

### Out of scope (deferred to 6c or later)

- **No Manager agent work.** `manager_stub` stays. Manager as a real LLM agent is 6c.
- **No LLM tool use.** No `complete_with_tools` method on `LLMProvider`. That's 6c.
- **No pulse primitive.** No background periodic task in the orchestrator. That's 6c.
- **No Manager persona or config files** (`prompts/manager.md`, `prompts/manager.toml`). That's 6c.
- **No wiring into `main.py` or `config.py` for the running process.** Only `scripts/calendar_smoke.py` constructs a `GoogleCalendar` instance in 6b. Startup validation of the token file lands in 6c.
- **No local cache, no local `appointments` table, no dual-backend protocol.** Google Calendar is the only source of truth. This is locked by memory (`project_calendar_backend.md`) and is not reopened in 6b.
- **No multi-calendar support.** Reads and writes target the single `GOOGLE_CALENDAR_ID` from config (default `primary`).
- **No free/busy queries, `calendars.list`, attendee management, recurring-event series editing, attachments, event-color API, or push notifications / webhooks.** If a future sub-project needs one of these, it adds a method to the existing client surface rather than a parallel client.
- **No credentials encryption beyond "chmod 600 plain JSON file in gitignored `data/`."** OS keyring and encrypted-at-rest are deferred until the threat model changes (e.g., Project 0 running on a shared host).
- **No retries, backoff, or circuit breakers on Google API calls.** Errors raise `GoogleCalendarError` and callers decide. Robustness lands in a later cross-cutting sub-project alongside Anthropic retries.
- **No timezone hot-reload.** Changing `USER_TIMEZONE` requires a process restart, matching every other configuration knob in Project 0.
- **Flagged as postponed for future sub-projects (not 6b, not 6c):** (a) calendar-aware slot suggestion and priority-job list, (b) contacts/CRM with stale-connection reminders. Both are real user-requested future work, captured here so they are not forgotten.

### What does not change from 6a

- `Envelope` schema (including the 6a additions: `payload` field, `listener_observation` routing reason).
- `AgentResult` shape.
- `messages` and `agent_memory` table schemas.
- The orchestrator's routing pipeline (focus dispatch + listener fan-out).
- The `LLMProvider` protocol and its `AnthropicProvider` / `FakeProvider` implementations.
- Secretary — all four paths (listener, mention, DM, Manager-directed reminder) continue to work unchanged. The reminder path is still exercised via `scripts/inject_reminder.py`.
- `manager_stub`, `intelligence_stub`.
- Single-process, single-SQLite-file, single-shared-connection model.
- Allow-list enforcement and content-based dedup across multi-bot Telegram groups.

---

## 3. Architectural Decisions (Settled in Brainstorming)

Captured up front so the implementation plan does not re-open them.

1. **Google Calendar is the only source of truth.** No local `appointments` table, no sync layer. The user accepts that Google-unreachable means Manager cannot read or write appointments.
2. **Sub-project ordering:** 6b = Google Calendar integration standalone; 6c = Manager with tool use + pulse. 6b was reframed from "Manager" to "GCal substrate" to keep one external seam isolated from the tool-use seam.
3. **OAuth flow:** OAuth 2.0 Installed Application flow with loopback redirect (`google_auth_oauthlib.flow.InstalledAppFlow.run_local_server`). Not device flow, not service account.
4. **Token storage:** plain JSON file at `data/google_token.json`, chmod 600 on write, gitignored. No keyring, no custom encryption. Threat model is "personal assistant on the user's own desktop."
5. **Calendar selection:** single calendar, ID from `GOOGLE_CALENDAR_ID` env var, default `primary`. No multi-calendar reads, no aggregation.
6. **SDK:** `google-api-python-client` (sync) wrapped with `asyncio.to_thread` in the client methods. Official library, widest support. Not `aiogoogle`, not hand-rolled `httpx`.
7. **Event model:** project-local `CalendarEvent` dataclass, frozen, with a translator layer that maps raw Google event dicts in both directions. Translator uses defensive reads and an explicit ignorable-field set; unknown top-level fields log a rate-limited warning (once per unknown key per process).
8. **Timezone:** single `USER_TIMEZONE` config (default `Asia/Shanghai`), validated with `zoneinfo.ZoneInfo` at startup, applied to every `CalendarEvent` on read and to every datetime serialized on write. All datetimes at the client boundary are timezone-aware; naive datetimes raise `ValueError` (programmer error, not `GoogleCalendarError`). No timezone hot-reload.
9. **OAuth scope:** `https://www.googleapis.com/auth/calendar.events` only. Least privilege that supports read + write of events on any calendar the user has access to, without allowing calendar-level creation/deletion.
10. **Testing:** `googleapiclient.http.HttpMockSequence` for all unit tests. Golden JSON fixtures captured from real Google responses via `scripts/calendar_smoke.py --dump-fixtures`. Tests never hit real Google; the smoke script always does. Auth loopback flow is exercised only by the smoke script.
11. **`singleEvents=True` as the default on `list_events`.** Recurring events are expanded to instances, which is what every current use case wants. Recurring-series editing is explicitly out of scope.
12. **Integration surface in 6b:** additive only. No changes to `main.py`, `config.py`, orchestrator, registry, existing agents. The smoke script is the only code path that constructs a `GoogleCalendar`.

---

## 4. Module Internals

Four small files under `src/project0/calendar/`, plus a re-exporting `__init__.py`.

### `errors.py`

```python
class GoogleCalendarError(Exception):
    """Raised by the GoogleCalendar client on any failure.

    Wraps the underlying exception (googleapiclient.errors.HttpError,
    auth failure, network error, etc.) via exception chaining. Callers
    catch this single type. Mirrors LLMProviderError from 6a.
    """
```

One class, no subclasses. 6c's Manager tool layer will catch `GoogleCalendarError` at the tool boundary and surface a human-readable failure string back to the LLM as the tool result.

### `model.py`

```python
@dataclass(frozen=True)
class CalendarEvent:
    id: str                    # Google's opaque event id
    summary: str               # event title; "" if Google returned no summary
    start: datetime            # timezone-aware, in USER_TIMEZONE
    end: datetime              # timezone-aware, in USER_TIMEZONE
    all_day: bool              # True if Google returned {"date": ...}
    description: str | None
    location: str | None
    html_link: str             # "" if missing
```

Frozen so instances are hashable and safe to pass around. All datetimes are timezone-aware and converted into `USER_TIMEZONE` at the translator boundary — no naive datetimes ever leave `model.py`.

**Translators:**

```python
def raw_event_to_model(raw: dict, user_tz: ZoneInfo) -> CalendarEvent: ...
def model_to_raw(event: CalendarEvent) -> dict: ...
```

Rules:

- **Defensive reads only.** `raw.get("summary", "")`, `raw.get("description")`, etc. Never bracket-indexing that would `KeyError` on a missing field.
- **All-day normalization.** Raw events with `{"date": "YYYY-MM-DD"}` become `datetime`s at midnight-in-`user_tz` with `all_day=True`.
- **Timezone conversion.** Raw events with `{"dateTime": "...", "timeZone": "..."}` get parsed with their source tz, then converted to `user_tz` before landing in `CalendarEvent`. Same instant, user's wall clock.
- **Unknown-field warning.** After reading all fields the translator consumes, compute `set(raw.keys()) - consumed_keys - known_ignorable_keys`. For each leftover key, emit a single warning `"unknown GCal event field: <key>"` via the standard logger, rate-limited to once per key per process lifetime using a module-level `set`. Ignorable keys (explicit and documented): `kind`, `etag`, `status`, `htmlLink`, `created`, `updated`, `creator`, `organizer`, `iCalUID`, `sequence`, `reminders`, `eventType`, `hangoutLink`, `conferenceData`, `attachments`, `attendees`, `recurrence`, `recurringEventId`, `originalStartTime`, `guestsCanInviteOthers`, `guestsCanModify`, `guestsCanSeeOtherGuests`, `privateCopy`, `locked`, `source`, `colorId`, `transparency`, `visibility`.
- **`model_to_raw` is defensive on writes too.** Only fields the caller provided are emitted; `None` fields are omitted entirely (not serialized as JSON null) so partial updates don't blank out existing values on Google's side.

### `auth.py`

```python
def load_or_acquire_credentials(
    token_path: Path,
    client_secrets_path: Path,
    scopes: list[str],
) -> Credentials: ...
```

Behavior:

1. **Token file exists and valid:** load, refresh if expired (via the refresh token), rewrite `token_path` with mode 0600, return.
2. **Token file missing:** run `InstalledAppFlow.from_client_secrets_file(client_secrets_path, scopes).run_local_server(port=0)`. This opens the default browser, redirects to `http://localhost:<random-port>/`, captures the auth code, exchanges for access + refresh tokens. Write `token_path` with mode 0600 via `os.chmod(token_path, 0o600)` *immediately* after write. Return the credentials.
3. **Refresh token revoked or invalid:** delete `token_path`, raise `GoogleCalendarError` with message instructing the user to re-run `scripts/calendar_smoke.py` to re-authorize.

This function is synchronous; the loopback flow blocks on user interaction. It is called only from `scripts/calendar_smoke.py` in 6b and from `main.py`'s startup in 6c. It is never called from inside an event loop.

The `scopes` argument is always `["https://www.googleapis.com/auth/calendar.events"]` at every call site in 6b and 6c. The argument exists only so tests can pass an empty list to exercise edge cases.

### `client.py`

```python
class GoogleCalendar:
    def __init__(
        self,
        credentials: Credentials,
        calendar_id: str,
        user_tz: ZoneInfo,
    ):
        self._service = build(
            "calendar", "v3", credentials=credentials, cache_discovery=False,
        )
        self._calendar_id = calendar_id
        self._user_tz = user_tz

    async def list_events(
        self,
        time_min: datetime,
        time_max: datetime,
        max_results: int = 250,
    ) -> list[CalendarEvent]: ...

    async def get_event(self, event_id: str) -> CalendarEvent: ...

    async def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: str | None = None,
        location: str | None = None,
    ) -> CalendarEvent: ...

    async def update_event(
        self,
        event_id: str,
        *,
        summary: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> CalendarEvent: ...

    async def delete_event(self, event_id: str) -> None: ...
```

Implementation rules applied to all five methods:

1. Each async method body is `return await asyncio.to_thread(self._sync_<op>, ...)`. The `_sync_*` helpers are plain synchronous functions that build the SDK call chain, call `.execute()`, and translate.
2. Each `_sync_*` helper wraps its SDK call in a single `try/except Exception as e: raise GoogleCalendarError(...) from e`. The catch is deliberately broad because the SDK raises a zoo of exception types (`HttpError`, `TransportError`, `RefreshError`, `httplib2.HttpLib2Error`, socket errors) and funneling them into one type is the contract callers rely on.
3. All `datetime` inputs are required to be timezone-aware. Each method asserts `dt.tzinfo is not None` on entry and raises `ValueError` (programmer error, not `GoogleCalendarError`) on violation. This prevents the most common class of calendar bug: "naive datetime assumed to be in some timezone that turned out not to be what you thought."
4. Datetimes are serialized to RFC3339 with explicit offset on the wire, so Google always receives an unambiguous timezone. On read, the translator converts whatever Google returns into `user_tz`.
5. `list_events` sets `singleEvents=True` and `orderBy="startTime"` on the SDK call. This is the silent default: the client never returns a recurring-event master, only expanded instances.
6. `list_events` does not paginate. If Google returns a `nextPageToken`, the client logs `"list_events truncated at max_results={n}"` and returns the first page. Manager's 1-hour-ahead pulse query in 6c will never hit this. Pagination is added when a caller actually needs it.

---

## 5. Testing Strategy

### Fixture setup

Four golden fixture files under `tests/fixtures/google_calendar/`, captured from real Google responses and committed:

- `list_single_dateTime.json` — an `events.list` response containing one normal (dateTime) event with `description` and `location` populated.
- `list_all_day.json` — an `events.list` response containing one all-day event (`{"date": "..."}`).
- `get_with_unknown_field.json` — an `events.get` response that includes a synthetic top-level field `futureFieldFromGoogle` not in the ignorable set.
- `error_404.json` — the body Google returns for "event not found," used to verify error translation.

Fixtures are captured by running `scripts/calendar_smoke.py --dump-fixtures tests/fixtures/google_calendar/` against a real Google account. This is the canonical refresh procedure; hand-editing fixtures is not allowed.

### Unit tests (`tests/test_google_calendar.py`)

Nine tests plus one auth tempdir/chmod test. All use `googleapiclient.http.HttpMockSequence`. All offline and deterministic. Total test runtime budget: < 300ms for the whole file.

1. **`test_list_events_dateTime_translates_to_user_tz`** — feed `list_single_dateTime.json`, assert the returned `CalendarEvent` has timezone-aware `start`/`end` in `Asia/Shanghai`, `all_day=False`, correct summary/description/location.
2. **`test_list_events_empty_result`** — feed `{"items": []}`, assert `list_events` returns `[]` without error.
3. **`test_list_events_all_day_normalization`** — feed `list_all_day.json`, assert `all_day=True` and `start`/`end` are midnight in `Asia/Shanghai` (not UTC, not naive).
4. **`test_list_events_source_timezone_conversion`** — feed an event whose source tz is `America/Los_Angeles`, assert the returned datetime has been converted into `Asia/Shanghai` (same instant, different wall clock).
5. **`test_create_event_request_body`** — call `client.create_event(...)`, inspect the request body the mock sequence received, assert summary, start/end as RFC3339-with-offset, description, location are present and no null fields are emitted for omitted kwargs.
6. **`test_update_event_partial`** — call `client.update_event(event_id, summary="new")`, assert the PATCH body contains only `summary` and does not contain null-valued `description`/`location`/`start`/`end`.
7. **`test_get_event_round_trip`** — feed `list_single_dateTime.json`'s first item as a `get` response, assert the `CalendarEvent` matches what `list_events` would produce for the same raw event.
8. **`test_unknown_field_logs_warning_once`** — feed `get_with_unknown_field.json`, capture logs via `caplog`, assert exactly one warning `"unknown GCal event field: futureFieldFromGoogle"` is emitted. Call the translator a second time on the same input and assert the warning is *not* re-emitted. A pytest fixture clears the module-level "seen" set before each test.
9. **`test_http_error_translates_to_GoogleCalendarError`** — feed a 404 response, call `client.get_event("bogus")`, assert it raises `GoogleCalendarError` whose `__cause__` is the underlying `HttpError` and whose message includes the original status code.
10. **`test_auth_writes_token_chmod_600`** — write a fake token file to a `tmp_path`, invoke the token-save path in `auth.py`, assert `stat` reports mode `0o600`.

### What the tests don't cover, and why

- **The OAuth loopback flow.** No meaningful unit test exists — the browser dance involves Google's real endpoints and the user's real consent. Integration coverage lives entirely in the smoke script, which is an explicit 6b acceptance criterion.
- **Token refresh against Google's OAuth endpoint.** The SDK handles it; a unit test here would be mocking Google's OAuth response with hand-rolled data, which proves nothing. The smoke script's second run (criterion J in section 7) is the real test: it exercises token load and, eventually, token refresh when the access token has aged out.
- **Pagination past 250 events.** 6b doesn't paginate, so there's nothing to test. The overflow warning is tested inline in the list tests if trivial.
- **Concurrent client calls.** Nothing in 6b calls the client concurrently. 6c's Manager will do one call at a time inside its tool loop. Adding a concurrency test in 6b would be testing nothing we actually rely on.

---

## 6. Smoke Script and One-Time Google Cloud Console Setup

### One-time Google Cloud Console setup (README documents)

1. Go to `console.cloud.google.com`, create a new project called `project-0` (or reuse an existing personal project).
2. In the project, enable the **Google Calendar API** from the API Library.
3. Configure the OAuth consent screen: user type = **External**, app name = `Project 0`, support email = your own email. Add `https://www.googleapis.com/auth/calendar.events` to the scopes list. Add your own Google account to the **Test users** list — this keeps the app in "Testing" mode indefinitely, which is correct for a personal tool (no Google verification review required, token stays valid).
4. Create OAuth 2.0 credentials: type = **Desktop app**, name = `Project 0 local`. Download the resulting JSON file.
5. Save the downloaded file as `data/google_client_secrets.json` in the repo. Confirm it is gitignored (6b adds the entry if missing).

The README explains briefly *why* these steps exist: we are creating a personal OAuth client that only the user can use, in Testing mode, so Google does not require app verification.

### `scripts/calendar_smoke.py`

Command-line interface:

```
uv run python scripts/calendar_smoke.py
uv run python scripts/calendar_smoke.py --dump-fixtures tests/fixtures/google_calendar/
```

Without `--dump-fixtures`, the script runs all seven steps against the real calendar. With the flag, each step additionally writes the raw Google response JSON to the target directory with predictable filenames, for seeding test fixtures.

Preconditions checked at startup:

- `USER_TIMEZONE` env var is set and parseable by `zoneinfo.ZoneInfo` — else fatal error with a clear message.
- `GOOGLE_CALENDAR_ID` env var defaults to `primary` with an info log. If it is set to anything other than `primary`, the script refuses to run unless `--i-know-this-is-not-primary` is also passed (defensive guard against accidentally writing test events to a shared work calendar).
- `data/google_client_secrets.json` exists — else fatal error pointing at the README setup section.

### The seven steps

1. **Authorize.** Call `load_or_acquire_credentials(...)`. First run opens a browser; subsequent runs load the existing token silently. Print `[1/7] authorized, token saved to data/google_token.json`.
2. **Read upcoming week.** `events = await client.list_events(now, now + 7 days)`. Print `[2/7] found {len(events)} events in next 7 days:` followed by a formatted table (start in user tz, summary, id). This is the "real read path works" checkpoint.
3. **Create.** `created = await client.create_event(summary="Project 0 smoke test", start=now+1h, end=now+2h, description="created by calendar_smoke.py — safe to delete")`. Print `[3/7] created event {created.id} — verify in Google Calendar web UI now, then press Enter`. Script waits for Enter; user opens Google Calendar on any device and confirms visually.
4. **Update.** `updated = await client.update_event(created.id, summary="Project 0 smoke test (edited)")`. Print `[4/7] updated summary — refresh Google Calendar and verify, then press Enter`. Wait for Enter.
5. **Read by ID.** `fetched = await client.get_event(created.id)`. Assert `fetched.summary == "Project 0 smoke test (edited)"`. Print `[5/7] get_event returned the edited summary — round-trip verified`.
6. **Delete.** `await client.delete_event(created.id)`. Print `[6/7] deleted — refresh Google Calendar and verify the event is gone, then press Enter`. Wait for Enter.
7. **Error path.** Call `client.get_event("definitely-not-a-real-id-zzz")` inside `try`. Catch `GoogleCalendarError` and print `[7/7] error path verified: {e}`. Print final summary line and exit 0.

### Safety notes

- The event created in step 3 lands on the configured calendar (default `primary`) at `now+1h` to `now+2h`, on the user's real calendar, until step 6 deletes it. The script prints a warning banner at startup about this.
- The script installs an `atexit` handler that attempts `delete_event(created.id)` if the script exits before step 6 completes. Best-effort cleanup — if the network is down at exit time, the cleanup itself fails and the event must be deleted manually in Google Calendar.
- The script is never run in CI and never run by `uv run pytest`. It is a manual tool, executed once during 6b acceptance and subsequently whenever the SDK dependency pin is upgraded or the user suspects something is wrong.

---

## 7. Acceptance Criteria

The 6a criteria A–E (pytest, mypy, ruff, manual Telegram smoke, messages-table inspection) still apply — 6b must not regress any of them, and 6b barely touches the running system so they should remain trivially green. 6b adds:

- **F.** `uv run pytest` passes, including all new tests in `test_google_calendar.py`. Zero failures. The new file adds < 300ms to total suite runtime.
- **G.** `uv run mypy src/project0/calendar` is clean. The new module is fully typed.
- **H.** `uv run ruff check src/project0/calendar scripts/calendar_smoke.py tests/test_google_calendar.py` is clean.
- **I.** Manual smoke: `uv run python scripts/calendar_smoke.py` completes all seven steps against a real Google Calendar with a real `data/google_client_secrets.json`.
  - **I.1.** Step 1 opens a browser, user approves, token written to `data/google_token.json` with mode 0600 (verify with `stat -c %a data/google_token.json` → `600`).
  - **I.2.** Step 2 prints real upcoming events in `Asia/Shanghai` wall-clock time, matching Google Calendar web UI.
  - **I.3.** Step 3 creates a test event; user visually confirms it in the web UI on a separate device.
  - **I.4.** Step 4 updates the summary; user visually confirms the new title.
  - **I.5.** Step 5 prints the edited summary round-tripped through `get_event`.
  - **I.6.** Step 6 deletes the event; user visually confirms it is gone.
  - **I.7.** Step 7 prints a `GoogleCalendarError` for the bogus event id.
- **J.** Re-run `scripts/calendar_smoke.py` a second time without deleting `data/google_token.json`. Step 1 completes silently (no browser) by loading the existing token. This verifies the token-load path, not just the token-acquire path.
- **K.** Delete `data/google_token.json` by hand, then run the smoke script a third time with `--dump-fixtures tests/fixtures/google_calendar/`. The four golden JSON files are written to disk. Verify they parse as valid JSON and match the schema the unit tests expect. Commit the four fixture files.

If any of F–K fails, 6b is not done.

---

## 8. Decisions Worth Flagging for Future Sub-Projects

1. **Google Calendar is the only source of truth for appointments, project-wide.** No local cache, no sync. Locked by memory (`project_calendar_backend.md`). Do not reopen without explicit user reason. Future sub-projects that want calendar data read it live through the client built in 6b.
2. **The `GoogleCalendar` client surface is deliberately small — 5 methods and 1 dataclass.** When future sub-projects need more (recurring-series editing, multi-calendar reads, free/busy, attendees), they add methods to the existing client, not a second client. Keep the surface narrow by default; grow it only on actual need.
3. **`singleEvents=True` is the default on reads.** Recurring-event instances are what every current use case wants. Adding recurring-series editing means a new variant, not flipping the default.
4. **All datetimes at the client boundary are timezone-aware in `USER_TIMEZONE`.** Naive datetimes are programmer error and raise `ValueError`. Future agents inherit this discipline: Manager in 6c uses `datetime.now(USER_TZ)`, never `datetime.now()`.
5. **OAuth consent scope is `calendar.events`, not full `calendar`.** Future sub-projects must not casually broaden it. If a feature genuinely needs a broader scope, that is a re-consent flow the user has to approve — flag loudly in the next brainstorm.
6. **The unknown-field warning is the early-warning system for Google API drift.** When you see it in logs, take it seriously: either add the field to the ignorable set (if irrelevant) or consume it properly (if relevant). Do not silence it without understanding.
7. **The smoke script's `--dump-fixtures` flag is the canonical way to refresh golden fixtures.** When a test breaks after an SDK upgrade, regenerate via the flag, diff against committed versions, understand the change, commit. Never hand-edit fixtures.
8. **Token storage:** plain JSON + chmod 600 + gitignored. Threat model is "personal desktop." If Project 0 ever runs on a shared host, revisit (OS keyring or encrypted-at-rest).
9. **6b does not wire GCal into `main.py`.** 6c is the sub-project that adds startup validation of the token file. Until then, `uv run python -m project0` works on a fresh clone without any Google credentials. This is intentional — keeps 6b additive and the dev loop uninterrupted.

---

## 9. What Comes After

**6c — Manager as real LLM agent with tool use + pulse.** Adds `complete_with_tools` to the `LLMProvider` protocol. Replaces `manager_stub` with a real Manager class. Introduces the pulse primitive in the orchestrator — a generic periodic `pulse()` dispatcher, parallel to 6a's listener fan-out, that gives every agent an optional hook to run background work on its own schedule. Wires `GoogleCalendar` into `main.py`'s composition root with startup token validation. Manager's tools in 6c are thin wrappers over the 6b client: `save_appointment`, `list_appointments`, `update_appointment`, `delete_appointment`, plus `delegate_to_agent` for routing. Manager's pulse reads `list_events(now, now + 1h)` on a 30-minute interval and, for any event crossing the reminder threshold, synthesizes a `manager_delegation` envelope to Secretary with the `payload={"kind": "reminder_request", ...}` shape Secretary already handles from 6a. 6c also lands the Chinese `prompts/manager.md` persona file and the `prompts/manager.toml` config file, following the 6a convention.

**Explicitly postponed, not planned yet** (captured from user input during the 6b brainstorm):
- Calendar-aware slot suggestion and priority-job list (Manager reads the calendar, finds free windows, and proposes when to schedule new tasks; Manager maintains a prioritized job list and nudges the user on ordering).
- Contacts / CRM with stale-connection reminders (Manager tracks friends and professional contacts, notes who the user met at each appointment, reminds the user of contacts not spoken to in a long time).

Both are real future work, likely becoming their own sub-projects after 6c and after the Intelligence / Learning / Supervisor sub-projects land.

**6d onward follows the original roadmap:** Intelligence (external data), Learning, Supervisor, memory-layer hardening, WebUI, tool-gateway hardening. Each gets its own brainstorm → spec → plan → implementation cycle.
