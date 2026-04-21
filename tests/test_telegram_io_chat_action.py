"""FakeBotSender records typing/chat_action sends; Protocol requires it."""

from __future__ import annotations

import pytest

from project0.telegram_io import FakeBotSender


@pytest.mark.asyncio
async def test_fake_bot_sender_records_chat_action() -> None:
    sender = FakeBotSender()
    await sender.send_chat_action(agent="secretary", chat_id=42, action="typing")
    assert sender.chat_actions == [
        {"agent": "secretary", "chat_id": 42, "action": "typing"}
    ]


@pytest.mark.asyncio
async def test_fake_bot_sender_send_and_chat_action_are_independent_lists() -> None:
    sender = FakeBotSender()
    await sender.send(agent="secretary", chat_id=1, text="hi")
    await sender.send_chat_action(agent="secretary", chat_id=1, action="typing")
    assert len(sender.sent) == 1
    assert len(sender.chat_actions) == 1
