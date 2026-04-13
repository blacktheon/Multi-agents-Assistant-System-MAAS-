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


@pytest.mark.asyncio
async def test_secretary_returns_noop_for_unknown_routing_reason(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.envelope import Envelope
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()

    persona = _build_trivial_persona()
    config = _build_trivial_config()
    llm = FakeProvider(responses=[])  # should not be called

    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=config,
    )

    env = Envelope(
        id=1,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=123,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="user",
        from_agent=None,
        to_agent="secretary",
        body="hi",
        routing_reason="default_manager",  # NOT a reason Secretary handles
    )
    result = await sec.handle(env)
    assert result is None
    assert len(llm.calls) == 0


# Helpers used by many Secretary tests.
@pytest.mark.asyncio
async def test_secretary_listener_cooldown_not_yet_open(tmp_path: Path) -> None:
    """First message into a fresh chat: cooldown counters start at zero and
    default last_reply_at = epoch, so time is elapsed but message count and
    weighted char count are not. No LLM call."""
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=[])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),  # n_min=3, l_min=100
    )

    result = await sec.handle(_listener_env(chat_id=777, body="hi"))
    assert result is None
    assert len(llm.calls) == 0


@pytest.mark.asyncio
async def test_secretary_listener_cooldown_opens_after_thresholds(tmp_path: Path) -> None:
    """Accumulate enough messages and characters so all three thresholds
    cross; the listener path should then call the LLM."""
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=["[skip]", "[skip]", "[skip]", "[skip]"])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),  # n_min=3, l_min=100
    )

    # Three short messages — msgs threshold crosses on the third, but chars
    # may still be under 100.
    for i, body in enumerate(["hey", "yo", "sup"]):
        _ = await sec.handle(_listener_env(chat_id=777, body=body, env_id=i + 1))

    # After three 3-char messages, weighted chars = 9. Below threshold 100.
    assert len(llm.calls) == 0

    # One more message with a longer body pushes chars past 100.
    long_body = "这里有一段比较长的中文消息,足够让加权字符数超过阈值" + "x" * 50
    _ = await sec.handle(_listener_env(chat_id=777, body=long_body, env_id=4))

    # Now all three thresholds are exceeded → LLM called once, response was
    # [skip], so counters are NOT reset.
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_secretary_listener_cooldown_t_min_blocks(tmp_path: Path) -> None:
    """Even if msg and char thresholds are crossed, if t_min has not
    elapsed since the last reply, no LLM call."""
    from datetime import UTC, datetime
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    mem = store.agent_memory("secretary")

    # Record a recent last_reply_at directly.
    now_iso = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    mem.set("last_reply_at_999", now_iso)

    llm = FakeProvider(responses=[])
    sec = Secretary(
        llm=llm,
        memory=mem,
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),  # t_min=60 seconds
    )

    # Push a giant message that would otherwise trip msg+char thresholds.
    giant = "x" * 5000
    result = await sec.handle(_listener_env(chat_id=999, body=giant, env_id=1))
    # msgs_since_reply now 1 (< n_min=3), t since last reply ~0 (< 60)
    assert result is None
    assert len(llm.calls) == 0


def _listener_env(chat_id: int, body: str, env_id: int = 10) -> "Envelope":
    from project0.envelope import Envelope
    return Envelope(
        id=env_id,
        ts="2026-04-13T12:00:00Z",
        parent_id=1,
        source="internal",
        telegram_chat_id=chat_id,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="system",
        from_agent=None,
        to_agent="secretary",
        body=body,
        routing_reason="listener_observation",
    )


def _build_trivial_persona():
    from project0.agents.secretary import SecretaryPersona
    return SecretaryPersona(
        core="CORE",
        listener_mode="LISTENER",
        group_addressed_mode="ADDRESSED",
        dm_mode="DM",
        reminder_mode="REMINDER",
    )


def _build_trivial_config():
    from project0.agents.secretary import SecretaryConfig
    return SecretaryConfig(
        t_min_seconds=60,
        n_min_messages=3,
        l_min_weighted_chars=100,
        transcript_window=20,
        model="claude-sonnet-4-6",
        max_tokens_reply=800,
        max_tokens_listener=400,
        skip_sentinels=["[skip]", "[跳过]"],
    )
