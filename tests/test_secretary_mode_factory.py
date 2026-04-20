"""Unit tests for _build_secretary_dependencies factory.

Constructs Settings directly (no env parsing) to isolate the factory's
selection logic and the local⇒writer=None invariant.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.config import Settings
from project0.llm.local_provider import LocalProvider
from project0.llm.provider import AnthropicProvider
from project0.main import _build_secretary_dependencies
from project0.store import LLMUsageStore, Store, UserFactsWriter


def _settings(mode: str) -> Settings:
    return Settings(
        bot_tokens={"secretary": "x", "manager": "x", "intelligence": "x",
                    "learning": "x", "supervisor": "x"},
        allowed_chat_ids=frozenset({1}),
        allowed_user_ids=frozenset({1}),
        anthropic_api_key="sk-test",
        store_path="ignored",
        log_level="INFO",
        user_tz=ZoneInfo("UTC"),
        google_calendar_id="primary",
        google_token_path=Path("ignored"),
        google_client_secrets_path=Path("ignored"),
        notion_token="x",
        notion_database_id="x",
        secretary_mode=mode,  # type: ignore[arg-type]
    )


def test_work_mode_returns_anthropic_writer_and_secretary_prompts(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "s.db"))
    store.init_schema()
    usage = LLMUsageStore(store.conn)
    anthropic = AnthropicProvider(
        api_key="sk-test", model="claude-sonnet-4-6", usage_store=usage,
    )
    writer = UserFactsWriter("secretary", store.conn)

    provider, persona_path, config_path, wired_writer = _build_secretary_dependencies(
        settings=_settings("work"),
        usage_store=usage,
        anthropic_provider=anthropic,
        base_facts_writer=writer,
    )
    assert provider is anthropic
    assert persona_path == Path("prompts/secretary.md")
    assert config_path == Path("prompts/secretary.toml")
    assert wired_writer is writer


def test_free_mode_returns_local_provider_and_free_prompts_and_no_writer(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "s.db"))
    store.init_schema()
    usage = LLMUsageStore(store.conn)
    anthropic = AnthropicProvider(
        api_key="sk-test", model="claude-sonnet-4-6", usage_store=usage,
    )
    writer = UserFactsWriter("secretary", store.conn)

    provider, persona_path, config_path, wired_writer = _build_secretary_dependencies(
        settings=_settings("free"),
        usage_store=usage,
        anthropic_provider=anthropic,
        base_facts_writer=writer,
    )
    assert isinstance(provider, LocalProvider)
    assert persona_path == Path("prompts/secretary_free.md")
    assert config_path == Path("prompts/secretary_free.toml")
    assert wired_writer is None


def test_factory_signature_exposes_base_facts_writer(tmp_path: Path) -> None:
    # Pin the shape so callers can't bypass the invariant by calling without
    # the writer argument (would return garbage silently).
    sig = inspect.signature(_build_secretary_dependencies)
    assert "base_facts_writer" in sig.parameters
    assert "anthropic_provider" in sig.parameters
    assert "usage_store" in sig.parameters
    assert "settings" in sig.parameters
