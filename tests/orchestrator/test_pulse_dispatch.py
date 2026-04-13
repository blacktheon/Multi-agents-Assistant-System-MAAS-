# tests/orchestrator/test_pulse_dispatch.py
import asyncio
from pathlib import Path

import pytest

from project0.agents.registry import AGENT_REGISTRY
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
    async def fake_manager(env: Envelope) -> AgentResult:
        assert env.source == "pulse"
        assert env.payload["pulse_name"] == "check_calendar"
        return AgentResult(reply_text="nothing urgent", delegate_to=None, handoff_text=None)

    monkeypatch.setitem(AGENT_REGISTRY, "manager", fake_manager)

    await orch.handle_pulse(_pulse_env(chat_id=None))

    # Pulse envelope and the internal reply envelope are both persisted.
    # No telegram send happens because telegram_chat_id is None.
    # chat_id=None rows aren't queryable by recent_for_chat — check via direct SQL fetch:
    cur = orch.store._conn.execute("SELECT envelope_json FROM messages ORDER BY id")
    all_rows = [r[0] for r in cur.fetchall()]
    assert any('"source":"pulse"' in r for r in all_rows)
    assert any('"body":"nothing urgent"' in r for r in all_rows)
    # No Telegram send because chat_id is None
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

    monkeypatch.setitem(AGENT_REGISTRY, "manager", fake_manager)
    monkeypatch.setitem(AGENT_REGISTRY, "secretary", fake_secretary)

    await orch.handle_pulse(_pulse_env(chat_id=999))

    assert "env" in captured, "secretary should have been dispatched"
    secretary_env = captured["env"]
    assert secretary_env.routing_reason == "manager_delegation"
    assert secretary_env.payload is not None
    assert secretary_env.payload["kind"] == "reminder_request"
    assert secretary_env.payload["appointment"] == "牙医"


@pytest.mark.asyncio
async def test_pulse_dispatch_non_manager_target_rejected(orch):
    env = _pulse_env()
    env.to_agent = "secretary"
    with pytest.raises(AssertionError):
        await orch.handle_pulse(env)
