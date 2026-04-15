import pytest

from project0.agents.manager import (
    Manager,
    ManagerConfig,
    ManagerPersona,
)
from project0.agents.registry import AGENT_REGISTRY, register_manager
from project0.envelope import Envelope
from project0.llm.provider import FakeProvider
from project0.llm.tools import ToolUseResult


@pytest.mark.asyncio
async def test_register_manager_installs_handle_in_registry():
    persona = ManagerPersona(core="c", dm_mode="d", group_addressed_mode="g",
                             pulse_mode="p", tool_use_guide="t")
    config = ManagerConfig(model="m", max_tokens_reply=100,
                           max_tool_iterations=4, transcript_window=5)

    fake = FakeProvider(tool_responses=[
        ToolUseResult(kind="text", text="ok", tool_calls=[], stop_reason="end_turn")
    ])

    class _Msgs:
        def recent_for_chat(self, *, chat_id, limit):
            return []

        def recent_for_dm(self, *, chat_id, agent, limit):
            return []

    mgr = Manager(llm=fake, calendar=None, memory=None,
                  messages_store=_Msgs(), persona=persona, config=config)

    # Save original to restore after.
    original = AGENT_REGISTRY.get("manager")
    try:
        register_manager(mgr.handle)
        handle = AGENT_REGISTRY["manager"]
        env = Envelope(
            id=1, ts="2026-04-14T00:00:00Z", parent_id=None,
            source="telegram_dm", telegram_chat_id=42, telegram_msg_id=1,
            received_by_bot="manager", from_kind="user", from_agent=None,
            to_agent="manager", body="hi", mentions=[], routing_reason="direct_dm",
        )
        result = await handle(env)
        assert result.reply_text == "ok"
    finally:
        if original is not None:
            AGENT_REGISTRY["manager"] = original
        else:
            AGENT_REGISTRY.pop("manager", None)
