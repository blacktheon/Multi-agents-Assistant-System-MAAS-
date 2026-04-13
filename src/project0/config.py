"""Load and validate settings from the environment (plus a .env file).

The allow-list is required. Without it, anyone who finds a bot on Telegram
can talk to it. load_settings() raises RuntimeError at startup if any
required variable is missing or malformed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from project0.agents.registry import AGENT_SPECS


@dataclass(frozen=True)
class Settings:
    bot_tokens: dict[str, str]
    allowed_chat_ids: frozenset[int]
    allowed_user_ids: frozenset[int]
    anthropic_api_key: str
    store_path: str
    log_level: str
    user_tz: ZoneInfo
    google_calendar_id: str
    google_token_path: Path
    google_client_secrets_path: Path


def _parse_int_csv(name: str, raw: str) -> frozenset[int]:
    if not raw.strip():
        raise RuntimeError(f"{name} must be a non-empty comma-separated list of integers")
    out: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError as e:
            raise RuntimeError(f"{name} contains non-integer value {chunk!r}") from e
    if not out:
        raise RuntimeError(f"{name} produced an empty set after parsing")
    return frozenset(out)


def load_settings() -> Settings:
    load_dotenv(override=False)

    # Derive required bot-token env vars from the agent registry so that
    # adding a new agent is a single-place edit in agents/registry.py and
    # does NOT require touching this file.
    bot_tokens: dict[str, str] = {}
    for spec in AGENT_SPECS.values():
        val = os.environ.get(spec.token_env_key, "").strip()
        if not val:
            raise RuntimeError(
                f"{spec.token_env_key} is required but was empty or unset"
            )
        bot_tokens[spec.name] = val

    chat_raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not chat_raw:
        raise RuntimeError("TELEGRAM_ALLOWED_CHAT_IDS is required but was empty or unset")
    allowed_chat_ids = _parse_int_csv("TELEGRAM_ALLOWED_CHAT_IDS", chat_raw)

    user_raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    if not user_raw:
        raise RuntimeError("TELEGRAM_ALLOWED_USER_IDS is required but was empty or unset")
    allowed_user_ids = _parse_int_csv("TELEGRAM_ALLOWED_USER_IDS", user_raw)

    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required but was empty or unset")
    if not anthropic_api_key.startswith("sk-"):
        raise RuntimeError("ANTHROPIC_API_KEY looks malformed (expected 'sk-...' prefix)")

    store_path = os.environ.get("STORE_PATH", "").strip() or "data/store.db"
    log_level = os.environ.get("LOG_LEVEL", "").strip() or "INFO"

    user_tz_name = os.environ.get("USER_TIMEZONE", "").strip()
    if not user_tz_name:
        raise RuntimeError("USER_TIMEZONE is required but was empty or unset")
    try:
        user_tz = ZoneInfo(user_tz_name)
    except ZoneInfoNotFoundError as e:
        raise RuntimeError(f"USER_TIMEZONE {user_tz_name!r} is not a valid IANA timezone") from e

    google_calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "").strip() or "primary"
    google_token_path = Path(
        os.environ.get("GOOGLE_TOKEN_PATH", "").strip() or "data/google_token.json"
    )
    google_client_secrets_path = Path(
        os.environ.get("GOOGLE_CLIENT_SECRETS_PATH", "").strip() or "data/google_client_secrets.json"
    )

    return Settings(
        bot_tokens=bot_tokens,
        allowed_chat_ids=allowed_chat_ids,
        allowed_user_ids=allowed_user_ids,
        anthropic_api_key=anthropic_api_key,
        store_path=store_path,
        log_level=log_level,
        user_tz=user_tz,
        google_calendar_id=google_calendar_id,
        google_token_path=google_token_path,
        google_client_secrets_path=google_client_secrets_path,
    )
