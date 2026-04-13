"""Store trust boundary tests.

The single most important thing these tests verify is memory isolation:
an AgentMemory scoped to one agent CANNOT read rows written by another.
"""

from __future__ import annotations

from pathlib import Path

from project0.envelope import Envelope
from project0.store import Store


def test_store_init_schema_is_idempotent(store: Store) -> None:
    store.init_schema()  # second call must not raise
    store.init_schema()


def test_agent_memory_set_and_get(store: Store) -> None:
    mem = store.agent_memory("manager")
    mem.set("last_seen", {"ts": "2026-04-13T12:00:00Z"})
    assert mem.get("last_seen") == {"ts": "2026-04-13T12:00:00Z"}


def test_agent_memory_get_missing_returns_none(store: Store) -> None:
    mem = store.agent_memory("manager")
    assert mem.get("nothing") is None


def test_agent_memory_delete(store: Store) -> None:
    mem = store.agent_memory("manager")
    mem.set("x", 1)
    mem.delete("x")
    assert mem.get("x") is None


def test_agent_memory_isolation_between_agents(store: Store) -> None:
    """Manager's AgentMemory must not see Intelligence's rows, and vice versa."""
    manager_mem = store.agent_memory("manager")
    intel_mem = store.agent_memory("intelligence")

    manager_mem.set("secret", "manager-only")
    intel_mem.set("secret", "intelligence-only")

    assert manager_mem.get("secret") == "manager-only"
    assert intel_mem.get("secret") == "intelligence-only"


def test_agent_memory_has_no_cross_agent_api(store: Store) -> None:
    """Regression guard: AgentMemory must not expose any method that accepts
    an agent name parameter, because that would let a caller pivot scope."""
    mem = store.agent_memory("manager")
    public_methods = [m for m in dir(mem) if not m.startswith("_")]
    # Whitelisted public surface. Anything else on AgentMemory is a red flag.
    assert set(public_methods) == {"get", "set", "delete"}


# --- Blackboard tests ---


def test_blackboard_append_returns_id(store: Store) -> None:
    bb = store.blackboard()
    row_id = bb.append("manager", "task_summary", {"task": "demo"})
    assert isinstance(row_id, int)
    assert row_id >= 1


def test_blackboard_recent_returns_appended(store: Store) -> None:
    bb = store.blackboard()
    bb.append("manager", "task_summary", {"n": 1})
    bb.append("intelligence", "handoff_note", {"n": 2})
    rows = bb.recent(limit=10)
    assert len(rows) == 2
    # Most recent first.
    assert rows[0]["payload"]["n"] == 2
    assert rows[0]["author_agent"] == "intelligence"
    assert rows[1]["author_agent"] == "manager"


def test_blackboard_recent_filters_by_kind(store: Store) -> None:
    bb = store.blackboard()
    bb.append("manager", "task_summary", {"n": 1})
    bb.append("manager", "handoff_note", {"n": 2})
    rows = bb.recent(kind="task_summary")
    assert len(rows) == 1
    assert rows[0]["kind"] == "task_summary"


# --- MessagesStore tests ---


def _user_env(chat_id: int, msg_id: int, body: str) -> Envelope:
    return Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=chat_id,
        telegram_msg_id=msg_id,
        received_by_bot="manager",
        from_kind="user",
        from_agent=None,
        to_agent="manager",
        body=body,
        mentions=[],
        routing_reason="default_manager",
    )


def test_messages_insert_assigns_id(store: Store) -> None:
    msgs = store.messages()
    env = _user_env(-100, 1, "hi")
    inserted = msgs.insert(env)
    assert inserted.id is not None
    assert inserted.id >= 1


def test_messages_dedup_by_telegram_ids(store: Store) -> None:
    msgs = store.messages()
    env1 = _user_env(-100, 1, "hi")
    env2 = _user_env(-100, 1, "hi")  # same (source, chat_id, msg_id)
    first = msgs.insert(env1)
    second = msgs.insert(env2)
    assert first.id is not None
    assert second is None  # dedup signaled by None return


def test_messages_internal_source_not_deduped(store: Store) -> None:
    """Internal envelopes have no telegram ids; each insert must succeed."""
    msgs = store.messages()
    env1 = _user_env(-100, 1, "hi")
    msgs.insert(env1)
    internal = Envelope(
        id=None,
        ts="2026-04-13T12:00:01Z",
        parent_id=1,
        source="internal",
        telegram_chat_id=None,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="intelligence",
        body="hi",
        mentions=[],
        routing_reason="manager_delegation",
    )
    a = msgs.insert(internal)
    b = msgs.insert(internal)
    assert a is not None and b is not None
    assert a.id != b.id


def test_messages_fetch_children(store: Store) -> None:
    msgs = store.messages()
    parent = msgs.insert(_user_env(-100, 1, "any news today?"))
    assert parent is not None and parent.id is not None

    for to, reason in [("user", "outbound_reply"), ("intelligence", "manager_delegation")]:
        child = Envelope(
            id=None,
            ts="2026-04-13T12:00:01Z",
            parent_id=parent.id,
            source="internal",
            telegram_chat_id=None,
            telegram_msg_id=None,
            received_by_bot=None,
            from_kind="agent",
            from_agent="manager",
            to_agent=to,
            body="...",
            mentions=[],
            routing_reason=reason,  # type: ignore[arg-type]
        )
        msgs.insert(child)

    children = msgs.fetch_children(parent.id)
    assert len(children) == 2
    assert {c.to_agent for c in children} == {"user", "intelligence"}


def test_messages_chat_focus_default(store: Store) -> None:
    focus = store.chat_focus()
    assert focus.get(-100) is None
    focus.set(-100, "manager")
    assert focus.get(-100) == "manager"
    focus.set(-100, "intelligence")
    assert focus.get(-100) == "intelligence"


def _fresh_user_env(chat_id: int, msg_id: int, body: str) -> Envelope:
    """Like _user_env but with the current UTC ts so recency-window queries hit."""
    from datetime import UTC, datetime

    now_iso = (
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    env = _user_env(chat_id, msg_id, body)
    env.ts = now_iso
    return env


def test_has_recent_user_text_in_group_finds_match(store: Store) -> None:
    msgs = store.messages()
    msgs.insert(_fresh_user_env(-100, 1, "diag-zzz"))
    assert msgs.has_recent_user_text_in_group(
        chat_id=-100, body="diag-zzz", within_seconds=5
    ) is True


def test_has_recent_user_text_in_group_distinguishes_text(store: Store) -> None:
    msgs = store.messages()
    msgs.insert(_fresh_user_env(-100, 1, "hello"))
    assert msgs.has_recent_user_text_in_group(
        chat_id=-100, body="goodbye", within_seconds=5
    ) is False


def test_has_recent_user_text_in_group_distinguishes_chat(store: Store) -> None:
    msgs = store.messages()
    msgs.insert(_fresh_user_env(-100, 1, "hi"))
    assert msgs.has_recent_user_text_in_group(
        chat_id=-200, body="hi", within_seconds=5
    ) is False


def test_has_recent_user_text_in_group_ignores_old_rows(store: Store) -> None:
    """A row older than the window must NOT count as a duplicate."""
    msgs = store.messages()
    # Insert with an explicit ts in the past.
    old = Envelope(
        id=None,
        ts="2020-01-01T00:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=-100,
        telegram_msg_id=1,
        received_by_bot="manager",
        from_kind="user",
        from_agent=None,
        to_agent="manager",
        body="old message",
        mentions=[],
        routing_reason="default_manager",
    )
    msgs.insert(old)
    assert msgs.has_recent_user_text_in_group(
        chat_id=-100, body="old message", within_seconds=5
    ) is False


def test_has_recent_user_text_in_group_ignores_internal_rows(store: Store) -> None:
    """Internal envelopes (bot replies, handoffs) must not be matched."""
    msgs = store.messages()
    bot_reply = Envelope(
        id=None,
        ts=_user_env(-100, 1, "x").ts,  # use same stale-but-recent format
        parent_id=None,
        source="internal",
        telegram_chat_id=-100,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="user",
        body="canary body",
        mentions=[],
        routing_reason="outbound_reply",
    )
    msgs.insert(bot_reply)
    assert msgs.has_recent_user_text_in_group(
        chat_id=-100, body="canary body", within_seconds=99999
    ) is False


def test_messages_insert_persists_payload(tmp_path: Path) -> None:
    from project0.envelope import Envelope
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()

    env = Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="internal",
        telegram_chat_id=100,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="secretary",
        body="reminder body",
        routing_reason="manager_delegation",
        payload={"kind": "reminder_request", "appointment": "项目评审"},
    )
    persisted = store.messages().insert(env)
    assert persisted is not None
    assert persisted.payload == {"kind": "reminder_request", "appointment": "项目评审"}

    # Round-trip via fetch_children from a freshly inserted parent.
    parent = Envelope(
        id=None,
        ts="2026-04-13T11:59:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=100,
        telegram_msg_id=1,
        received_by_bot="manager",
        from_kind="user",
        from_agent=None,
        to_agent="manager",
        body="anchor",
        routing_reason="default_manager",
    )
    parent_persisted = store.messages().insert(parent)
    assert parent_persisted is not None

    child = Envelope(
        id=None,
        ts="2026-04-13T12:00:01Z",
        parent_id=parent_persisted.id,
        source="internal",
        telegram_chat_id=100,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="secretary",
        body="child with payload",
        routing_reason="manager_delegation",
        payload={"kind": "reminder_request", "when": "明天"},
    )
    store.messages().insert(child)
    children = store.messages().fetch_children(parent_persisted.id or 0)
    assert len(children) == 1
    assert children[0].payload == {"kind": "reminder_request", "when": "明天"}


def test_messages_insert_null_payload_works(tmp_path: Path) -> None:
    from project0.envelope import Envelope
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    env = Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=100,
        telegram_msg_id=2,
        received_by_bot="manager",
        from_kind="user",
        from_agent=None,
        to_agent="manager",
        body="no payload",
        routing_reason="default_manager",
    )
    persisted = store.messages().insert(env)
    assert persisted is not None
    assert persisted.payload is None
