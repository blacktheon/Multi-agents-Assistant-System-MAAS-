"""Storage layer and the trust boundary for memory isolation.

This module is the ONLY place in the codebase that runs SQL. Agent code,
orchestrator code, and I/O code all go through the typed helper classes
below. The isolation guarantee for private working memory is enforced here
by construction: an `AgentMemory` instance is permanently bound to one
agent_name at construction and has no API to touch other agents' rows.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_memory (
    agent_name   TEXT NOT NULL,
    key          TEXT NOT NULL,
    value_json   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (agent_name, key)
);

CREATE TABLE IF NOT EXISTS blackboard (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    author_agent  TEXT NOT NULL,
    kind          TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_blackboard_created_at ON blackboard(created_at);
CREATE INDEX IF NOT EXISTS ix_blackboard_kind       ON blackboard(kind);

CREATE TABLE IF NOT EXISTS messages (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 TEXT NOT NULL,
    source             TEXT NOT NULL,
    telegram_chat_id   INTEGER,
    telegram_msg_id    INTEGER,
    from_kind          TEXT NOT NULL,
    from_agent         TEXT,
    to_agent           TEXT NOT NULL,
    envelope_json      TEXT NOT NULL,
    parent_id          INTEGER,
    UNIQUE (source, telegram_chat_id, telegram_msg_id)
);
CREATE INDEX IF NOT EXISTS ix_messages_ts       ON messages(ts);
CREATE INDEX IF NOT EXISTS ix_messages_to_agent ON messages(to_agent);
CREATE INDEX IF NOT EXISTS ix_messages_parent   ON messages(parent_id);

CREATE TABLE IF NOT EXISTS chat_focus (
    telegram_chat_id  INTEGER PRIMARY KEY,
    current_agent     TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
"""


class Store:
    """Holds the shared SQLite connection and hands out typed sub-APIs.

    Single connection + asyncio lock is intentional: it makes the multi-bot
    dedup race impossible to mis-order, at the cost of serializing writes.
    One-user scale makes the cost negligible.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        # isolation_level=None => autocommit; we use explicit transactions when needed.
        self._conn = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = asyncio.Lock()

    @property
    def lock(self) -> asyncio.Lock:
        """Async lock guarding all writes. Public so the orchestrator can hold
        it across multi-statement transactions (delegation flow)."""
        return self._lock

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def init_schema(self) -> None:
        self._conn.executescript(SCHEMA_SQL)

    def agent_memory(self, agent_name: str) -> AgentMemory:
        return AgentMemory(self._conn, agent_name)


class AgentMemory:
    """Per-agent private memory. Scoped to one agent at construction; there
    is no API path to touch other agents' rows from an instance.

    DO NOT add methods that accept an agent_name parameter.
    """

    def __init__(self, conn: sqlite3.Connection, agent_name: str) -> None:
        self._conn = conn
        self._agent_name = agent_name

    def get(self, key: str) -> Any | None:
        row = self._conn.execute(
            "SELECT value_json FROM agent_memory WHERE agent_name = ? AND key = ?",
            (self._agent_name, key),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["value_json"])

    def set(self, key: str, value: Any) -> None:
        self._conn.execute(
            """
            INSERT INTO agent_memory (agent_name, key, value_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (agent_name, key)
            DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
            """,
            (self._agent_name, key, json.dumps(value), _utc_now_iso()),
        )

    def delete(self, key: str) -> None:
        self._conn.execute(
            "DELETE FROM agent_memory WHERE agent_name = ? AND key = ?",
            (self._agent_name, key),
        )
