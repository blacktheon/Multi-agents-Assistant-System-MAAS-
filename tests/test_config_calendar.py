import pytest

from project0.config import load_settings


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "project0.config.load_dotenv",
        lambda *args, **kwargs: None,
    )


@pytest.fixture
def base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_MANAGER", "t1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_INTELLIGENCE", "t2")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_SECRETARY", "t3")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_LEARNING", "t4")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "2")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-foo")
    monkeypatch.setenv("STORE_PATH", str(tmp_path / "store.db"))
    monkeypatch.setenv("NOTION_TOKEN", "notion-test-token")
    monkeypatch.setenv("NOTION_DATABASE_ID", "notion-test-db-id")
    return monkeypatch


def test_user_tz_required(base_env):
    base_env.delenv("USER_TIMEZONE", raising=False)
    with pytest.raises(RuntimeError, match="USER_TIMEZONE"):
        load_settings()


def test_user_tz_invalid(base_env):
    base_env.setenv("USER_TIMEZONE", "Not/A/Zone")
    with pytest.raises(RuntimeError, match="USER_TIMEZONE"):
        load_settings()


def test_calendar_defaults(base_env):
    base_env.setenv("USER_TIMEZONE", "Asia/Shanghai")
    s = load_settings()
    assert s.user_tz.key == "Asia/Shanghai"
    assert s.google_calendar_id == "primary"
    assert s.google_token_path.name == "google_token.json"
    assert s.google_client_secrets_path.name == "google_client_secrets.json"


def test_calendar_overrides(base_env, tmp_path):
    base_env.setenv("USER_TIMEZONE", "UTC")
    base_env.setenv("GOOGLE_CALENDAR_ID", "work@example.com")
    base_env.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "t.json"))
    base_env.setenv("GOOGLE_CLIENT_SECRETS_PATH", str(tmp_path / "c.json"))
    s = load_settings()
    assert s.google_calendar_id == "work@example.com"
    assert s.google_token_path == tmp_path / "t.json"
    assert s.google_client_secrets_path == tmp_path / "c.json"
