"""IntelligenceConfig loader tests. Parses [llm.summarizer], [llm.qa],
[context], [twitter]. Missing keys raise RuntimeError naming the key."""
from __future__ import annotations

from pathlib import Path

import pytest

from project0.agents.intelligence import (
    IntelligenceConfig,
    load_intelligence_config,
)


VALID_TOML = """
[llm.summarizer]
model = "claude-opus-4-6"
max_tokens = 16384

[llm.qa]
model = "claude-sonnet-4-6"
max_tokens = 2048

[context]
transcript_window = 10
max_tool_iterations = 6

[twitter]
timeline_since_hours = 24
max_tweets_per_handle = 50

[[watch]]
handle = "sama"
"""


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "intelligence.toml"
    p.write_text(content, encoding="utf-8")
    return p


def test_valid_toml_parses(tmp_path: Path):
    p = _write(tmp_path, VALID_TOML)
    cfg = load_intelligence_config(p)
    assert isinstance(cfg, IntelligenceConfig)
    assert cfg.summarizer_model == "claude-opus-4-6"
    assert cfg.summarizer_max_tokens == 16384
    assert cfg.qa_model == "claude-sonnet-4-6"
    assert cfg.qa_max_tokens == 2048
    assert cfg.transcript_window == 10
    assert cfg.max_tool_iterations == 6
    assert cfg.timeline_since_hours == 24
    assert cfg.max_tweets_per_handle == 50


def test_missing_key_raises_runtime_error(tmp_path: Path):
    p = _write(tmp_path, """
[llm.summarizer]
model = "claude-opus-4-6"
""")
    with pytest.raises(RuntimeError, match="max_tokens"):
        load_intelligence_config(p)


def test_thinking_budget_optional_defaults_none(tmp_path: Path):
    """6e: thinking_budget_tokens is optional; missing key → None."""
    p = _write(tmp_path, VALID_TOML)
    cfg = load_intelligence_config(p)
    assert cfg.summarizer_thinking_budget is None


def test_thinking_budget_loaded_when_present(tmp_path: Path):
    """6e: when [llm.summarizer].thinking_budget_tokens is set, it loads."""
    toml_with_budget = """
[llm.summarizer]
model = "claude-opus-4-6"
max_tokens = 32768
thinking_budget_tokens = 16384

[llm.qa]
model = "claude-sonnet-4-6"
max_tokens = 2048

[context]
transcript_window = 10
max_tool_iterations = 6

[twitter]
timeline_since_hours = 24
max_tweets_per_handle = 50
"""
    p = _write(tmp_path, toml_with_budget)
    cfg = load_intelligence_config(p)
    assert cfg.summarizer_thinking_budget == 16384
    assert cfg.summarizer_max_tokens == 32768
