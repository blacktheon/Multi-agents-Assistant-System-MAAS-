from __future__ import annotations

import pytest

from project0.agents.intelligence import intelligence_stub
from project0.agents.manager import manager_stub
from project0.agents.registry import AGENT_REGISTRY, AGENT_SPECS
from project0.envelope import Envelope


def _env(body: str) -> Envelope:
    return Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=-100,
        telegram_msg_id=1,
        received_by_bot="manager",
        from_kind="user",
        from_agent=None,
        to_agent="manager",
        body=body,
        mentions=[],
        routing_reason="default_manager",
    )


@pytest.mark.asyncio
async def test_manager_stub_handles_normal_message() -> None:
    result = await manager_stub(_env("hello there"))
    assert result.is_reply()
    assert "manager-stub" in (result.reply_text or "")
    assert "hello there" in (result.reply_text or "")


@pytest.mark.asyncio
async def test_manager_stub_delegates_on_news_keyword() -> None:
    result = await manager_stub(_env("any news today?"))
    assert result.is_delegation()
    assert result.delegate_to == "intelligence"
    assert "@intelligence" in (result.handoff_text or "")


@pytest.mark.asyncio
async def test_manager_stub_keyword_is_case_insensitive() -> None:
    result = await manager_stub(_env("give me the News please"))
    assert result.is_delegation()


@pytest.mark.asyncio
async def test_intelligence_stub_always_replies() -> None:
    result = await intelligence_stub(_env("any news today?"))
    assert result.is_reply()
    assert "intelligence-stub" in (result.reply_text or "")


def test_registry_contains_manager_and_intelligence() -> None:
    assert set(AGENT_REGISTRY.keys()) == {"manager", "intelligence"}


def test_agent_specs_know_their_token_env_keys() -> None:
    assert AGENT_SPECS["manager"].token_env_key == "TELEGRAM_BOT_TOKEN_MANAGER"
    assert AGENT_SPECS["intelligence"].token_env_key == "TELEGRAM_BOT_TOKEN_INTELLIGENCE"
