"""The routing pipeline. Plain async Python — no framework.

Responsibilities:
  1. Dedup multi-bot polling races at the DB UNIQUE layer.
  2. Enforce the allow-list.
  3. Classify DM vs group.
  4. For groups, parse @mentions and resolve focus.
  5. Persist the envelope, dispatch to the agent, persist the reply.
  6. Enforce delegation authority (only Manager can delegate) — see Task 10.

Intentionally *does not* do: LLM calls, Telegram-specific logic, logging
beyond one-line event records. Those belong in telegram_io.py and main.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from project0.agents.registry import AGENT_REGISTRY
from project0.envelope import Envelope, RoutingReason
from project0.errors import RoutingError
from project0.mentions import parse_mentions
from project0.store import Store
from project0.telegram_io import BotSender, InboundUpdate

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class Orchestrator:
    store: Store
    sender: BotSender
    allowed_chat_ids: frozenset[int]
    allowed_user_ids: frozenset[int]

    async def handle(self, update: InboundUpdate) -> None:
        # (1) Allow-list. Silent drop.
        if update.kind == "group" and update.chat_id not in self.allowed_chat_ids:
            log.info("allowlist: rejecting group chat_id=%s", update.chat_id)
            return
        if update.user_id not in self.allowed_user_ids:
            log.info("allowlist: rejecting user_id=%s", update.user_id)
            return

        async with self.store.lock:
            # (2) Build inbound envelope, (3) resolve target, (4) try to insert.
            inbound = self._build_inbound_envelope(update)
            persisted = self.store.messages().insert(inbound)
            if persisted is None:
                log.info(
                    "dedup: dropping duplicate telegram msg chat=%s id=%s",
                    update.chat_id,
                    update.msg_id,
                )
                return

            # (5) Update focus if needed.
            if persisted.routing_reason in ("mention", "default_manager"):
                assert persisted.telegram_chat_id is not None
                self.store.chat_focus().set(
                    persisted.telegram_chat_id, persisted.to_agent
                )

        # (6) Dispatch the agent. Done outside the lock so agent code cannot
        # accidentally hold it. Agent stubs are pure functions; real agents
        # will also be free of store-side-effects except through typed APIs.
        agent_fn = AGENT_REGISTRY[persisted.to_agent]
        result = await agent_fn(persisted)

        async with self.store.lock:
            if result.is_reply():
                await self._emit_reply(
                    parent=persisted,
                    speaker=persisted.to_agent,
                    text=result.reply_text or "",
                )
                return

            # Delegation path implemented in Task 10.
            raise RoutingError(
                "delegation path not implemented yet (covered in Task 10)"
            )

    # --- helpers -------------------------------------------------------------

    def _build_inbound_envelope(self, u: InboundUpdate) -> Envelope:
        source: str
        to_agent: str
        reason: RoutingReason
        mentions: list[str] = []

        if u.kind == "dm":
            source = "telegram_dm"
            to_agent = u.received_by_bot
            reason = "direct_dm"
        else:
            source = "telegram_group"
            mentions = parse_mentions(u.text, AGENT_REGISTRY.keys())
            if mentions:
                to_agent = mentions[-1]  # last mention wins
                reason = "mention"
            else:
                existing = self.store.chat_focus().get(u.chat_id)
                if existing is not None:
                    to_agent = existing
                    reason = "focus"
                else:
                    to_agent = "manager"
                    reason = "default_manager"

        return Envelope(
            id=None,
            ts=_utc_now_iso(),
            parent_id=None,
            source=source,  # type: ignore[arg-type]
            telegram_chat_id=u.chat_id,
            telegram_msg_id=u.msg_id,
            received_by_bot=u.received_by_bot,
            from_kind="user",
            from_agent=None,
            to_agent=to_agent,
            body=u.text,
            mentions=mentions,
            routing_reason=reason,
        )

    async def _emit_reply(self, *, parent: Envelope, speaker: str, text: str) -> None:
        """Persist an outbound_reply envelope (child of parent) and dispatch it."""
        assert parent.id is not None
        out = Envelope(
            id=None,
            ts=_utc_now_iso(),
            parent_id=parent.id,
            source="internal",
            telegram_chat_id=parent.telegram_chat_id,
            telegram_msg_id=None,
            received_by_bot=None,
            from_kind="agent",
            from_agent=speaker,
            to_agent="user",
            body=text,
            mentions=[],
            routing_reason="outbound_reply",
        )
        persisted = self.store.messages().insert(out)
        assert persisted is not None  # internal rows are not dedup'd
        if parent.telegram_chat_id is not None:
            await self.sender.send(
                agent=speaker, chat_id=parent.telegram_chat_id, text=text
            )
