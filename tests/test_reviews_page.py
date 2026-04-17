"""Tests for the /reviews page — renders with and without review rows."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from project0.store import Store, SupervisorReviewRow


@pytest.fixture
def app(tmp_path):
    from project0.control_panel.app import create_app
    from project0.control_panel.supervisor import MAASSupervisor

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    async def _never_spawn():
        raise RuntimeError("should not spawn in tests")
    maas_sup = MAASSupervisor(spawn_fn=_never_spawn)

    app = create_app(
        supervisor=maas_sup,
        store=store,
        project_root=tmp_path,
    )
    return app, store


def test_reviews_page_renders_with_empty_db(app) -> None:
    fastapi_app, _ = app
    client = TestClient(fastapi_app)
    r = client.get("/reviews")
    assert r.status_code == 200
    assert ("no data" in r.text) or ("no reviews yet" in r.text) or ("还没" in r.text)


def test_reviews_page_renders_with_rows(app) -> None:
    fastapi_app, store = app
    rs = store.supervisor_reviews()
    rs.insert(SupervisorReviewRow(
        id=0, ts="2026-04-17T09:00:00Z", agent="manager",
        envelope_id_from=1, envelope_id_to=3, envelope_count=3,
        score_overall=70,
        score_helpfulness=72, score_correctness=68,
        score_tone=75, score_efficiency=65,
        critique_text="Manager 昨日表现一般。",
        recommendations_json="[]",
        trigger="pulse",
    ))
    rs.insert(SupervisorReviewRow(
        id=0, ts="2026-04-17T10:00:00Z", agent="manager",
        envelope_id_from=1, envelope_id_to=5, envelope_count=5,
        score_overall=77,
        score_helpfulness=80, score_correctness=75,
        score_tone=85, score_efficiency=70,
        critique_text="Manager 回应及时。",
        recommendations_json=json.dumps([
            {"target": "prompt", "summary": "更主动", "detail": "可以主动提醒。"},
        ], ensure_ascii=False),
        trigger="pulse",
    ))
    rs.insert(SupervisorReviewRow(
        id=0, ts="2026-04-17T11:00:00Z", agent="intelligence",
        envelope_id_from=1, envelope_id_to=3, envelope_count=3,
        score_overall=62,
        score_helpfulness=60, score_correctness=70,
        score_tone=60, score_efficiency=55,
        critique_text="Intelligence 今天稍显冷淡。",
        recommendations_json="[]",
        trigger="pulse",
    ))
    client = TestClient(fastapi_app)
    r = client.get("/reviews")
    assert r.status_code == 200
    assert "77" in r.text
    assert "62" in r.text
    assert "Manager 回应及时" in r.text
    assert "<polyline" in r.text


def test_reviews_page_listed_in_nav(app) -> None:
    fastapi_app, _ = app
    client = TestClient(fastapi_app)
    r = client.get("/")
    assert 'href="/reviews"' in r.text
