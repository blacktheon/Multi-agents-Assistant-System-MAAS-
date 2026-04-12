"""End-to-end skeleton acceptance test.

Simulates the 'any news today?' flow start to finish (without real
Telegram) and asserts the exact four-envelope tree and outbound sequence.

If this test passes and the 6 unit test files pass, the automated portion
of the acceptance gate (criteria A/B/C) is satisfied. Manual Telegram
smoke tests D.1–D.5 still need to happen — see README.
"""

from __future__ import annotations

import pytest

from project0.orchestrator import Orchestrator
from project0.store import Store
from project0.telegram_io import FakeBotSender, InboundUpdate


@pytest.mark.asyncio
async def test_news_flow_produces_exact_envelope_tree(store: Store) -> None:
    sender = FakeBotSender()
    orch = Orchestrator(
        store=store,
        sender=sender,
        allowed_chat_ids=frozenset({-100123}),
        allowed_user_ids=frozenset({42}),
    )

    # Step 1: user sends 'any news today?' in the group (no @mention).
    await orch.handle(
        InboundUpdate(
            received_by_bot="manager",
            kind="group",
            chat_id=-100123,
            msg_id=1,
            user_id=42,
            text="any news today?",
        )
    )

    # --- outbound dispatch assertions ---
    assert [s["agent"] for s in sender.sent] == ["manager", "intelligence"]
    assert sender.sent[0]["text"] == "→ forwarding to @intelligence"
    assert sender.sent[1]["text"] == "[intelligence-stub] acknowledged: any news today?"

    # --- messages table assertions ---
    rows = list(
        store.conn.execute(
            "SELECT id, source, from_kind, from_agent, to_agent, parent_id "
            "FROM messages ORDER BY id ASC"
        ).fetchall()
    )
    assert len(rows) == 4

    user_row, handoff_row, internal_row, intel_reply_row = rows

    # Envelope #1: user → manager
    assert (user_row["source"], user_row["from_kind"], user_row["to_agent"]) == (
        "telegram_group",
        "user",
        "manager",
    )
    assert user_row["parent_id"] is None

    # Envelope #2: manager → user (visible handoff)
    assert handoff_row["from_agent"] == "manager"
    assert handoff_row["to_agent"] == "user"
    assert handoff_row["source"] == "internal"
    assert handoff_row["parent_id"] == user_row["id"]

    # Envelope #3: manager → intelligence (internal forward)
    assert internal_row["from_agent"] == "manager"
    assert internal_row["to_agent"] == "intelligence"
    assert internal_row["source"] == "internal"
    assert internal_row["parent_id"] == user_row["id"]

    # Envelope #4: intelligence → user
    assert intel_reply_row["from_agent"] == "intelligence"
    assert intel_reply_row["to_agent"] == "user"
    assert intel_reply_row["source"] == "internal"
    assert intel_reply_row["parent_id"] == internal_row["id"]

    # --- focus assertion ---
    assert store.chat_focus().get(-100123) == "intelligence"

    # --- sticky focus: follow-up without @mention routes to Intelligence ---
    sender.sent.clear()
    await orch.handle(
        InboundUpdate(
            received_by_bot="manager",
            kind="group",
            chat_id=-100123,
            msg_id=2,
            user_id=42,
            text="what else?",
        )
    )
    assert len(sender.sent) == 1
    assert sender.sent[0]["agent"] == "intelligence"
    assert "what else?" in sender.sent[0]["text"]  # type: ignore[operator]
