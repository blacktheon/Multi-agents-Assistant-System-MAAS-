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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from project0.envelope import Envelope


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


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
        """Raw connection. Use only for multi-statement transactions that cannot
        be expressed through the typed sub-APIs. Caller must hold ``self.lock``
        for any write. Reading across agent_memory rows here bypasses the
        isolation guarantee enforced by ``AgentMemory``."""
        return self._conn

    def init_schema(self) -> None:
        self._conn.executescript(SCHEMA_SQL)
        self._run_additive_migrations()

    def _run_additive_migrations(self) -> None:
        """Idempotent ALTER TABLE helpers. SQLite lacks 'ADD COLUMN IF NOT
        EXISTS', so we catch OperationalError on duplicate-column errors."""
        import sqlite3 as _sqlite
        additive_columns: list[tuple[str, str, str]] = [
            ("messages", "payload_json", "TEXT"),
        ]
        for table, col, coltype in additive_columns:
            try:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"
                )
            except _sqlite.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise

    def agent_memory(self, agent_name: str) -> AgentMemory:
        return AgentMemory(self._conn, agent_name)

    def blackboard(self) -> Blackboard:
        return Blackboard(self._conn)

    def messages(self) -> MessagesStore:
        return MessagesStore(self._conn)

    def chat_focus(self) -> ChatFocusStore:
        return ChatFocusStore(self._conn)


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


class Blackboard:
    """Append-only shared collaboration surface. All agents can read; all
    agents can append. No update, no delete. The `author_agent` is passed
    by the orchestrator from the currently-running agent's identity, so
    agents cannot spoof each other.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(self, author: str, kind: str, payload: dict[str, Any]) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO blackboard (author_agent, kind, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (author, kind, json.dumps(payload), _utc_now_iso()),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def recent(self, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        if kind is None:
            rows = self._conn.execute(
                "SELECT id, author_agent, kind, payload_json, created_at "
                "FROM blackboard ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, author_agent, kind, payload_json, created_at "
                "FROM blackboard WHERE kind = ? ORDER BY id DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "author_agent": r["author_agent"],
                "kind": r["kind"],
                "payload": json.loads(r["payload_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]


class MessagesStore:
    """Append-only envelope log with SQL-level dedup on Telegram msg ids.

    First-writer-wins: when multiple bots poll the same group, each sees
    the same Telegram update and tries to insert. The UNIQUE constraint
    rejects the loser, which `insert()` reports as None.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, env: Envelope) -> Envelope | None:
        try:
            cur = self._conn.execute(
                """
                INSERT INTO messages (
                    ts, source, telegram_chat_id, telegram_msg_id,
                    from_kind, from_agent, to_agent, envelope_json, parent_id,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    env.ts,
                    env.source,
                    env.telegram_chat_id,
                    env.telegram_msg_id,
                    env.from_kind,
                    env.from_agent,
                    env.to_agent,
                    env.to_json(),
                    env.parent_id,
                    json.dumps(env.payload) if env.payload is not None else None,
                ),
            )
        except sqlite3.IntegrityError:
            return None

        assert cur.lastrowid is not None
        stored = Envelope.from_json(env.to_json())
        stored.id = cur.lastrowid
        return stored

    def fetch_children(self, parent_id: int) -> list[Envelope]:
        rows = self._conn.execute(
            "SELECT id, envelope_json FROM messages WHERE parent_id = ? ORDER BY id ASC",
            (parent_id,),
        ).fetchall()
        result = []
        for r in rows:
            env = Envelope.from_json(r["envelope_json"])
            env.id = r["id"]
            result.append(env)
        return result

    def recent_for_chat(self, *, chat_id: int, limit: int) -> list[Envelope]:
        """Return the most recent envelopes for a single Telegram chat,
        oldest-first. Used by agents loading transcript context."""
        rows = self._conn.execute(
            """
            SELECT id, envelope_json FROM messages
            WHERE telegram_chat_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (chat_id, limit),
        ).fetchall()
        result: list[Envelope] = []
        for r in rows:
            env = Envelope.from_json(r["envelope_json"])
            env.id = r["id"]
            result.append(env)
        result.reverse()
        return result

    def has_recent_user_text_in_group(
        self, *, chat_id: int, body: str, within_seconds: int
    ) -> bool:
        """Content-based dedup gate for multi-bot groups.

        In a Telegram group with multiple bot members, sending one user
        message can produce two physically distinct messages with sequential
        ``message_id`` values — one delivered to each bot's update queue.
        The UNIQUE constraint on ``telegram_msg_id`` cannot catch this
        because the ids are legitimately different. This method lets the
        orchestrator dedup on ``(chat_id, body)`` within a short time
        window before inserting.
        """
        cutoff_iso = (
            datetime.now(UTC) - timedelta(seconds=within_seconds)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        row = self._conn.execute(
            """
            SELECT id FROM messages
            WHERE source = 'telegram_group'
              AND telegram_chat_id = ?
              AND from_kind = 'user'
              AND ts >= ?
              AND json_extract(envelope_json, '$.body') = ?
            LIMIT 1
            """,
            (chat_id, cutoff_iso, body),
        ).fetchone()
        return row is not None


class ChatFocusStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, chat_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT current_agent FROM chat_focus WHERE telegram_chat_id = ?",
            (chat_id,),
        ).fetchone()
        return None if row is None else row["current_agent"]

    def set(self, chat_id: int, agent: str) -> None:
        self._conn.execute(
            """
            INSERT INTO chat_focus (telegram_chat_id, current_agent, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (telegram_chat_id)
            DO UPDATE SET current_agent = excluded.current_agent,
                          updated_at    = excluded.updated_at
            """,
            (chat_id, agent, _utc_now_iso()),
        )

    def clear_all(self) -> None:
        """Wipe every chat's focus. Called once at process startup so each
        restart begins with Manager as the default route for every group,
        regardless of where the previous process left things."""
        self._conn.execute("DELETE FROM chat_focus")
