"""Tests for the Secretary agent. All LLM calls go through FakeProvider."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_load_persona_splits_on_mode_headers(tmp_path: Path) -> None:
    from project0.agents.secretary import load_persona

    md = tmp_path / "secretary.md"
    md.write_text(
        """# 秘书 — 角色设定
you are warm and playful
never hallucinate appointments

# 模式：群聊旁观
when in group-listener mode, either reply or output [skip]

# 模式：群聊点名
when addressed in group, always reply

# 模式：私聊
in DMs, be more personal

# 模式：经理委托提醒
deliver reminders warmly
""",
        encoding="utf-8",
    )
    persona = load_persona(md)
    assert "warm and playful" in persona.core
    assert "[skip]" in persona.listener_mode
    assert "always reply" in persona.group_addressed_mode
    assert "more personal" in persona.dm_mode
    assert "warmly" in persona.reminder_mode


def test_load_persona_raises_on_missing_section(tmp_path: Path) -> None:
    from project0.agents.secretary import load_persona

    md = tmp_path / "bad.md"
    md.write_text("# 秘书 — 角色设定\njust the core\n", encoding="utf-8")
    with pytest.raises(ValueError, match="模式：群聊旁观"):
        load_persona(md)


def test_load_config_parses_toml(tmp_path: Path) -> None:
    from project0.agents.secretary import load_config

    toml_path = tmp_path / "secretary.toml"
    toml_path.write_text(
        """
[cooldown]
t_min_seconds = 45
n_min_messages = 2
l_min_weighted_chars = 120

[context]
transcript_window = 10

[llm]
model = "claude-sonnet-4-6"
max_tokens_reply = 500
max_tokens_listener = 250

[skip_sentinels]
patterns = ["[skip]", "[跳过]"]
""",
        encoding="utf-8",
    )
    cfg = load_config(toml_path)
    assert cfg.t_min_seconds == 45
    assert cfg.n_min_messages == 2
    assert cfg.l_min_weighted_chars == 120
    assert cfg.transcript_window == 10
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.max_tokens_reply == 500
    assert cfg.max_tokens_listener == 250
    assert cfg.skip_sentinels == ["[skip]", "[跳过]"]


def test_load_config_raises_on_missing_key(tmp_path: Path) -> None:
    from project0.agents.secretary import load_config

    toml_path = tmp_path / "partial.toml"
    toml_path.write_text(
        """
[cooldown]
t_min_seconds = 45
n_min_messages = 2
# l_min_weighted_chars missing!

[context]
transcript_window = 10

[llm]
model = "x"
max_tokens_reply = 500
max_tokens_listener = 250

[skip_sentinels]
patterns = []
""",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="cooldown.l_min_weighted_chars"):
        load_config(toml_path)


def test_load_persona_raises_on_malformed_header(tmp_path: Path) -> None:
    from project0.agents.secretary import load_persona

    md = tmp_path / "malformed.md"
    md.write_text(
        """#秘书 — 角色设定
body
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed section header"):
        load_persona(md)
