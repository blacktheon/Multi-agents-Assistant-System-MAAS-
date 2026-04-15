"""register_intelligence tests. Parallel to test_register_manager.py."""
from __future__ import annotations

import pytest

from project0.agents.registry import AGENT_REGISTRY, AGENT_SPECS, register_intelligence
from project0.envelope import AgentResult, Envelope


@pytest.mark.asyncio
async def test_register_intelligence_installs_handler():
    async def fake_handle(env: Envelope) -> AgentResult | None:
        return AgentResult(reply_text="ok", delegate_to=None, handoff_text=None)

    register_intelligence(fake_handle)
    assert "intelligence" in AGENT_REGISTRY


@pytest.mark.asyncio
async def test_register_intelligence_adapter_surfaces_none_as_placeholder():
    async def null_handle(env: Envelope) -> AgentResult | None:
        return None

    register_intelligence(null_handle)
    env = Envelope(
        id=None,
        ts="2026-04-15T00:00:00Z",
        parent_id=None,
        source="telegram_dm",
        from_kind="user",
        from_agent=None,
        to_agent="intelligence",
        routing_reason="direct_dm",
        telegram_chat_id=1,
        telegram_msg_id=1,
        received_by_bot="intelligence",
        body="hi",
        mentions=[],
    )
    result = await AGENT_REGISTRY["intelligence"](env)
    assert isinstance(result, AgentResult)
    assert result.reply_text  # non-empty placeholder


def test_intelligence_token_env_key_unchanged():
    assert AGENT_SPECS["intelligence"].token_env_key == "TELEGRAM_BOT_TOKEN_INTELLIGENCE"
