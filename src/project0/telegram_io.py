"""Telegram I/O.

In this skeleton, two concerns live here:

  1. InboundUpdate — a minimal normalized representation of an incoming
     Telegram message. Real bot handlers (added in Task 12) convert
     python-telegram-bot Update objects into InboundUpdate so the
     orchestrator never imports telegram.* types.

  2. BotSender — the outbound interface. FakeBotSender is used by tests
     and records every outbound message; RealBotSender (Task 12) dispatches
     through python-telegram-bot Applications.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

Kind = Literal["group", "dm"]


@dataclass(frozen=True)
class InboundUpdate:
    """Normalized incoming message. Orchestrator consumes this."""

    received_by_bot: str  # which agent's bot saw it
    kind: Kind
    chat_id: int
    msg_id: int
    user_id: int
    text: str


class BotSender(Protocol):
    async def send(self, *, agent: str, chat_id: int, text: str) -> None:
        """Send `text` to `chat_id` as `agent`'s bot."""


@dataclass
class FakeBotSender:
    """Test double. Records every send so tests can assert the outbound tree."""

    sent: list[dict[str, object]] = field(default_factory=list)

    async def send(self, *, agent: str, chat_id: int, text: str) -> None:
        self.sent.append({"agent": agent, "chat_id": chat_id, "text": text})
