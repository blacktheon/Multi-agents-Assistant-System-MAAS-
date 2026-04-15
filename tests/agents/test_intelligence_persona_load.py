"""IntelligencePersona parser tests. Five canonical Chinese headers, same
parse-style as Manager/Secretary: exact header match + near-miss detection
with suggestion."""
from __future__ import annotations

from pathlib import Path

import pytest

from project0.agents.intelligence import (
    IntelligencePersona,
    load_intelligence_persona,
)


VALID_PERSONA = """# 情报 — 角色设定
core content 1

# 模式：私聊
dm content 2

# 模式：群聊点名
group content 3

# 模式：被经理委派
delegated content 4

# 模式：工具使用守则
tools content 5
"""


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "intelligence.md"
    p.write_text(content, encoding="utf-8")
    return p


def test_valid_persona_parses(tmp_path: Path):
    p = _write(tmp_path, VALID_PERSONA)
    persona = load_intelligence_persona(p)
    assert isinstance(persona, IntelligencePersona)
    assert "core content 1" in persona.core
    assert "dm content 2" in persona.dm_mode
    assert "group content 3" in persona.group_addressed_mode
    assert "delegated content 4" in persona.delegated_mode
    assert "tools content 5" in persona.tool_use_guide


def test_missing_section_raises(tmp_path: Path):
    # Drop the tool-use section.
    p = _write(
        tmp_path,
        """# 情报 — 角色设定
core

# 模式：私聊
dm

# 模式：群聊点名
group

# 模式：被经理委派
del
""",
    )
    with pytest.raises(ValueError, match="工具使用守则"):
        load_intelligence_persona(p)


def test_near_miss_header_raises_with_canonical_suggestion(tmp_path: Path):
    # Use half-width colon instead of full-width.
    p = _write(
        tmp_path,
        """# 情报 — 角色设定
core

# 模式:私聊
oops

# 模式：群聊点名
g

# 模式：被经理委派
d

# 模式：工具使用守则
t
""",
    )
    with pytest.raises(ValueError, match="私聊"):
        load_intelligence_persona(p)


def test_real_persona_mentions_get_report_link() -> None:
    """6e: prompts/intelligence.md should carry the get_report_link rule."""
    text = Path("prompts/intelligence.md").read_text(encoding="utf-8")
    assert "get_report_link" in text


def test_real_persona_mentions_source_tweet_citation_rule() -> None:
    """6e: prompts/intelligence.md should explain the source_tweets citation rule."""
    text = Path("prompts/intelligence.md").read_text(encoding="utf-8")
    assert "source_tweets" in text


def test_real_persona_still_parses_after_6e_additions() -> None:
    """6e: adding the new persona blocks must not break the canonical parser."""
    load_intelligence_persona(Path("prompts/intelligence.md"))
