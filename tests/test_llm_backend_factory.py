"""Provider factories in main.py must follow LLM_BACKEND and
INTELLIGENCE_BRIEFING_BACKEND. Settings are constructed directly (no env
parsing) to isolate the selection logic."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.config import Settings
from project0.llm.local_provider import LocalProvider
from project0.llm.provider import AnthropicProvider, FakeProvider
from project0.main import _build_intelligence_providers, _build_llm_provider
from project0.store import LLMUsageStore, Store

SUMMARIZER = "claude-opus-4-6"
QA = "claude-sonnet-4-6"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = dict(
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
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def usage(tmp_path: Path) -> LLMUsageStore:
    store = Store(str(tmp_path / "s.db"))
    store.init_schema()
    return LLMUsageStore(store.conn)


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    yield


def _intel(settings: Settings, usage: LLMUsageStore, default):
    return _build_intelligence_providers(
        settings=settings,
        usage_store=usage,
        default_provider=default,
        summarizer_model=SUMMARIZER,
        qa_model=QA,
    )


def test_default_backend_builds_anthropic(usage: LLMUsageStore) -> None:
    p = _build_llm_provider(_settings(), usage)
    assert isinstance(p, AnthropicProvider)
    assert p._model == "claude-sonnet-4-6"


def test_local_backend_builds_local_provider_from_settings(usage: LLMUsageStore) -> None:
    p = _build_llm_provider(_settings(llm_backend="local", local_llm_model="qwen-test"), usage)
    assert isinstance(p, LocalProvider)
    assert p._model == "qwen-test"


def test_fake_provider_env_overrides_backend(
    usage: LLMUsageStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    p = _build_llm_provider(_settings(llm_backend="local"), usage)
    assert isinstance(p, FakeProvider)


def test_intelligence_all_anthropic_by_default(usage: LLMUsageStore) -> None:
    s = _settings()
    summarizer, qa = _intel(s, usage, _build_llm_provider(s, usage))
    assert isinstance(summarizer, AnthropicProvider)
    assert summarizer._model == SUMMARIZER
    assert isinstance(qa, AnthropicProvider)
    assert qa._model == QA


def test_intelligence_briefing_stays_anthropic_when_backend_local(usage: LLMUsageStore) -> None:
    s = _settings(llm_backend="local")
    default = _build_llm_provider(s, usage)
    summarizer, qa = _intel(s, usage, default)
    assert isinstance(summarizer, AnthropicProvider)
    assert summarizer._model == SUMMARIZER
    assert qa is default


def test_intelligence_briefing_local_when_asked(usage: LLMUsageStore) -> None:
    s = _settings(llm_backend="local", intelligence_briefing_backend="local")
    default = _build_llm_provider(s, usage)
    summarizer, qa = _intel(s, usage, default)
    assert isinstance(summarizer, LocalProvider)
    assert qa is default


def test_local_backend_wires_thinking_allowance(usage: LLMUsageStore) -> None:
    p = _build_llm_provider(_settings(llm_backend="local", local_llm_thinking_budget=512), usage)
    assert isinstance(p, LocalProvider)
    assert p._thinking_allowance == 512
