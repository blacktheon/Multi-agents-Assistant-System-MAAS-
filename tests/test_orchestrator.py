from __future__ import annotations

import pytest

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
