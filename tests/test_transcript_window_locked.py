"""Locks transcript_window values per §E of the memory-hardening spec.
Any drift fails loudly so future edits must pass through the spec."""
from __future__ import annotations

import tomllib
from pathlib import Path


def test_manager_transcript_window_is_10() -> None:
    data = tomllib.loads(Path("prompts/manager.toml").read_text(encoding="utf-8"))
    assert data["context"]["transcript_window"] == 10


def test_secretary_transcript_window_is_20() -> None:
    data = tomllib.loads(Path("prompts/secretary.toml").read_text(encoding="utf-8"))
    assert data["context"]["transcript_window"] == 20


def test_intelligence_transcript_window_is_10() -> None:
    data = tomllib.loads(
        Path("prompts/intelligence.toml").read_text(encoding="utf-8")
    )
    assert data["context"]["transcript_window"] == 10
