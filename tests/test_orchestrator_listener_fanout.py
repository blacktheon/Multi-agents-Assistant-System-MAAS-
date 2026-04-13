"""Tests for the listener fan-out behavior in the orchestrator.

Secretary (and later Supervisor) registers as a listener: every group
message is fanned out to every listener whose name is not the focus target.
Listener replies go through the listener's own bot and become children of
the listener_observation envelope, not the original user message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from project0.agents.registry import AGENT_REGISTRY, LISTENER_REGISTRY
from project0.envelope import AgentResult, Envelope
from project0.errors import RoutingError
from project0.orchestrator import Orchestrator
from project0.store import Store
from project0.telegram_io import InboundUpdate


@dataclass
class _RecordingSender:
    sent: list[tuple[str, int, str]] = field(default_factory=list)

    async def send(self, *, agent: str, chat_id: int, text: str) -> None:
        self.sent.append((agent, chat_id, text))


def _install_fake_secretary(
    handler: Callable[[Envelope], Awaitable[AgentResult | None]],
) -> None:
    """Install a test listener under the name 'secretary'. Must be undone
    in a teardown."""
    async def agent_wrapper(env: Envelope) -> AgentResult:
        result = await handler(env)
        if result is None:
            return AgentResult(
                reply_text="(fallback)", delegate_to=None, handoff_text=None
            )
        return result

    AGENT_REGISTRY["secretary"] = agent_wrapper
    LISTENER_REGISTRY["secretary"] = handler


def _uninstall_fake_secretary() -> None:
    AGENT_REGISTRY.pop("secretary", None)
    LISTENER_REGISTRY.pop("secretary", None)


@pytest.fixture
def fake_secretary_slot():
    try:
        yield
    finally:
        _uninstall_fake_secretary()


@pytest.mark.asyncio
async def test_group_message_fans_out_to_secretary_listener(
    tmp_path: Path, fake_secretary_slot
) -> None:
    store = Store(tmp_path / "t.db")
    store.init_schema()

    observations: list[Envelope] = []

    async def fake_listener(env: Envelope) -> AgentResult | None:
        observations.append(env)
        return None  # observed but silent

    _install_fake_secretary(fake_listener)

    sender = _RecordingSender()
    orch = Orchestrator(
        store=store,
        sender=sender,
        allowed_chat_ids=frozenset({-1001}),
        allowed_user_ids=frozenset({42}),
    )

    update = InboundUpdate(
        kind="group",
        chat_id=-1001,
        msg_id=1,
        user_id=42,
        text="hello all",
        received_by_bot="manager",
    )
    await orch.handle(update)

    # Listener saw the message.
    assert len(observations) == 1
    obs = observations[0]
    assert obs.routing_reason == "listener_observation"
    assert obs.source == "internal"
    assert obs.to_agent == "secretary"
    assert obs.from_kind == "system"
    assert obs.body == "hello all"
    assert obs.parent_id is not None  # links to the original user msg

    # Sender: manager_stub sent its reply; listener stayed silent.
    assert any(a == "manager" for a, _, _ in sender.sent)
    assert not any(a == "secretary" for a, _, _ in sender.sent)


@pytest.mark.asyncio
async def test_dm_does_not_fan_out_to_listeners(
    tmp_path: Path, fake_secretary_slot
) -> None:
    store = Store(tmp_path / "t.db")
    store.init_schema()

    observations: list[Envelope] = []

    async def fake_listener(env: Envelope) -> AgentResult | None:
        observations.append(env)
        return None

    _install_fake_secretary(fake_listener)

    orch = Orchestrator(
        store=store,
        sender=_RecordingSender(),
        allowed_chat_ids=frozenset({-1001}),
        allowed_user_ids=frozenset({42}),
    )
    update = InboundUpdate(
        kind="dm",
        chat_id=8888,
        msg_id=1,
        user_id=42,
        text="private message",
        received_by_bot="manager",
    )
    await orch.handle(update)

    assert len(observations) == 0


@pytest.mark.asyncio
async def test_listener_delegate_raises_routing_error(
    tmp_path: Path, fake_secretary_slot
) -> None:
    store = Store(tmp_path / "t.db")
    store.init_schema()

    async def bad_listener(env: Envelope) -> AgentResult | None:
        return AgentResult(
            reply_text=None, delegate_to="manager", handoff_text="no"
        )

    _install_fake_secretary(bad_listener)

    orch = Orchestrator(
        store=store,
        sender=_RecordingSender(),
        allowed_chat_ids=frozenset({-1001}),
        allowed_user_ids=frozenset({42}),
    )
    update = InboundUpdate(
        kind="group", chat_id=-1001, msg_id=1, user_id=42,
        text="hi", received_by_bot="manager",
    )
    with pytest.raises(RoutingError, match="listener"):
        await orch.handle(update)


@pytest.mark.asyncio
async def test_secretary_focus_target_not_double_dispatched(
    tmp_path: Path, fake_secretary_slot
) -> None:
    """If Secretary is already the focus target (user typed @secretary or
    the chat's focus points at Secretary), the listener fan-out must skip
    it — one invocation only."""
    store = Store(tmp_path / "t.db")
    store.init_schema()

    invocations: list[str] = []

    async def fake_listener(env: Envelope) -> AgentResult | None:
        invocations.append(env.routing_reason)
        if env.routing_reason == "mention":
            return AgentResult(
                reply_text="hi back", delegate_to=None, handoff_text=None
            )
        return None

    _install_fake_secretary(fake_listener)

    sender = _RecordingSender()
    orch = Orchestrator(
        store=store,
        sender=sender,
        allowed_chat_ids=frozenset({-1001}),
        allowed_user_ids=frozenset({42}),
        username_to_agent={"secretary_bot": "secretary"},
    )
    update = InboundUpdate(
        kind="group", chat_id=-1001, msg_id=1, user_id=42,
        text="@secretary hello", received_by_bot="secretary",
    )
    await orch.handle(update)

    # Exactly one invocation, via the focus (mention) path.
    assert invocations == ["mention"]


@pytest.mark.asyncio
async def test_listener_reply_uses_own_bot_and_correct_parent(
    tmp_path: Path, fake_secretary_slot
) -> None:
    store = Store(tmp_path / "t.db")
    store.init_schema()

    async def chatty_listener(env: Envelope) -> AgentResult | None:
        return AgentResult(
            reply_text="嘿我听到了", delegate_to=None, handoff_text=None
        )

    _install_fake_secretary(chatty_listener)

    sender = _RecordingSender()
    orch = Orchestrator(
        store=store,
        sender=sender,
        allowed_chat_ids=frozenset({-1001}),
        allowed_user_ids=frozenset({42}),
    )
    update = InboundUpdate(
        kind="group", chat_id=-1001, msg_id=1, user_id=42,
        text="anyone around",
        received_by_bot="manager",
    )
    await orch.handle(update)

    # Secretary's bot was used for its outbound reply.
    assert any(a == "secretary" and text == "嘿我听到了"
               for a, _, text in sender.sent)

    # Inspect the messages table: we should have 4 envelopes:
    #   1) user's "anyone around" (default_manager)
    #   2) manager's outbound reply (parent = 1)
    #   3) listener_observation to secretary (parent = 1)
    #   4) secretary's outbound reply (parent = 3, NOT 1)
    rows = store.conn.execute(
        "SELECT id, parent_id, from_agent, to_agent, "
        "json_extract(envelope_json, '$.routing_reason') AS rr "
        "FROM messages ORDER BY id ASC"
    ).fetchall()
    assert len(rows) == 4

    user_row = rows[0]
    manager_reply = rows[1]
    listener_obs = rows[2]
    secretary_reply = rows[3]

    assert user_row["rr"] == "default_manager"
    assert manager_reply["parent_id"] == user_row["id"]
    assert listener_obs["rr"] == "listener_observation"
    assert listener_obs["parent_id"] == user_row["id"]
    assert secretary_reply["parent_id"] == listener_obs["id"]
    assert secretary_reply["from_agent"] == "secretary"
