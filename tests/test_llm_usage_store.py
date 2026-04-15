from __future__ import annotations

import sqlite3

import pytest

from project0.store import LLMUsageStore, Store


@pytest.fixture
def conn() -> sqlite3.Connection:
    s = Store(":memory:")
    s.init_schema()
    yield s.conn
    s.conn.close()


def test_record_and_read_back(conn: sqlite3.Connection) -> None:
    usage = LLMUsageStore(conn)
    row_id = usage.record(
        agent="secretary",
        model="claude-sonnet-4-6",
        input_tokens=1234,
        cache_creation_input_tokens=500,
        cache_read_input_tokens=700,
        output_tokens=210,
        envelope_id=42,
        purpose="reply",
    )
    assert row_id > 0
    rows = conn.execute(
        "SELECT agent, model, input_tokens, cache_creation_input_tokens, "
        "cache_read_input_tokens, output_tokens, envelope_id, purpose "
        "FROM llm_usage ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("secretary", "claude-sonnet-4-6", 1234, 500, 700, 210, 42, "reply")
    ]


def test_record_with_null_envelope(conn: sqlite3.Connection) -> None:
    usage = LLMUsageStore(conn)
    usage.record(
        agent="intelligence_summarizer",
        model="claude-opus-4-6",
        input_tokens=5000,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens=1200,
        envelope_id=None,
        purpose="report_gen",
    )
    row = conn.execute(
        "SELECT envelope_id FROM llm_usage WHERE agent='intelligence_summarizer'"
    ).fetchone()
    assert row[0] is None


def test_summary_since_groups_by_agent(conn: sqlite3.Connection) -> None:
    usage = LLMUsageStore(conn)
    for _ in range(3):
        usage.record(
            agent="secretary",
            model="claude-sonnet-4-6",
            input_tokens=100,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=50,
            output_tokens=20,
            envelope_id=None,
            purpose="reply",
        )
    for _ in range(2):
        usage.record(
            agent="manager",
            model="claude-sonnet-4-6",
            input_tokens=500,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=300,
            output_tokens=80,
            envelope_id=None,
            purpose="tool_loop",
        )
    rollup = usage.summary_since("1970-01-01T00:00:00Z")
    rows_by_agent = {r["agent"]: r for r in rollup}
    assert rows_by_agent["secretary"]["input_tokens"] == 300
    assert rows_by_agent["secretary"]["output_tokens"] == 60
    assert rows_by_agent["manager"]["input_tokens"] == 1000
