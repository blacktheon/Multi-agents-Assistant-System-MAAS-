"""Tests for the Secretary agent. All LLM calls go through FakeProvider."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from project0.envelope import Envelope


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


def _listener_env(chat_id: int, body: str, env_id: int = 10) -> Envelope:
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


@pytest.mark.asyncio
async def test_secretary_listener_skip_does_not_reset_counters(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    mem = store.agent_memory("secretary")

    mem.set("last_reply_at_555", "1970-01-01T00:00:00Z")
    mem.set("msgs_since_reply_555", 10)
    mem.set("chars_since_reply_555", 500)

    llm = FakeProvider(responses=["[skip]"])
    sec = Secretary(
        llm=llm,
        memory=mem,
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )

    result = await sec.handle(_listener_env(chat_id=555, body="next msg"))
    assert result is None
    assert mem.get("msgs_since_reply_555") == 11
    assert mem.get("chars_since_reply_555") >= 500


@pytest.mark.asyncio
async def test_secretary_listener_reply_resets_counters(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    mem = store.agent_memory("secretary")

    mem.set("last_reply_at_666", "1970-01-01T00:00:00Z")
    mem.set("msgs_since_reply_666", 10)
    mem.set("chars_since_reply_666", 500)

    llm = FakeProvider(responses=["嘿你今天这么勤快呢"])
    sec = Secretary(
        llm=llm,
        memory=mem,
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )

    result = await sec.handle(_listener_env(chat_id=666, body="next msg"))
    assert result is not None
    assert result.reply_text == "嘿你今天这么勤快呢"
    assert mem.get("msgs_since_reply_666") == 0
    assert mem.get("chars_since_reply_666") == 0
    last_at = mem.get("last_reply_at_666")
    assert last_at is not None and last_at != "1970-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_secretary_listener_full_width_bracket_skip(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    mem = store.agent_memory("secretary")
    mem.set("last_reply_at_444", "1970-01-01T00:00:00Z")
    mem.set("msgs_since_reply_444", 10)
    mem.set("chars_since_reply_444", 500)

    llm = FakeProvider(responses=["【跳过】"])
    sec = Secretary(
        llm=llm,
        memory=mem,
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )
    result = await sec.handle(_listener_env(chat_id=444, body="msg"))
    assert result is None


@pytest.mark.asyncio
async def test_secretary_listener_loads_transcript_context(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    mem = store.agent_memory("secretary")
    mem.set("last_reply_at_333", "1970-01-01T00:00:00Z")
    mem.set("msgs_since_reply_333", 10)
    mem.set("chars_since_reply_333", 500)

    for i, (from_agent, body) in enumerate([
        (None, "用户说的一段话"),
        ("manager", "manager stub answer"),
        (None, "用户又说一句"),
    ]):
        store.messages().insert(Envelope(
            id=None,
            ts=f"2026-04-13T12:00:{i:02d}Z",
            parent_id=None,
            source="telegram_group",
            telegram_chat_id=333,
            telegram_msg_id=i + 1,
            received_by_bot="manager",
            from_kind="user" if from_agent is None else "agent",
            from_agent=from_agent,
            to_agent="manager" if from_agent is None else "user",
            body=body,
            routing_reason="default_manager" if from_agent is None else "outbound_reply",
        ))

    llm = FakeProvider(responses=["[skip]"])
    sec = Secretary(
        llm=llm,
        memory=mem,
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )
    # Mirror orchestrator behavior: the inbound user envelope is inserted
    # into the messages table BEFORE Secretary is dispatched, so the
    # transcript already contains it as its last line.
    store.messages().insert(Envelope(
        id=None,
        ts="2026-04-13T12:00:99Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=333,
        telegram_msg_id=99,
        received_by_bot="manager",
        from_kind="user",
        from_agent=None,
        to_agent="manager",
        body="newest",
        routing_reason="default_manager",
    ))
    await sec.handle(_listener_env(chat_id=333, body="newest"))
    assert len(llm.calls) == 1
    user_msg = llm.calls[0].messages[0].content
    assert "用户说的一段话" in user_msg
    assert "manager stub answer" in user_msg
    assert "用户又说一句" in user_msg
    # Regression guard: the latest user line must appear exactly once, not
    # twice. (Pre-fix, secretary.py duplicated `user: <body>` at the end of
    # the prompt even though it was already the last transcript line.)
    assert user_msg.count("newest") == 1


@pytest.mark.asyncio
async def test_secretary_mention_path_always_replies(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=["嘿你来啦"])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )
    env = Envelope(
        id=5,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=111,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="user",
        from_agent=None,
        to_agent="secretary",
        body="@secretary 在吗",
        mentions=["secretary"],
        routing_reason="mention",
    )
    result = await sec.handle(env)
    assert result is not None
    assert result.reply_text == "嘿你来啦"
    assert len(llm.calls) == 1
    # Uses group_addressed_mode section.
    assert "ADDRESSED" in llm.calls[0].system


@pytest.mark.asyncio
async def test_secretary_addressed_path_does_not_duplicate_user_line(tmp_path: Path) -> None:
    """Regression: the latest user line should appear exactly once in the
    LLM prompt. The transcript (via recent_for_chat) already contains it."""
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()

    # Pre-seed the chat with one prior user line so the transcript is non-empty.
    store.messages().insert(Envelope(
        id=None, ts="2026-04-13T11:59:00Z", parent_id=None,
        source="telegram_group", telegram_chat_id=111, telegram_msg_id=1,
        received_by_bot="secretary", from_kind="user", from_agent=None,
        to_agent="manager", body="earlier line", routing_reason="default_manager",
    ))
    # The current @mention envelope — this is what the orchestrator would
    # insert and then dispatch to Secretary.
    current = Envelope(
        id=None, ts="2026-04-13T12:00:00Z", parent_id=None,
        source="telegram_group", telegram_chat_id=111, telegram_msg_id=2,
        received_by_bot="secretary", from_kind="user", from_agent=None,
        to_agent="secretary",
        body="unique-marker-xyz",  # unique substring we'll count
        mentions=["secretary"], routing_reason="mention",
    )
    persisted = store.messages().insert(current)
    assert persisted is not None

    llm = FakeProvider(responses=["ok"])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )
    await sec.handle(persisted)
    user_content = llm.calls[0].messages[0].content
    # Regression guard: the unique marker must appear EXACTLY once.
    assert user_content.count("unique-marker-xyz") == 1, user_content


@pytest.mark.asyncio
async def test_secretary_focus_path_uses_addressed_mode(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=["继续聊"])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )
    env = Envelope(
        id=6,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=222,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="user",
        from_agent=None,
        to_agent="secretary",
        body="跟上",
        routing_reason="focus",
    )
    result = await sec.handle(env)
    assert result is not None
    assert result.reply_text == "继续聊"
    assert "ADDRESSED" in llm.calls[0].system


@pytest.mark.asyncio
async def test_secretary_dm_path_uses_dm_mode(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=["私聊里我更大胆"])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )
    env = Envelope(
        id=7,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_dm",
        telegram_chat_id=333,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="user",
        from_agent=None,
        to_agent="secretary",
        body="你今天怎么样",
        routing_reason="direct_dm",
    )
    result = await sec.handle(env)
    assert result is not None
    assert result.reply_text == "私聊里我更大胆"
    assert "DM" in llm.calls[0].system


@pytest.mark.asyncio
async def test_secretary_dm_cooldown_namespace_is_separate_from_group(tmp_path: Path) -> None:
    """Activity in a DM should not affect group listener cooldowns and
    vice versa, because cooldown keys are per-chat_id."""
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    mem = store.agent_memory("secretary")

    # Prime the GROUP cooldown as if it just fired.
    now_iso = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    mem.set("last_reply_at_888", now_iso)
    mem.set("msgs_since_reply_888", 0)
    mem.set("chars_since_reply_888", 0)

    llm = FakeProvider(responses=["DM reply", "DM reply2"])
    sec = Secretary(
        llm=llm, memory=mem, messages_store=store.messages(),
        persona=_build_trivial_persona(), config=_build_trivial_config(),
    )

    # A DM to chat 999 should not see the group cooldown state.
    dm = Envelope(
        id=None, ts="2026-04-13T12:00:00Z", parent_id=None,
        source="telegram_dm", telegram_chat_id=999, telegram_msg_id=1,
        received_by_bot="secretary", from_kind="user", from_agent=None,
        to_agent="secretary", body="hi", routing_reason="direct_dm",
    )
    r = await sec.handle(dm)
    assert r is not None and r.reply_text == "DM reply"
    # Group cooldown for 888 is untouched.
    assert mem.get("msgs_since_reply_888") == 0
    assert mem.get("last_reply_at_888") == now_iso


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
        skip_sentinels=["[skip]", "[跳过]", "【skip】", "【跳过】"],
    )


@pytest.mark.asyncio
async def test_secretary_reminder_path_incorporates_payload(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=["提醒你一下 项目评审 明天下午3点哦 别迟到"])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )

    env = Envelope(
        id=8,
        ts="2026-04-13T12:00:00Z",
        parent_id=1,
        source="internal",
        telegram_chat_id=100,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="secretary",
        body="",  # body empty; all content in payload
        routing_reason="manager_delegation",
        payload={
            "kind": "reminder_request",
            "appointment": "项目评审",
            "when": "明天下午3点",
            "note": "别迟到",
        },
    )
    result = await sec.handle(env)
    assert result is not None
    assert "项目评审" in result.reply_text  # type: ignore[operator]

    # System prompt used reminder_mode section.
    assert "REMINDER" in llm.calls[0].system
    # User prompt included the payload fields.
    user_content = llm.calls[0].messages[0].content
    assert "项目评审" in user_content
    assert "明天下午3点" in user_content
    assert "别迟到" in user_content


@pytest.mark.asyncio
async def test_secretary_reminder_path_without_payload_kind_is_noop(tmp_path: Path) -> None:
    """A manager_delegation envelope without a reminder_request payload
    is ignored (future payload kinds may be handled differently)."""
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=[])
    sec = Secretary(
        llm=llm, memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(), config=_build_trivial_config(),
    )
    env = Envelope(
        id=9, ts="2026-04-13T12:00:00Z", parent_id=None,
        source="internal", telegram_chat_id=100, telegram_msg_id=None,
        received_by_bot=None, from_kind="agent", from_agent="manager",
        to_agent="secretary", body="unknown delegation",
        routing_reason="manager_delegation",
        payload={"kind": "something_else"},
    )
    result = await sec.handle(env)
    assert result is None
    assert len(llm.calls) == 0


@pytest.mark.asyncio
async def test_secretary_reminder_path_handles_none_payload_values(tmp_path: Path) -> None:
    """Manager's JSON output may emit null for optional fields; payload values
    of None must not crash _handle_reminder."""
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=["记得开会哦"])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )
    env = Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="internal",
        telegram_chat_id=None,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="secretary",
        body="",
        routing_reason="manager_delegation",
        payload={
            "kind": "reminder_request",
            "appointment": "开会",
            "when": None,        # explicit null
            "note": None,        # explicit null
        },
    )
    result = await sec.handle(env)
    assert result is not None
    assert result.reply_text == "记得开会哦"
    # User message should include appointment but not null fields.
    user_content = llm.calls[0].messages[0].content
    assert "开会" in user_content
    # Null-handled fields should not emit label lines.
    assert "- 时间:" not in user_content
    assert "- 备注:" not in user_content


@pytest.mark.asyncio
async def test_secretary_cooldown_survives_instance_restart(tmp_path: Path) -> None:
    """Cooldown counters live in agent_memory, so a fresh Secretary
    instance constructed against the same store must read them back."""
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()

    # First instance: reply once to seed a last_reply_at.
    now_iso = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    mem = store.agent_memory("secretary")
    mem.set("last_reply_at_222", now_iso)
    mem.set("msgs_since_reply_222", 0)
    mem.set("chars_since_reply_222", 0)
    del mem

    # Second (fresh) instance reading from the same DB.
    sec = Secretary(
        llm=FakeProvider(responses=[]),
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),  # t_min=60
    )
    # A message comes in right after the recorded reply → t_min blocks it.
    result = await sec.handle(_listener_env(chat_id=222, body="hi"))
    assert result is None
