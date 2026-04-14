# tests/orchestrator/test_pulse_dispatch.py
import asyncio
from pathlib import Path

import pytest

from project0.agents.registry import AGENT_REGISTRY, PULSE_REGISTRY
from project0.envelope import AgentResult, Envelope
from project0.orchestrator import Orchestrator
from project0.store import Store
from project0.telegram_io import FakeBotSender


@pytest.fixture
def orch(tmp_path):
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    o = Orchestrator(
        store=store,
        sender=FakeBotSender(),
        allowed_chat_ids=frozenset({999}),
        allowed_user_ids=frozenset({1}),
    )
    return o


def _pulse_env(chat_id=None):
    return Envelope(
        id=None,
        ts="2026-04-14T00:00:00Z",
        parent_id=None,
        source="pulse",
        telegram_chat_id=chat_id,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="system",
        from_agent=None,
        to_agent="manager",
        body="check_calendar",
        mentions=[],
        routing_reason="pulse",
        payload={"pulse_name": "check_calendar", "window_minutes": 60},
    )


@pytest.mark.asyncio
async def test_pulse_dispatch_text_reply(orch, monkeypatch):
    async def fake_manager(env: Envelope) -> AgentResult | None:
        assert env.source == "pulse"
        assert env.payload["pulse_name"] == "check_calendar"
        return AgentResult(reply_text="nothing urgent", delegate_to=None, handoff_text=None)

    # handle_pulse dispatches via PULSE_REGISTRY (raw handlers), not
    # AGENT_REGISTRY (which wraps None → fail-visible fallback).
    monkeypatch.setitem(PULSE_REGISTRY, "manager", fake_manager)

    await orch.handle_pulse(_pulse_env(chat_id=None))

    # Pulse envelope and the internal reply envelope are both persisted.
    # No telegram send happens because telegram_chat_id is None.
    cur = orch.store._conn.execute("SELECT envelope_json FROM messages ORDER BY id")
    all_rows = [r[0] for r in cur.fetchall()]
    assert any('"source":"pulse"' in r for r in all_rows)
    assert any('"body":"nothing urgent"' in r for r in all_rows)
    # No Telegram send because chat_id is None
    assert orch.sender.sent == []


@pytest.mark.asyncio
async def test_pulse_dispatch_none_result_stays_silent(orch, monkeypatch):
    """Pulse path: if Manager returns None (nothing urgent), handle_pulse
    must NOT emit anything — no persisted reply, no Telegram send. This
    is the critical property that prevents pulse spam in the group chat."""
    async def silent_manager(env: Envelope) -> AgentResult | None:
        return None

    monkeypatch.setitem(PULSE_REGISTRY, "manager", silent_manager)

    await orch.handle_pulse(_pulse_env(chat_id=999))

    # Only the pulse envelope itself is persisted; no reply envelope.
    cur = orch.store._conn.execute("SELECT envelope_json FROM messages ORDER BY id")
    all_rows = [r[0] for r in cur.fetchall()]
    assert len(all_rows) == 1
    assert '"source":"pulse"' in all_rows[0]
    assert orch.sender.sent == []


@pytest.mark.asyncio
async def test_pulse_dispatch_delegation_forwards_payload(orch, monkeypatch):
    captured: dict = {}

    async def fake_manager(env: Envelope) -> AgentResult:
        return AgentResult(
            reply_text=None,
            delegate_to="secretary",
            handoff_text="→ 已让秘书帮你记着",
            delegation_payload={
                "kind": "reminder_request",
                "appointment": "牙医",
                "when": "明天上午",
                "note": "",
            },
        )

    async def fake_secretary(env: Envelope) -> AgentResult:
        captured["env"] = env
        return AgentResult(reply_text="好的，明天上午提醒你", delegate_to=None, handoff_text=None)

    # Manager dispatch goes through PULSE_REGISTRY (raw handler); the
    # delegation target (Secretary) is still looked up in AGENT_REGISTRY.
    monkeypatch.setitem(PULSE_REGISTRY, "manager", fake_manager)
    monkeypatch.setitem(AGENT_REGISTRY, "secretary", fake_secretary)

    await orch.handle_pulse(_pulse_env(chat_id=999))

    assert "env" in captured, "secretary should have been dispatched"
    secretary_env = captured["env"]
    assert secretary_env.routing_reason == "manager_delegation"
    assert secretary_env.payload is not None
    assert secretary_env.payload["kind"] == "reminder_request"
    assert secretary_env.payload["appointment"] == "牙医"

    # Manager's handoff_text must NOT be emitted to Telegram — only
    # Secretary's reply reaches the user. Exactly one send.
    assert len(orch.sender.sent) == 1
    assert orch.sender.sent[0]["agent"] == "secretary"
    assert orch.sender.sent[0]["text"] == "好的，明天上午提醒你"


@pytest.mark.asyncio
async def test_pulse_dispatch_non_manager_target_rejected(orch):
    env = _pulse_env()
    env.to_agent = "secretary"
    with pytest.raises(AssertionError):
        await orch.handle_pulse(env)
