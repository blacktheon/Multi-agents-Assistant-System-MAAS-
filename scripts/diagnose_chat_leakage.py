"""Diagnostic for Secretary/Intelligence transcript leakage.

Dumps the last N envelopes grouped by telegram_chat_id, showing what
each agent would see when it loads transcript context. Run this right
after you reproduce the leakage (chat with Intelligence in the group,
then DM Secretary and see her use motion descriptions).

Usage:
    uv run python scripts/diagnose_chat_leakage.py [limit_per_chat]

Default limit_per_chat is 15. Reads from data/store.db by default
(override via STORE_PATH env var).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

STORE_PATH = os.environ.get("STORE_PATH", "data/store.db")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 15


def main() -> None:
    if not Path(STORE_PATH).exists():
        sys.exit(f"store not found at {STORE_PATH}")

    conn = sqlite3.connect(STORE_PATH)
    conn.row_factory = sqlite3.Row

    # Distinct chat_ids with their latest activity.
    chats = conn.execute(
        """
        SELECT
            telegram_chat_id AS chat_id,
            COUNT(*) AS n,
            MAX(id) AS last_id,
            MAX(ts) AS last_ts
        FROM messages
        WHERE telegram_chat_id IS NOT NULL
        GROUP BY telegram_chat_id
        ORDER BY last_id DESC
        """
    ).fetchall()

    print(f"=== store: {STORE_PATH} ===")
    print(f"distinct chats with telegram_chat_id: {len(chats)}")
    for c in chats:
        print(f"  chat_id={c['chat_id']}  msgs={c['n']}  latest={c['last_ts']}")
    print()

    for c in chats:
        chat_id = c["chat_id"]
        print(f"=== last {LIMIT} envelopes in chat_id={chat_id} ===")
        rows = conn.execute(
            """
            SELECT id, ts, envelope_json
            FROM messages
            WHERE telegram_chat_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (chat_id, LIMIT),
        ).fetchall()
        for r in rows:
            env = json.loads(r["envelope_json"])
            source = env.get("source", "?")
            from_kind = env.get("from_kind", "?")
            from_agent = env.get("from_agent") or ""
            to_agent = env.get("to_agent") or ""
            routing = env.get("routing_reason", "?")
            body = (env.get("body") or "").replace("\n", " ")
            body_short = body[:80] + ("..." if len(body) > 80 else "")
            speaker = f"{from_kind}:{from_agent}" if from_agent else from_kind
            print(
                f"  #{r['id']:4d}  {r['ts'][:19]}  src={source:14s}  "
                f"{speaker:16s} -> {to_agent:12s} ({routing:20s})  {body_short}"
            )
        print()


if __name__ == "__main__":
    main()
