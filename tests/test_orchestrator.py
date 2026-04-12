from __future__ import annotations

import pytest

from project0.errors import RoutingError
from project0.orchestrator import Orchestrator
from project0.store import Store
from project0.telegram_io import FakeBotSender, InboundUpdate


@pytest.fixture()
def orchestrator(store: Store) -> tuple[Orchestrator, FakeBotSender]:
    sender = FakeBotSender()
    orch = Orchestrator(
        store=store,
        sender=sender,
        allowed_chat_ids=frozenset({-100}),
        allowed_user_ids=frozenset({42}),
    )
    return orch, sender


def _update(**overrides) -> InboundUpdate:
    base = dict(
        received_by_bot="manager",
        kind="group",
        chat_id=-100,
        msg_id=1,
        user_id=42,
        text="hello",
    )
    base.update(overrides)
    return InboundUpdate(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_default_manager_on_first_message(
    orchestrator: tuple[Orchestrator, FakeBotSender], store: Store
) -> None:
    orch, sender = orchestrator
    await orch.handle(_update(text="hi"))
    # One outbound reply from manager's bot.
    assert len(sender.sent) == 1
    assert sender.sent[0]["agent"] == "manager"
    assert "[manager-stub]" in sender.sent[0]["text"]  # type: ignore[operator]
    # Focus is now manager.
    assert store.chat_focus().get(-100) == "manager"


@pytest.mark.asyncio
async def test_sticky_focus_after_mention(
    orchestrator: tuple[Orchestrator, FakeBotSender], store: Store
) -> None:
    orch, _sender = orchestrator
    await orch.handle(_update(msg_id=1, text="@intelligence hi"))
    assert store.chat_focus().get(-100) == "intelligence"
    # Follow-up with no @mention.
    await orch.handle(_update(msg_id=2, text="another one"))
    assert store.chat_focus().get(-100) == "intelligence"


@pytest.mark.asyncio
async def test_mention_overrides_focus(
    orchestrator: tuple[Orchestrator, FakeBotSender], store: Store
) -> None:
    orch, _sender = orchestrator
    # Establish intelligence focus.
    await orch.handle(_update(msg_id=1, text="@intelligence hi"))
    # Switch back with explicit mention.
    await orch.handle(_update(msg_id=2, text="@manager switch back"))
    assert store.chat_focus().get(-100) == "manager"


@pytest.mark.asyncio
async def test_dm_path_routes_to_owning_bot(
    orchestrator: tuple[Orchestrator, FakeBotSender], store: Store
) -> None:
    orch, sender = orchestrator
    await orch.handle(_update(kind="dm", received_by_bot="intelligence", text="hi"))
    assert len(sender.sent) == 1
    assert sender.sent[0]["agent"] == "intelligence"
    # DMs do NOT touch group focus.
    assert store.chat_focus().get(-100) is None


@pytest.mark.asyncio
async def test_dedup_same_telegram_message_twice(
    orchestrator: tuple[Orchestrator, FakeBotSender]
) -> None:
    orch, sender = orchestrator
    await orch.handle(_update(msg_id=7, text="hi"))
    await orch.handle(_update(msg_id=7, text="hi"))  # same update seen by second bot
    assert len(sender.sent) == 1  # second invocation is a silent no-op


@pytest.mark.asyncio
async def test_allowlist_rejects_unknown_chat(
    orchestrator: tuple[Orchestrator, FakeBotSender]
) -> None:
    orch, sender = orchestrator
    await orch.handle(_update(chat_id=-999, text="hi"))
    assert sender.sent == []


@pytest.mark.asyncio
async def test_allowlist_rejects_unknown_user(
    orchestrator: tuple[Orchestrator, FakeBotSender]
) -> None:
    orch, sender = orchestrator
    await orch.handle(_update(user_id=999, text="hi"))
    assert sender.sent == []


# --- Delegation tests ---


@pytest.mark.asyncio
async def test_manager_delegation_produces_four_envelopes(
    orchestrator: tuple[Orchestrator, FakeBotSender], store: Store
) -> None:
    orch, sender = orchestrator
    await orch.handle(_update(msg_id=10, text="any news today?"))

    # Two outbound sends: (a) Manager's visible handoff, (b) Intelligence's reply.
    agents_sent_by = [s["agent"] for s in sender.sent]
    assert agents_sent_by == ["manager", "intelligence"]
    assert "@intelligence" in sender.sent[0]["text"]  # type: ignore[operator]
    assert "[intelligence-stub]" in sender.sent[1]["text"]  # type: ignore[operator]

    # Four rows in messages: user → manager, manager → user (handoff),
    # manager → intelligence (internal), intelligence → user (reply).
    rows = store.conn.execute("SELECT * FROM messages ORDER BY id ASC").fetchall()
    assert len(rows) == 4

    user_row, handoff_row, internal_row, intel_reply_row = rows

    assert user_row["from_kind"] == "user"
    assert user_row["to_agent"] == "manager"

    assert handoff_row["from_agent"] == "manager"
    assert handoff_row["to_agent"] == "user"
    assert handoff_row["parent_id"] == user_row["id"]

    assert internal_row["from_agent"] == "manager"
    assert internal_row["to_agent"] == "intelligence"
    assert internal_row["parent_id"] == user_row["id"]
    assert internal_row["source"] == "internal"

    assert intel_reply_row["from_agent"] == "intelligence"
    assert intel_reply_row["to_agent"] == "user"
    assert intel_reply_row["parent_id"] == internal_row["id"]

    # Focus is now intelligence.
    assert store.chat_focus().get(-100) == "intelligence"


@pytest.mark.asyncio
async def test_non_manager_delegation_raises_routing_error(
    orchestrator: tuple[Orchestrator, FakeBotSender], store: Store
) -> None:
    """Delegation authority belongs exclusively to Manager. If Intelligence
    ever returned a delegation, the orchestrator must refuse it."""
    from project0.agents import registry as reg
    from project0.envelope import AgentResult
    from project0.envelope import Envelope as E

    async def rogue_intelligence(env: E) -> AgentResult:
        return AgentResult(
            reply_text=None,
            delegate_to="manager",
            handoff_text="→ bouncing back",
        )

    original = reg.AGENT_REGISTRY["intelligence"]
    reg.AGENT_REGISTRY["intelligence"] = rogue_intelligence
    try:
        orch, _sender = orchestrator
        with pytest.raises(RoutingError, match="only Manager may delegate"):
            await orch.handle(_update(msg_id=11, text="@intelligence ping"))
    finally:
        reg.AGENT_REGISTRY["intelligence"] = original
