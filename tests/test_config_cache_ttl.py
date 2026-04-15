from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from project0.config import load_settings

ENV_MIN: dict[str, str] = {
    "TELEGRAM_BOT_TOKEN_MANAGER": "t1",
    "TELEGRAM_BOT_TOKEN_SECRETARY": "t2",
    "TELEGRAM_BOT_TOKEN_INTELLIGENCE": "t3",
    "TELEGRAM_ALLOWED_CHAT_IDS": "-100123",
    "TELEGRAM_ALLOWED_USER_IDS": "42",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "STORE_PATH": ":memory:",
    "USER_TIMEZONE": "Asia/Shanghai",
    "GOOGLE_CALENDAR_ID": "primary",
    "MANAGER_PULSE_CHAT_ID": "-100123",
    "TWITTERAPI_IO_API_KEY": "x",
}


def test_default_cache_ttl_is_ephemeral() -> None:
    with patch.dict(os.environ, ENV_MIN, clear=True):
        s = load_settings()
    assert s.anthropic_cache_ttl == "ephemeral"


def test_explicit_ephemeral() -> None:
    with patch.dict(os.environ, {**ENV_MIN, "ANTHROPIC_CACHE_TTL": "ephemeral"}, clear=True):
        s = load_settings()
    assert s.anthropic_cache_ttl == "ephemeral"


def test_explicit_1h() -> None:
    with patch.dict(os.environ, {**ENV_MIN, "ANTHROPIC_CACHE_TTL": "1h"}, clear=True):
        s = load_settings()
    assert s.anthropic_cache_ttl == "1h"


def test_invalid_value_raises_at_startup() -> None:
    with (
        patch.dict(os.environ, {**ENV_MIN, "ANTHROPIC_CACHE_TTL": "5m"}, clear=True),
        pytest.raises(RuntimeError) as exc_info,
    ):
        load_settings()
    assert "ANTHROPIC_CACHE_TTL" in str(exc_info.value)
    assert "5m" in str(exc_info.value)


def test_empty_value_raises() -> None:
    with (
        patch.dict(os.environ, {**ENV_MIN, "ANTHROPIC_CACHE_TTL": ""}, clear=True),
        pytest.raises(RuntimeError),
    ):
        load_settings()
