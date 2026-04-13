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

from project0.calendar import GoogleCalendar, GoogleCalendarError
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
        raise SystemExit(1)  # unreachable; satisfies type narrowing
    try:
        user_tz = ZoneInfo(user_tz_name)
    except Exception as e:
        _fatal(f"USER_TIMEZONE={user_tz_name!r} is not a valid zoneinfo name: {e}")
        raise SystemExit(1) from e  # unreachable; satisfies type narrowing
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
    raw = (
        client._service.events()
        .list(
            calendarId=client._calendar_id,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=1,
        )
        .execute()
    )
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
        client._service.events()
        .get(
            calendarId=client._calendar_id,
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
    from googleapiclient.errors import HttpError

    try:
        client._service.events().get(
            calendarId=client._calendar_id,
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
