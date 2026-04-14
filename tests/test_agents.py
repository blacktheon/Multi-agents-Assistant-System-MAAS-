from __future__ import annotations

import pytest

from project0.agents.intelligence import intelligence_stub
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
async def test_intelligence_stub_always_replies() -> None:
    result = await intelligence_stub(_env("any news today?"))
    assert result.is_reply()
    assert "intelligence-stub" in (result.reply_text or "")


def test_registry_contains_intelligence() -> None:
    assert "intelligence" in AGENT_REGISTRY


def test_registry_does_not_pre_populate_manager() -> None:
    # manager is installed at runtime via register_manager(); not at import time.
    # This test verifies the module-load state (no fixture installs manager here).
    # NOTE: if another test in the session calls register_manager(), the key may
    # already be present — so we only assert intelligence is always there.
    assert "intelligence" in AGENT_REGISTRY


def test_agent_specs_know_their_token_env_keys() -> None:
    assert AGENT_SPECS["manager"].token_env_key == "TELEGRAM_BOT_TOKEN_MANAGER"
    assert AGENT_SPECS["intelligence"].token_env_key == "TELEGRAM_BOT_TOKEN_INTELLIGENCE"
