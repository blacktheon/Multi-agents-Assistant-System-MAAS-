"""typing_indicator CM refreshes 'typing' every ~refresh_seconds until exited.

The CM is the user-visible feedback during slow local-LLM inference.
Contract:
  - On enter: one chat_action fires immediately.
  - While open: refresh every `refresh_seconds` seconds.
  - On exit (success or exception): background refresh task is cancelled.
  - Sender errors are swallowed (typing failure must never block a reply).
"""

from __future__ import annotations

import asyncio

import pytest

from project0.telegram_io import FakeBotSender, typing_indicator


@pytest.mark.asyncio
async def test_typing_indicator_sends_immediately_on_enter() -> None:
    sender = FakeBotSender()
    async with typing_indicator(sender=sender, agent="secretary", chat_id=9, refresh_seconds=10.0):
        pass
    assert len(sender.chat_actions) == 1
    assert sender.chat_actions[0]["action"] == "typing"
    assert sender.chat_actions[0]["agent"] == "secretary"
    assert sender.chat_actions[0]["chat_id"] == 9


@pytest.mark.asyncio
async def test_typing_indicator_refreshes_while_open() -> None:
    sender = FakeBotSender()
    async with typing_indicator(sender=sender, agent="secretary", chat_id=9, refresh_seconds=0.05):
        await asyncio.sleep(0.18)  # ~3 refreshes expected: 0 + 0.05 + 0.10 + 0.15
    # Allow some scheduler slop: expect at least 3 sends.
    assert len(sender.chat_actions) >= 3


@pytest.mark.asyncio
async def test_typing_indicator_stops_refreshing_on_exit() -> None:
    sender = FakeBotSender()
    async with typing_indicator(sender=sender, agent="secretary", chat_id=9, refresh_seconds=0.05):
        await asyncio.sleep(0.12)
    count_at_exit = len(sender.chat_actions)
    await asyncio.sleep(0.20)
    assert len(sender.chat_actions) == count_at_exit  # no further sends after exit


@pytest.mark.asyncio
async def test_typing_indicator_swallows_sender_errors() -> None:
    class BrokenSender:
        async def send(self, *, agent: str, chat_id: int, text: str) -> None:
            raise RuntimeError("should not be called")

        async def send_chat_action(self, *, agent: str, chat_id: int, action: str) -> None:
            raise RuntimeError("boom")

    # Must not raise.
    async with typing_indicator(sender=BrokenSender(), agent="secretary", chat_id=1, refresh_seconds=0.05):
        await asyncio.sleep(0.10)


@pytest.mark.asyncio
async def test_typing_indicator_cancels_on_inner_exception() -> None:
    sender = FakeBotSender()
    with pytest.raises(ValueError):
        async with typing_indicator(sender=sender, agent="secretary", chat_id=9, refresh_seconds=0.05):
            raise ValueError("caller error")
    count_after_exit = len(sender.chat_actions)
    await asyncio.sleep(0.10)
    assert len(sender.chat_actions) == count_after_exit
