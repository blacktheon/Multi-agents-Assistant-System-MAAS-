"""Tests for the Learning agent — persona loading, config loading."""

from __future__ import annotations

from pathlib import Path

import pytest


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def test_load_persona_has_all_sections() -> None:
    from project0.agents.learning import load_learning_persona

    persona = load_learning_persona(PROMPTS_DIR / "learning.md")

    # Each field should contain representative Chinese text from its section
    assert "温书瑶" in persona.core
    assert "角色设定" in persona.core
    assert "私聊" in persona.dm_mode
    assert "群聊" in persona.group_addressed_mode
    assert "脉冲" in persona.pulse_mode
    assert "工具" in persona.tool_use_guide


def test_load_config_parses_all_fields() -> None:
    from project0.agents.learning import load_learning_config

    cfg = load_learning_config(PROMPTS_DIR / "learning.toml")

    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.max_tokens_reply == 2048
    assert cfg.max_tool_iterations == 5
    assert cfg.transcript_window == 10
    assert cfg.sync_interval_seconds == 30
    assert cfg.reminder_interval_seconds == 1800
    assert cfg.intervals_days == [1, 3, 7, 14, 30]
    assert cfg.max_summary_tokens == 800


def test_load_persona_raises_on_missing_section(tmp_path: Path) -> None:
    from project0.agents.learning import load_learning_persona

    md = tmp_path / "bad.md"
    md.write_text("# 学习助手 — 角色设定\njust the core\n", encoding="utf-8")
    with pytest.raises(ValueError, match="模式：私聊"):
        load_learning_persona(md)


def test_load_persona_raises_on_malformed_header(tmp_path: Path) -> None:
    from project0.agents.learning import load_learning_persona

    md = tmp_path / "malformed.md"
    md.write_text(
        "#学习助手 — 角色设定\ncore text\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed section header"):
        load_learning_persona(md)


def test_load_config_raises_on_missing_key(tmp_path: Path) -> None:
    from project0.agents.learning import load_learning_config

    toml_path = tmp_path / "partial.toml"
    toml_path.write_text(
        """
[llm]
model = "test"
max_tokens_reply = 100
max_tool_iterations = 3

[context]
transcript_window = 5
""",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="notion.sync_interval_seconds"):
        load_learning_config(toml_path)


def test_learning_agent_builds_tool_specs() -> None:
    from project0.agents.learning import (
        LearningAgent,
        LearningConfig,
        LearningPersona,
    )

    persona = LearningPersona(
        core="core", dm_mode="dm", group_addressed_mode="group",
        pulse_mode="pulse", tool_use_guide="tools",
    )
    config = LearningConfig(
        model="test", max_tokens_reply=100, max_tool_iterations=3,
        transcript_window=5, sync_interval_seconds=30,
        reminder_interval_seconds=1800, intervals_days=[1, 3, 7],
        max_summary_tokens=400,
    )
    agent = LearningAgent(
        llm=None, notion=None, knowledge_index=None,
        review_schedule=None, messages_store=None,
        persona=persona, config=config,
    )
    names = {s.name for s in agent._tool_specs}
    assert names == {
        "process_link", "process_text", "list_upcoming_reviews",
        "mark_reviewed", "list_entries", "get_entry",
    }
