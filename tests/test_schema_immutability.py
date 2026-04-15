"""Schema immutability (Task 17 / spec §B.4).

Lock the schemas of the four pre-existing tables (messages, agent_memory,
blackboard, chat_focus). Any accidental drift from this sub-project fails
the test. New tables added in this sub-project (user_facts, llm_usage) are
intentionally NOT locked here — they are allowed to evolve until they
reach steady state.
"""
from __future__ import annotations

import sqlite3

from project0.store import Store

# PRAGMA table_info rows → (name, type, notnull, pk) for each column,
# ordered by column index (cid).
EXPECTED_SCHEMA: dict[str, list[tuple[str, str, int, int]]] = {
    "messages": [
        ("id", "INTEGER", 0, 1),
        ("ts", "TEXT", 1, 0),
        ("source", "TEXT", 1, 0),
        ("telegram_chat_id", "INTEGER", 0, 0),
        ("telegram_msg_id", "INTEGER", 0, 0),
        ("from_kind", "TEXT", 1, 0),
        ("from_agent", "TEXT", 0, 0),
        ("to_agent", "TEXT", 1, 0),
        ("envelope_json", "TEXT", 1, 0),
        ("parent_id", "INTEGER", 0, 0),
        ("payload_json", "TEXT", 0, 0),
    ],
    "agent_memory": [
        ("agent_name", "TEXT", 1, 1),
        ("key", "TEXT", 1, 2),
        ("value_json", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ],
    "blackboard": [
        ("id", "INTEGER", 0, 1),
        ("author_agent", "TEXT", 1, 0),
        ("kind", "TEXT", 1, 0),
        ("payload_json", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
    ],
    "chat_focus": [
        ("telegram_chat_id", "INTEGER", 0, 1),
        ("current_agent", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ],
}


def _fresh_conn() -> sqlite3.Connection:
    store = Store(":memory:")
    store.init_schema()
    return store.conn


def _schema_tuples(
    conn: sqlite3.Connection, table: str
) -> list[tuple[str, str, int, int]]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [(r[1], r[2], r[3], r[5]) for r in rows]


def test_messages_schema_unchanged() -> None:
    assert _schema_tuples(_fresh_conn(), "messages") == EXPECTED_SCHEMA["messages"]


def test_agent_memory_schema_unchanged() -> None:
    assert (
        _schema_tuples(_fresh_conn(), "agent_memory") == EXPECTED_SCHEMA["agent_memory"]
    )


def test_blackboard_schema_unchanged() -> None:
    assert _schema_tuples(_fresh_conn(), "blackboard") == EXPECTED_SCHEMA["blackboard"]


def test_chat_focus_schema_unchanged() -> None:
    assert _schema_tuples(_fresh_conn(), "chat_focus") == EXPECTED_SCHEMA["chat_focus"]
