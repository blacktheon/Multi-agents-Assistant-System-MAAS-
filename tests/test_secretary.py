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


def test_weighted_len_counts_cjk_as_three_and_ascii_as_one() -> None:
    from project0.agents.secretary import weighted_len
    assert weighted_len("") == 0
    assert weighted_len("hello") == 5
    assert weighted_len("你好") == 6  # 2 CJK chars × 3
    assert weighted_len("hi 你") == 2 + 1 + 3
    assert weighted_len("   ") == 3  # whitespace is ASCII


def test_is_skip_sentinel_exact_match() -> None:
    from project0.agents.secretary import is_skip_sentinel
    sentinels = ["[skip]", "[跳过]", "【skip】"]
    assert is_skip_sentinel("[skip]", sentinels)
    assert is_skip_sentinel("  [skip]  ", sentinels)
    assert is_skip_sentinel("[SKIP]", sentinels)  # case-insensitive
    assert is_skip_sentinel("[跳过]", sentinels)
    assert is_skip_sentinel("【skip】", sentinels)


def test_is_skip_sentinel_starts_with_match() -> None:
    """The model may emit '[skip] nothing clicks here' — still a skip."""
    from project0.agents.secretary import is_skip_sentinel
    sentinels = ["[skip]"]
    assert is_skip_sentinel("[skip] this beat is already covered", sentinels)
    assert is_skip_sentinel("[skip].", sentinels)
    assert is_skip_sentinel("[skip]\nreasoning", sentinels)
    # But not when the sentinel is just part of a longer word.
    assert not is_skip_sentinel("[skipthis]", sentinels)


def test_is_skip_sentinel_negative_cases() -> None:
    from project0.agents.secretary import is_skip_sentinel
    sentinels = ["[skip]"]
    assert not is_skip_sentinel("嘿你今天怎么这么努力", sentinels)
    assert not is_skip_sentinel("", sentinels)
    assert not is_skip_sentinel("no skip here", sentinels)
