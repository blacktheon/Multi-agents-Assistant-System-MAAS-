"""Watchlist loader tests. The loader reads the [[watch]] array from a
TOML file and produces a list of frozen WatchEntry records. Malformed
entries raise RuntimeError naming the file and field so the failure
message is directly actionable."""
from __future__ import annotations

from pathlib import Path

import pytest

from project0.intelligence.watchlist import WatchEntry, load_watchlist


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "intelligence.toml"
    p.write_text(content, encoding="utf-8")
    return p


def test_valid_toml_produces_watch_entries(tmp_path: Path):
    p = _write(tmp_path, """
[llm.summarizer]
model = "claude-opus-4-6"

[[watch]]
handle = "openai"
tags = ["ai-labs", "first-party"]
notes = "OpenAI official"

[[watch]]
handle = "sama"
tags = ["executive"]

[[watch]]
handle = "@anthropicai"
""")
    entries = load_watchlist(p)
    assert len(entries) == 3
    assert entries[0] == WatchEntry(handle="openai", tags=("ai-labs", "first-party"), notes="OpenAI official")
    assert entries[1] == WatchEntry(handle="sama", tags=("executive",), notes="")
    # Leading @ is stripped, handle is lowercased.
    assert entries[2] == WatchEntry(handle="anthropicai", tags=(), notes="")


def test_missing_watch_array_returns_empty_list(tmp_path: Path):
    p = _write(tmp_path, """
[llm.summarizer]
model = "claude-opus-4-6"
""")
    assert load_watchlist(p) == []


def test_missing_handle_raises_runtime_error(tmp_path: Path):
    p = _write(tmp_path, """
[[watch]]
tags = ["orphan"]
""")
    with pytest.raises(RuntimeError, match="handle"):
        load_watchlist(p)


def test_duplicate_handle_raises_runtime_error(tmp_path: Path):
    p = _write(tmp_path, """
[[watch]]
handle = "sama"

[[watch]]
handle = "SAMA"
""")
    with pytest.raises(RuntimeError, match="duplicate"):
        load_watchlist(p)


def test_empty_handle_raises_runtime_error(tmp_path: Path):
    p = _write(tmp_path, """
[[watch]]
handle = ""
""")
    with pytest.raises(RuntimeError, match="handle"):
        load_watchlist(p)
