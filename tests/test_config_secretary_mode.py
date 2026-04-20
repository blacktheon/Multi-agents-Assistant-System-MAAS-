"""Settings must accept SECRETARY_MODE and local-LLM env vars."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture
def required_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Minimal required env so load_settings does not bail on other vars.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_SECRETARY", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_MANAGER", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_INTELLIGENCE", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_LEARNING", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_SUPERVISOR", "x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("USER_TIMEZONE", "UTC")
    monkeypatch.setenv("NOTION_INTERNAL_INTEGRATION_SECRET", "x")
    monkeypatch.setenv("NOTION_DATABASE_ID", "x")
    yield


def test_default_secretary_mode_is_work(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from project0.config import load_settings
    monkeypatch.delenv("SECRETARY_MODE", raising=False)
    s = load_settings()
    assert s.secretary_mode == "work"
    assert s.local_llm_base_url == "http://127.0.0.1:8000/v1"
    assert s.local_llm_model == "qwen2.5-72b-awq-8k"
    assert s.local_llm_api_key == "unused"


def test_secretary_mode_free(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("SECRETARY_MODE", "free")
    s = load_settings()
    assert s.secretary_mode == "free"


def test_secretary_mode_invalid_raises(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("SECRETARY_MODE", "chaos")
    with pytest.raises(RuntimeError, match="SECRETARY_MODE"):
        load_settings()


def test_local_llm_overrides(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("SECRETARY_MODE", "free")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://192.168.1.50:9000/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "other-model")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "token123")
    s = load_settings()
    assert s.local_llm_base_url == "http://192.168.1.50:9000/v1"
    assert s.local_llm_model == "other-model"
    assert s.local_llm_api_key == "token123"
