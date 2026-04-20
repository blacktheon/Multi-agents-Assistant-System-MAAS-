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

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from telegram import Update
from telegram.ext import Application, MessageHandler, filters

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

    async def send_chat_action(self, *, agent: str, chat_id: int, action: str) -> None:
        """Send a chat action (e.g. 'typing') to `chat_id` as `agent`'s bot.
        Telegram's indicator auto-expires after ~5s; callers refresh if needed."""


@dataclass
class FakeBotSender:
    """Test double. Records every send so tests can assert the outbound tree."""

    sent: list[dict[str, object]] = field(default_factory=list)
    chat_actions: list[dict[str, object]] = field(default_factory=list)

    async def send(self, *, agent: str, chat_id: int, text: str) -> None:
        self.sent.append({"agent": agent, "chat_id": chat_id, "text": text})

    async def send_chat_action(self, *, agent: str, chat_id: int, action: str) -> None:
        self.chat_actions.append(
            {"agent": agent, "chat_id": chat_id, "action": action}
        )


# --- Real Telegram wiring -------------------------------------------------

log = logging.getLogger(__name__)

UpdateHandler = Callable[[InboundUpdate], Awaitable[None]]


class RealBotSender:
    """Outbound dispatcher. Holds one telegram Application per agent and
    picks the correct one by agent name when sending.
    """

    def __init__(self, apps_by_agent: dict[str, Application[Any, Any, Any, Any, Any, Any]]) -> None:
        self._apps = apps_by_agent

    async def send(self, *, agent: str, chat_id: int, text: str) -> None:
        app = self._apps[agent]
        await app.bot.send_message(chat_id=chat_id, text=text)

    async def send_chat_action(self, *, agent: str, chat_id: int, action: str) -> None:
        app = self._apps[agent]
        await app.bot.send_chat_action(chat_id=chat_id, action=action)


def _classify_update(update: Update, received_by_bot: str) -> InboundUpdate | None:
    msg = update.message
    if msg is None or msg.from_user is None or msg.chat is None:
        return None
    # Drop bot-originated messages (including our own agents' outbound sends).
    # With privacy mode disabled, bots see every message in a group, including
    # other bots' messages. The allow-list would reject these by user_id, but
    # filtering here is cheaper and more explicit.
    if msg.from_user.is_bot:
        return None
    text = msg.text if msg.text is not None else "[non-text]"
    chat_type = msg.chat.type  # 'private', 'group', 'supergroup', 'channel'
    if chat_type == "private":
        kind: Kind = "dm"
    elif chat_type in ("group", "supergroup"):
        kind = "group"
    else:
        return None
    return InboundUpdate(
        received_by_bot=received_by_bot,
        kind=kind,
        chat_id=msg.chat.id,
        msg_id=msg.message_id,
        user_id=msg.from_user.id,
        text=text,
    )


def fetch_bot_usernames(
    apps: dict[str, Application[Any, Any, Any, Any, Any, Any]],
) -> dict[str, str]:
    """Return a ``{telegram_username_lowercase: agent_name}`` mapping for the
    given apps. Must be called AFTER each app has been ``initialize()``'d —
    before that, ``app.bot.username`` is None.
    """
    result: dict[str, str] = {}
    for agent_name, app in apps.items():
        username = app.bot.username
        if username is None:
            raise RuntimeError(
                f"app for agent {agent_name!r} has no username; "
                "fetch_bot_usernames must be called after initialize()"
            )
        result[username.lower()] = agent_name
    return result


async def build_bot_applications(
    bot_tokens: dict[str, str],
    handler: UpdateHandler,
) -> tuple[dict[str, Application[Any, Any, Any, Any, Any, Any]], RealBotSender]:
    """Create one Application per agent token, register a single text-message
    handler on each, and return (apps, sender).

    The caller is responsible for running `app.run_polling()` tasks — see
    main.py. This function does not start polling itself.
    """
    apps: dict[str, Application[Any, Any, Any, Any, Any, Any]] = {}
    for agent_name, token in bot_tokens.items():
        app = Application.builder().token(token).build()

        async def _dispatch(update: Update, _context: object, _agent: str = agent_name) -> None:
            inbound = _classify_update(update, received_by_bot=_agent)
            if inbound is None:
                return
            try:
                await handler(inbound)
            except Exception:
                log.exception("orchestrator raised on update from bot=%s", _agent)

        app.add_handler(MessageHandler(filters.ALL, _dispatch))
        apps[agent_name] = app

    sender = RealBotSender(apps)
    return apps, sender
