"""Settings must accept LLM_BACKEND and INTELLIGENCE_BRIEFING_BACKEND.

Both default to "anthropic" so an unchanged .env reproduces today's
behaviour; "local" routes to the LocalProvider (see
docs/superpowers/specs/2026-10-24-all-agents-local-llm-design.md).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture
def required_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("project0.config.load_dotenv", lambda *a, **kw: False)
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
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    monkeypatch.delenv("INTELLIGENCE_BRIEFING_BACKEND", raising=False)
    yield


def test_defaults_are_anthropic(required_env: None) -> None:
    from project0.config import load_settings
    s = load_settings()
    assert s.llm_backend == "anthropic"
    assert s.intelligence_briefing_backend == "anthropic"


def test_llm_backend_local_leaves_briefing_on_anthropic(
    required_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("LLM_BACKEND", "local")
    s = load_settings()
    assert s.llm_backend == "local"
    assert s.intelligence_briefing_backend == "anthropic"


def test_briefing_backend_is_independent(
    required_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("INTELLIGENCE_BRIEFING_BACKEND", "local")
    s = load_settings()
    assert s.llm_backend == "anthropic"
    assert s.intelligence_briefing_backend == "local"


def test_values_are_case_insensitive(
    required_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("LLM_BACKEND", " Local ")
    assert load_settings().llm_backend == "local"


def test_invalid_llm_backend_raises(
    required_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("LLM_BACKEND", "openai")
    with pytest.raises(RuntimeError, match="LLM_BACKEND"):
        load_settings()


def test_invalid_briefing_backend_raises(
    required_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("INTELLIGENCE_BRIEFING_BACKEND", "x")
    with pytest.raises(RuntimeError, match="INTELLIGENCE_BRIEFING_BACKEND"):
        load_settings()


def test_thinking_budget_default(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from project0.config import load_settings
    monkeypatch.delenv("LOCAL_LLM_THINKING_BUDGET", raising=False)
    assert load_settings().local_llm_thinking_budget == 4096


def test_thinking_budget_override(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("LOCAL_LLM_THINKING_BUDGET", "8192")
    assert load_settings().local_llm_thinking_budget == 8192


def test_thinking_budget_invalid_raises(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("LOCAL_LLM_THINKING_BUDGET", "lots")
    with pytest.raises(RuntimeError, match="LOCAL_LLM_THINKING_BUDGET"):
        load_settings()


def test_local_thinking_default_off(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from project0.config import load_settings
    monkeypatch.delenv("LOCAL_LLM_THINKING", raising=False)
    s = load_settings()
    assert s.local_llm_thinking is False
    assert s.local_llm_model == "qwen3.5-122b-abliterated-q4km-nothink"


def test_local_thinking_truthy_values(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from project0.config import load_settings
    for raw in ("true", "1", "yes", "On"):
        monkeypatch.setenv("LOCAL_LLM_THINKING", raw)
        assert load_settings().local_llm_thinking is True, raw


def test_local_thinking_invalid_raises(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("LOCAL_LLM_THINKING", "maybe")
    with pytest.raises(RuntimeError, match="LOCAL_LLM_THINKING"):
        load_settings()
