"""Load and validate settings from the environment (plus a .env file).

The allow-list is required. Without it, anyone who finds a bot on Telegram
can talk to it. load_settings() raises RuntimeError at startup if any
required variable is missing or malformed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_tokens: dict[str, str]
    allowed_chat_ids: frozenset[int]
    allowed_user_ids: frozenset[int]
    anthropic_api_key: str
    store_path: str
    log_level: str


_REQUIRED_SINGLES = {
    "TELEGRAM_BOT_TOKEN_MANAGER": "manager",
    "TELEGRAM_BOT_TOKEN_INTELLIGENCE": "intelligence",
}


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

    bot_tokens: dict[str, str] = {}
    for env_key, agent_name in _REQUIRED_SINGLES.items():
        val = os.environ.get(env_key, "").strip()
        if not val:
            raise RuntimeError(f"{env_key} is required but was empty or unset")
        bot_tokens[agent_name] = val

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

    return Settings(
        bot_tokens=bot_tokens,
        allowed_chat_ids=allowed_chat_ids,
        allowed_user_ids=allowed_user_ids,
        anthropic_api_key=anthropic_api_key,
        store_path=store_path,
        log_level=log_level,
    )
