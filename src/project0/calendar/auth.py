"""OAuth 2.0 installed-app flow + token load/save for Google Calendar."""

from __future__ import annotations

import contextlib
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
            creds = Credentials.from_authorized_user_info(  # type: ignore[no-untyped-call]
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
            with contextlib.suppress(FileNotFoundError):
                token_path.unlink()
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
    token_path.write_text(creds.to_json(), encoding="utf-8")  # type: ignore[no-untyped-call]
    os.chmod(token_path, 0o600)
    logger.info("wrote Google Calendar token to %s", token_path)
