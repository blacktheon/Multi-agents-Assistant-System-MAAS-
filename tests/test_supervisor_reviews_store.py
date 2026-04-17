"""Tests for supervisor_reviews table + SupervisorReviewsStore."""
from __future__ import annotations

import json

from project0.store import Store, SupervisorReviewRow


def _row(
    *,
    ts: str = "2026-04-17T10:00:00Z",
    agent: str = "manager",
    envelope_id_from: int = 1,
    envelope_id_to: int = 10,
    envelope_count: int = 9,
    score_overall: int = 78,
    score_helpfulness: int = 80,
    score_correctness: int = 75,
    score_tone: int = 85,
    score_efficiency: int = 70,
    critique_text: str = "整体表现不错,回应及时。",
    recommendations: list[dict] | None = None,
    trigger: str = "pulse",
) -> SupervisorReviewRow:
    recs = recommendations if recommendations is not None else []
    return SupervisorReviewRow(
        id=0,
        ts=ts,
        agent=agent,
        envelope_id_from=envelope_id_from,
        envelope_id_to=envelope_id_to,
        envelope_count=envelope_count,
        score_overall=score_overall,
        score_helpfulness=score_helpfulness,
        score_correctness=score_correctness,
        score_tone=score_tone,
        score_efficiency=score_efficiency,
        critique_text=critique_text,
        recommendations_json=json.dumps(recs, ensure_ascii=False),
        trigger=trigger,
    )


def test_schema_creates_table(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    cur = store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='supervisor_reviews'"
    )
    assert cur.fetchone() is not None


def test_insert_and_latest_for_agent(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    rs = store.supervisor_reviews()

    new_id = rs.insert(_row(ts="2026-04-17T10:00:00Z", agent="manager", score_overall=70))
    assert new_id > 0

    latest = rs.latest_for_agent("manager")
    assert latest is not None
    assert latest.agent == "manager"
    assert latest.score_overall == 70
    assert latest.critique_text == "整体表现不错,回应及时。"


def test_latest_for_agent_returns_most_recent(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    rs = store.supervisor_reviews()

    rs.insert(_row(ts="2026-04-17T08:00:00Z", agent="manager",
                   envelope_id_to=10, score_overall=60))
    rs.insert(_row(ts="2026-04-17T11:00:00Z", agent="manager",
                   envelope_id_to=20, score_overall=88))
    rs.insert(_row(ts="2026-04-17T10:00:00Z", agent="intelligence",
                   envelope_id_to=10, score_overall=55))

    latest_mgr = rs.latest_for_agent("manager")
    assert latest_mgr is not None
    assert latest_mgr.score_overall == 88
    latest_intel = rs.latest_for_agent("intelligence")
    assert latest_intel is not None
    assert latest_intel.score_overall == 55


def test_latest_for_agent_none_when_empty(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    rs = store.supervisor_reviews()
    assert rs.latest_for_agent("manager") is None


def test_recent_for_agent_respects_limit_and_order(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    rs = store.supervisor_reviews()
    for i in range(5):
        rs.insert(_row(
            ts=f"2026-04-17T{10+i:02d}:00:00Z",
            agent="manager",
            envelope_id_to=10 + i * 10,
            score_overall=60 + i,
        ))
    got = rs.recent_for_agent("manager", limit=3)
    assert [r.score_overall for r in got] == [64, 63, 62]  # newest-first


def test_history_spark_returns_tuples_oldest_first(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    rs = store.supervisor_reviews()
    for i in range(4):
        rs.insert(_row(
            ts=f"2026-04-17T{10+i:02d}:00:00Z",
            agent="learning",
            envelope_id_to=10 + i * 10,
            score_overall=70 + i,
        ))
    pairs = rs.history_spark(agent="learning", limit=10)
    assert len(pairs) == 4
    assert pairs[0][1] == 70
    assert pairs[-1][1] == 73


def test_all_recent(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    rs = store.supervisor_reviews()
    rs.insert(_row(ts="2026-04-17T09:00:00Z", agent="manager",
                   envelope_id_to=10, score_overall=60))
    rs.insert(_row(ts="2026-04-17T10:00:00Z", agent="intelligence",
                   envelope_id_to=10, score_overall=70))
    rs.insert(_row(ts="2026-04-17T11:00:00Z", agent="learning",
                   envelope_id_to=10, score_overall=80))
    got = rs.all_recent(limit=50)
    assert len(got) == 3
    assert got[0].agent == "learning"


def test_insert_is_idempotent_on_envelope_id_to(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    rs = store.supervisor_reviews()

    first_id = rs.insert(_row(agent="manager", envelope_id_to=50, score_overall=70))
    # Same agent, same envelope_id_to — idempotent, returns existing id.
    second_id = rs.insert(_row(agent="manager", envelope_id_to=50, score_overall=99))
    assert second_id == first_id

    # Same agent, LOWER envelope_id_to — NOT a duplicate; legitimate new row.
    # This covers scenarios like an on-demand review of a historical window or
    # a cursor-corruption recovery — surface it rather than hiding it.
    third_id = rs.insert(_row(agent="manager", envelope_id_to=40, score_overall=99))
    assert third_id != first_id

    # Same agent, HIGHER envelope_id_to — new insert.
    fourth_id = rs.insert(_row(agent="manager", envelope_id_to=60, score_overall=50))
    assert fourth_id != first_id
    assert fourth_id != third_id

    # Different agent, same envelope_id_to — new insert.
    fifth_id = rs.insert(_row(agent="intelligence", envelope_id_to=50, score_overall=50))
    assert fifth_id != first_id
