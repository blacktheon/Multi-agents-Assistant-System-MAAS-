from __future__ import annotations

import pytest

from project0.config import Settings, load_settings


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent the real ``.env`` file (if any) from leaking into these tests.

    ``load_settings`` calls ``load_dotenv(override=False)``, which reads the
    project's ``.env`` from disk. Once the user fills in a real ``.env`` for
    the smoke test, that file starts populating env vars that the test
    expected to be absent. Stubbing ``load_dotenv`` keeps the unit tests
    hermetic.
    """
    monkeypatch.setattr(
        "project0.config.load_dotenv",
        lambda *args, **kwargs: None,
    )


def _full_env() -> dict[str, str]:
    return {
        "TELEGRAM_BOT_TOKEN_MANAGER": "m-token",
        "TELEGRAM_BOT_TOKEN_INTELLIGENCE": "i-token",
        "TELEGRAM_BOT_TOKEN_SECRETARY": "s-token",
        "TELEGRAM_ALLOWED_CHAT_IDS": "-100123,-100456",
        "TELEGRAM_ALLOWED_USER_IDS": "42",
        "ANTHROPIC_API_KEY": "sk-ant-xxx",
        "STORE_PATH": "data/store.db",
        "LOG_LEVEL": "INFO",
    }


def test_load_settings_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _full_env().items():
        monkeypatch.setenv(k, v)
    s = load_settings()
    assert isinstance(s, Settings)
    assert s.bot_tokens == {
        "manager": "m-token",
        "intelligence": "i-token",
        "secretary": "s-token",
    }
    assert s.allowed_chat_ids == frozenset({-100123, -100456})
    assert s.allowed_user_ids == frozenset({42})
    assert s.anthropic_api_key == "sk-ant-xxx"
    assert s.store_path == "data/store.db"
    assert s.log_level == "INFO"


@pytest.mark.parametrize(
    "missing_key",
    [
        "TELEGRAM_BOT_TOKEN_MANAGER",
        "TELEGRAM_BOT_TOKEN_INTELLIGENCE",
        "TELEGRAM_BOT_TOKEN_SECRETARY",
        "TELEGRAM_ALLOWED_CHAT_IDS",
        "TELEGRAM_ALLOWED_USER_IDS",
        "ANTHROPIC_API_KEY",
    ],
)
def test_load_settings_missing_required_raises(
    monkeypatch: pytest.MonkeyPatch, missing_key: str
) -> None:
    env = _full_env()
    env.pop(missing_key)
    for k in _full_env():
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(RuntimeError, match=missing_key):
        load_settings()


def test_load_settings_rejects_non_integer_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _full_env()
    env["TELEGRAM_ALLOWED_CHAT_IDS"] = "-100123,not-an-int"
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(RuntimeError, match="TELEGRAM_ALLOWED_CHAT_IDS"):
        load_settings()


def test_load_settings_defaults_optional_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _full_env()
    del env["STORE_PATH"]
    del env["LOG_LEVEL"]
    for k in _full_env():
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    s = load_settings()
    assert s.store_path == "data/store.db"
    assert s.log_level == "INFO"
