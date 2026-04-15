from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

from project0.intelligence_web.config import WebConfig


def test_valid_full_config() -> None:
    section = {
        "public_base_url": "http://intel.tailnet.ts.net:8080",
        "bind_host": "127.0.0.1",
        "bind_port": 9000,
        "reports_dir": "/tmp/reports",
        "feedback_dir": "/tmp/feedback",
        "user_tz": "UTC",
    }
    cfg = WebConfig.from_toml_section(section)
    assert cfg.public_base_url == "http://intel.tailnet.ts.net:8080"
    assert cfg.bind_host == "127.0.0.1"
    assert cfg.bind_port == 9000
    assert cfg.reports_dir == Path("/tmp/reports")
    assert cfg.feedback_dir == Path("/tmp/feedback")
    assert cfg.user_tz == ZoneInfo("UTC")


def test_valid_minimal_config_applies_defaults() -> None:
    section = {"public_base_url": "http://localhost:8080"}
    cfg = WebConfig.from_toml_section(section)
    assert cfg.public_base_url == "http://localhost:8080"
    assert cfg.bind_host == "0.0.0.0"
    assert cfg.bind_port == 8080
    assert cfg.reports_dir == Path("data/intelligence/reports")
    assert cfg.feedback_dir == Path("data/intelligence/feedback")
    assert cfg.user_tz == ZoneInfo("Asia/Shanghai")


def test_rejects_base_url_without_scheme() -> None:
    with pytest.raises(RuntimeError, match="public_base_url"):
        WebConfig.from_toml_section({"public_base_url": "intel.ts.net:8080"})


def test_rejects_base_url_with_ftp_scheme() -> None:
    with pytest.raises(RuntimeError, match="public_base_url"):
        WebConfig.from_toml_section({"public_base_url": "ftp://intel.ts.net/"})


def test_rejects_missing_public_base_url() -> None:
    with pytest.raises(KeyError):
        WebConfig.from_toml_section({})


def test_accepts_https_base_url() -> None:
    cfg = WebConfig.from_toml_section(
        {"public_base_url": "https://intel.example.com"}
    )
    assert cfg.public_base_url == "https://intel.example.com"
