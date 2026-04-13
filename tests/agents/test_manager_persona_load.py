from pathlib import Path

import pytest

from project0.agents.manager import (
    ManagerConfig,
    ManagerPersona,
    load_manager_config,
    load_manager_persona,
)


def test_loads_real_persona_file():
    persona = load_manager_persona(Path("prompts/manager.md"))
    assert isinstance(persona, ManagerPersona)
    assert "经理" in persona.core
    assert "私聊" in persona.dm_mode
    assert "群聊点名" in persona.group_addressed_mode
    assert "定时脉冲" in persona.pulse_mode
    assert "工具使用守则" in persona.tool_use_guide


def test_loads_real_config_file():
    cfg = load_manager_config(Path("prompts/manager.toml"))
    assert isinstance(cfg, ManagerConfig)
    assert cfg.model.startswith("claude-")
    assert cfg.max_tokens_reply > 0
    assert cfg.max_tool_iterations >= 1
    assert cfg.transcript_window >= 1


def test_missing_section_raises(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("# 经理 — 角色设定\nhello\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing section"):
        load_manager_persona(p)


def test_near_miss_header_detected(tmp_path):
    p = tmp_path / "typo.md"
    p.write_text(
        "# 经理 — 角色设定\ncore\n\n# 模式:私聊\nbody\n",  # half-width colon, no space
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed section header"):
        load_manager_persona(p)


def test_missing_config_key_raises(tmp_path):
    p = tmp_path / "cfg.toml"
    p.write_text("[llm]\nmodel='x'\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="max_tokens_reply"):
        load_manager_config(p)
