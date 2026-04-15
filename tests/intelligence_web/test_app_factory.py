from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from project0.intelligence_web.app import create_app
from project0.intelligence_web.config import WebConfig


def test_create_app_returns_fastapi_instance(tmp_path: Path) -> None:
    cfg = WebConfig(
        public_base_url="http://test.local",
        bind_host="127.0.0.1",
        bind_port=8080,
        reports_dir=tmp_path / "reports",
        feedback_dir=tmp_path / "feedback",
        user_tz=ZoneInfo("UTC"),
    )
    app = create_app(cfg)
    assert isinstance(app, FastAPI)


def test_create_app_with_nonexistent_dirs_still_constructs(tmp_path: Path) -> None:
    cfg = WebConfig(
        public_base_url="http://test.local",
        bind_host="127.0.0.1",
        bind_port=8080,
        reports_dir=tmp_path / "not-created",
        feedback_dir=tmp_path / "also-not-created",
        user_tz=ZoneInfo("UTC"),
    )
    # Should NOT raise — directories are read lazily per request.
    app = create_app(cfg)
    client = TestClient(app)
    # healthz should work regardless of directory state
    assert client.get("/healthz").status_code == 200
