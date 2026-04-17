"""Tests for the Secretary-history isolation guarantee on
MessagesStore.recent_for_chat — non-Secretary callers never see envelopes
where Secretary is the from_agent or to_agent participant."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from project0.envelope import Envelope
from project0.store import Store


def _mk_env(
    *,
    ts: str,
    from_kind: str,
    from_agent: str | None,
    to_agent: str,
    body: str,
    chat_id: int = 100,
    msg_id: int,
) -> Envelope:
    return Envelope(
        id=None,
        ts=ts,
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=chat_id,
        telegram_msg_id=msg_id,
        received_by_bot=None,
        from_kind=from_kind,  # type: ignore[arg-type]
        from_agent=from_agent,
        to_agent=to_agent,
        body=body,
    )


def _seed(store: Store) -> None:
    """Insert a group-chat transcript that includes both Secretary and
    non-Secretary envelopes."""
    msgs = store.messages()
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    msgs.insert(_mk_env(
        ts=now, from_kind="user", from_agent=None,
        to_agent="manager", body="帮我看看明天的日程", msg_id=1,
    ))
    msgs.insert(_mk_env(
        ts=now, from_kind="agent", from_agent="manager",
        to_agent="user", body="明天下午两点有会议", msg_id=2,
    ))
    msgs.insert(_mk_env(
        ts=now, from_kind="user", from_agent=None,
        to_agent="secretary", body="(listener) user asked manager about schedule",
        msg_id=3,
    ))
    msgs.insert(_mk_env(
        ts=now, from_kind="agent", from_agent="secretary",
        to_agent="user", body="记得多喝水哦", msg_id=4,
    ))


def test_recent_for_chat_requires_visible_to_kwarg(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _seed(store)
    msgs = store.messages()
    with pytest.raises(TypeError):
        msgs.recent_for_chat(chat_id=100, limit=10)  # type: ignore[call-arg]


def test_manager_caller_does_not_see_secretary_envelopes(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _seed(store)
    msgs = store.messages()
    got = msgs.recent_for_chat(chat_id=100, visible_to="manager", limit=10)
    bodies = [e.body for e in got]
    assert "帮我看看明天的日程" in bodies
    assert "明天下午两点有会议" in bodies
    assert "(listener) user asked manager about schedule" not in bodies
    assert "记得多喝水哦" not in bodies


def test_secretary_caller_sees_everything(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _seed(store)
    msgs = store.messages()
    got = msgs.recent_for_chat(chat_id=100, visible_to="secretary", limit=10)
    bodies = [e.body for e in got]
    assert "(listener) user asked manager about schedule" in bodies
    assert "记得多喝水哦" in bodies


def test_intelligence_caller_does_not_see_secretary_envelopes(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _seed(store)
    msgs = store.messages()
    got = msgs.recent_for_chat(chat_id=100, visible_to="intelligence", limit=10)
    for e in got:
        assert e.from_agent != "secretary"
        assert e.to_agent != "secretary"


def test_envelopes_for_review_returns_only_target_agent(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _seed(store)
    msgs = store.messages()
    got = msgs.envelopes_for_review(agent="manager", after_id=0, limit=50)
    bodies = [e.body for e in got]
    assert "帮我看看明天的日程" in bodies    # to_agent=manager
    assert "明天下午两点有会议" in bodies    # from_agent=manager
    assert "(listener) user asked manager about schedule" not in bodies
    assert "记得多喝水哦" not in bodies


def test_envelopes_for_review_respects_after_id(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _seed(store)
    msgs = store.messages()
    all_mgr = msgs.envelopes_for_review(agent="manager", after_id=0, limit=50)
    assert len(all_mgr) >= 2
    mid_id = all_mgr[0].id
    assert mid_id is not None
    got = msgs.envelopes_for_review(agent="manager", after_id=mid_id, limit=50)
    ids = [e.id for e in got]
    for i in ids:
        assert i > mid_id


def test_envelopes_for_review_rejects_secretary(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    msgs = store.messages()
    with pytest.raises(ValueError, match="Secretary"):
        msgs.envelopes_for_review(agent="secretary", after_id=0, limit=50)


def test_envelopes_for_review_rejects_unknown_agent(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    msgs = store.messages()
    with pytest.raises(ValueError, match="unknown reviewable agent"):
        msgs.envelopes_for_review(agent="nobody", after_id=0, limit=50)
