# tests/pulse/test_pulse_loader.py
from pathlib import Path

import pytest

from project0.pulse import PulseEntry, load_pulse_entries


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agent.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_no_pulse_array_is_empty_list(tmp_path):
    p = _write(tmp_path, "[llm]\nmodel = 'x'\n")
    assert load_pulse_entries(p) == []


def test_valid_entries_parse(tmp_path, monkeypatch):
    monkeypatch.setenv("MANAGER_PULSE_CHAT_ID", "12345")
    p = _write(
        tmp_path,
        """
[[pulse]]
name = "check_calendar"
every_seconds = 300
chat_id_env = "MANAGER_PULSE_CHAT_ID"
payload = { window_minutes = 60 }

[[pulse]]
name = "nightly"
every_seconds = 3600
payload = { note = "sleep" }
""",
    )
    entries = load_pulse_entries(p)
    assert len(entries) == 2
    assert entries[0] == PulseEntry(
        name="check_calendar",
        every_seconds=300,
        chat_id=12345,
        payload={"window_minutes": 60},
    )
    assert entries[1].chat_id is None
    assert entries[1].payload == {"note": "sleep"}


def test_missing_env_var_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("MANAGER_PULSE_CHAT_ID", raising=False)
    p = _write(
        tmp_path,
        """
[[pulse]]
name = "check_calendar"
every_seconds = 300
chat_id_env = "MANAGER_PULSE_CHAT_ID"
""",
    )
    with pytest.raises(RuntimeError, match="MANAGER_PULSE_CHAT_ID"):
        load_pulse_entries(p)


def test_non_int_env_var_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("BAD_CHAT", "not-a-number")
    p = _write(
        tmp_path,
        """
[[pulse]]
name = "check_calendar"
every_seconds = 300
chat_id_env = "BAD_CHAT"
""",
    )
    with pytest.raises(RuntimeError, match="BAD_CHAT"):
        load_pulse_entries(p)


def test_every_seconds_too_small_raises(tmp_path):
    p = _write(
        tmp_path,
        """
[[pulse]]
name = "too_fast"
every_seconds = 5
""",
    )
    with pytest.raises(RuntimeError, match="every_seconds"):
        load_pulse_entries(p)


def test_duplicate_name_raises(tmp_path):
    p = _write(
        tmp_path,
        """
[[pulse]]
name = "check_calendar"
every_seconds = 300

[[pulse]]
name = "check_calendar"
every_seconds = 600
""",
    )
    with pytest.raises(RuntimeError, match="duplicate"):
        load_pulse_entries(p)
