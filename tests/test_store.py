"""Store trust boundary tests.

The single most important thing these tests verify is memory isolation:
an AgentMemory scoped to one agent CANNOT read rows written by another.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from project0.envelope import Envelope
from project0.store import Store, UserFactsWriter


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


def test_messages_recent_for_chat_returns_in_chronological_order(tmp_path: Path) -> None:
    from project0.envelope import Envelope
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()

    def env(msg_id: int, ts: str, body: str, chat_id: int = 500) -> Envelope:
        return Envelope(
            id=None,
            ts=ts,
            parent_id=None,
            source="telegram_group",
            telegram_chat_id=chat_id,
            telegram_msg_id=msg_id,
            received_by_bot="manager",
            from_kind="user",
            from_agent=None,
            to_agent="manager",
            body=body,
            routing_reason="default_manager",
        )

    store.messages().insert(env(1, "2026-04-13T12:00:00Z", "first"))
    store.messages().insert(env(2, "2026-04-13T12:00:05Z", "second"))
    store.messages().insert(env(3, "2026-04-13T12:00:10Z", "third"))
    # Another chat to verify isolation.
    store.messages().insert(env(10, "2026-04-13T12:00:07Z", "other-chat", chat_id=999))

    got = store.messages().recent_for_chat(chat_id=500, limit=10)
    assert [e.body for e in got] == ["first", "second", "third"]


def test_messages_recent_for_chat_respects_limit(tmp_path: Path) -> None:
    from project0.envelope import Envelope
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    for i in range(5):
        store.messages().insert(Envelope(
            id=None,
            ts=f"2026-04-13T12:00:{i:02d}Z",
            parent_id=None,
            source="telegram_group",
            telegram_chat_id=700,
            telegram_msg_id=i + 1,
            received_by_bot="manager",
            from_kind="user",
            from_agent=None,
            to_agent="manager",
            body=f"msg-{i}",
            routing_reason="default_manager",
        ))

    got = store.messages().recent_for_chat(chat_id=700, limit=3)
    assert [e.body for e in got] == ["msg-2", "msg-3", "msg-4"]


def test_messages_recent_for_dm_isolates_by_agent(tmp_path: Path) -> None:
    """Regression: Telegram assigns the same private chat_id (the user's
    user_id) to every 1:1 DM that user has with any bot. A naive
    ``recent_for_chat(chat_id=<user_id>)`` query mixes Intelligence's DM
    transcript with Secretary's. ``recent_for_dm`` must scope by
    ``(chat_id, agent)`` via ``from_agent = ? OR to_agent = ?`` so each
    agent only sees its own DM conversation with the user.

    Reproduces the bug reported in 6d where Secretary started using
    Intelligence-style motion roleplay after the user chatted with
    Intelligence in a DM under the same chat_id."""
    from project0.envelope import Envelope
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()

    USER_ID = 7716133697

    def user_dm(msg_id: int, ts: str, body: str, to_agent: str) -> Envelope:
        return Envelope(
            id=None, ts=ts, parent_id=None,
            source="telegram_dm",
            telegram_chat_id=USER_ID, telegram_msg_id=msg_id,
            received_by_bot=to_agent,
            from_kind="user", from_agent=None, to_agent=to_agent,
            body=body, routing_reason="direct_dm",
        )

    def agent_reply(
        msg_id: int | None, ts: str, body: str, from_agent: str, parent_id: int
    ) -> Envelope:
        return Envelope(
            id=None, ts=ts, parent_id=parent_id,
            source="internal",
            telegram_chat_id=USER_ID, telegram_msg_id=msg_id,
            received_by_bot=None,
            from_kind="agent", from_agent=from_agent, to_agent="user",
            body=body, routing_reason="outbound_reply",
        )

    # Interleaved DM conversation: user DMs intelligence, then secretary,
    # then intelligence again. Every row shares the same telegram_chat_id.
    i1 = store.messages().insert(user_dm(1, "2026-04-14T23:10:00Z", "查情报", "intelligence"))
    assert i1 is not None
    store.messages().insert(agent_reply(None, "2026-04-14T23:10:05Z", "*motion* 主人", "intelligence", i1.id))

    s1 = store.messages().insert(user_dm(2, "2026-04-14T23:12:00Z", "我想你了", "secretary"))
    assert s1 is not None
    store.messages().insert(agent_reply(None, "2026-04-14T23:12:05Z", "宝贝~", "secretary", s1.id))

    i2 = store.messages().insert(user_dm(3, "2026-04-14T23:14:00Z", "继续", "intelligence"))
    assert i2 is not None
    store.messages().insert(agent_reply(None, "2026-04-14T23:14:05Z", "*更多 motion*", "intelligence", i2.id))

    secretary_view = store.messages().recent_for_dm(
        chat_id=USER_ID, agent="secretary", limit=20
    )
    intel_view = store.messages().recent_for_dm(
        chat_id=USER_ID, agent="intelligence", limit=20
    )

    sec_bodies = [e.body for e in secretary_view]
    intel_bodies = [e.body for e in intel_view]

    # Secretary only sees the single user→secretary DM and her own reply.
    assert sec_bodies == ["我想你了", "宝贝~"]
    # No intelligence leakage — no "*motion*" strings in Secretary's view.
    assert not any("motion" in b for b in sec_bodies)

    # Intelligence sees its own two exchanges, not the secretary one.
    assert intel_bodies == ["查情报", "*motion* 主人", "继续", "*更多 motion*"]
    assert "我想你了" not in intel_bodies
    assert "宝贝~" not in intel_bodies

    # Sanity: the old loader still returns everything (this is why the
    # new method was needed in the first place).
    everything = store.messages().recent_for_chat(chat_id=USER_ID, limit=20)
    assert len(everything) == 6


class TestUserFactsWriterExtended:
    """Layer D writer extended for the control panel sub-project.

    See docs/superpowers/specs/2026-04-16-control-panel-design.md §5.
    Authorized authors are {'secretary', 'human'}. 'human' is the only
    author permitted to edit or hard-delete existing facts.
    """

    def _store(self, tmp_path: Path) -> Store:
        s = Store(tmp_path / "s.db")
        s.init_schema()
        return s

    def test_human_writer_can_be_constructed(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        UserFactsWriter("human", s.conn)  # must not raise

    def test_secretary_writer_still_works(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        UserFactsWriter("secretary", s.conn)

    def test_unknown_agent_rejected(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        with pytest.raises(PermissionError):
            UserFactsWriter("manager", s.conn)
        with pytest.raises(PermissionError):
            UserFactsWriter("supervisor", s.conn)

    def test_human_add_sets_author_agent_human(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        w = UserFactsWriter("human", s.conn)
        fact_id = w.add("用户喜欢寿司", topic="food")
        row = s.conn.execute(
            "SELECT author_agent, fact_text, topic, is_active FROM user_facts WHERE id=?",
            (fact_id,),
        ).fetchone()
        assert row["author_agent"] == "human"
        assert row["fact_text"] == "用户喜欢寿司"
        assert row["topic"] == "food"
        assert row["is_active"] == 1

    def test_secretary_add_still_sets_author_agent_secretary(
        self, tmp_path: Path
    ) -> None:
        s = self._store(tmp_path)
        w = UserFactsWriter("secretary", s.conn)
        fact_id = w.add("用户生日是三月十四日")
        row = s.conn.execute(
            "SELECT author_agent FROM user_facts WHERE id=?", (fact_id,)
        ).fetchone()
        assert row["author_agent"] == "secretary"

    def test_reactivate(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        w = UserFactsWriter("human", s.conn)
        fid = w.add("x")
        w.deactivate(fid)
        assert s.conn.execute(
            "SELECT is_active FROM user_facts WHERE id=?", (fid,)
        ).fetchone()[0] == 0
        w.reactivate(fid)
        assert s.conn.execute(
            "SELECT is_active FROM user_facts WHERE id=?", (fid,)
        ).fetchone()[0] == 1

    def test_human_edit_updates_text_and_topic(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        w = UserFactsWriter("human", s.conn)
        fid = w.add("old text", topic="old_topic")
        original_ts = s.conn.execute(
            "SELECT ts FROM user_facts WHERE id=?", (fid,)
        ).fetchone()[0]
        w.edit(fid, "new text", "new_topic")
        row = s.conn.execute(
            "SELECT fact_text, topic, ts, author_agent FROM user_facts WHERE id=?",
            (fid,),
        ).fetchone()
        assert row["fact_text"] == "new text"
        assert row["topic"] == "new_topic"
        # Editing does not rewrite the original ts or author_agent:
        assert row["ts"] == original_ts
        assert row["author_agent"] == "human"

    def test_secretary_edit_raises(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        w_h = UserFactsWriter("human", s.conn)
        fid = w_h.add("x")
        w_s = UserFactsWriter("secretary", s.conn)
        with pytest.raises(PermissionError):
            w_s.edit(fid, "y", None)

    def test_human_delete_removes_row(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        w = UserFactsWriter("human", s.conn)
        fid = w.add("x")
        w.delete(fid)
        row = s.conn.execute(
            "SELECT id FROM user_facts WHERE id=?", (fid,)
        ).fetchone()
        assert row is None

    def test_secretary_delete_raises(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        w_h = UserFactsWriter("human", s.conn)
        fid = w_h.add("x")
        w_s = UserFactsWriter("secretary", s.conn)
        with pytest.raises(PermissionError):
            w_s.delete(fid)
