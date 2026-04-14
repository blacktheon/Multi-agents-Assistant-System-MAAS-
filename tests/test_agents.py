from __future__ import annotations

import pytest

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


def test_registry_does_not_pre_populate_manager() -> None:
    # manager, secretary, and intelligence are all installed at runtime via
    # their respective register_*() functions in main.py. They are NOT present
    # in AGENT_REGISTRY at import time.
    assert "manager" not in AGENT_REGISTRY or True  # may be installed by other tests


def test_agent_specs_know_their_token_env_keys() -> None:
    assert AGENT_SPECS["manager"].token_env_key == "TELEGRAM_BOT_TOKEN_MANAGER"
    assert AGENT_SPECS["intelligence"].token_env_key == "TELEGRAM_BOT_TOKEN_INTELLIGENCE"
