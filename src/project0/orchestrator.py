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
from dataclasses import dataclass, field
from datetime import UTC, datetime

from project0.agents.registry import AGENT_REGISTRY, LISTENER_REGISTRY, PULSE_REGISTRY
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
    username_to_agent: dict[str, str] = field(default_factory=dict)

    async def handle(self, update: InboundUpdate) -> None:
        # (1) Allow-list. Silent drop.
        if update.kind == "group" and update.chat_id not in self.allowed_chat_ids:
            log.info("allowlist: rejecting group chat_id=%s", update.chat_id)
            return
        if update.user_id not in self.allowed_user_ids:
            log.info("allowlist: rejecting user_id=%s", update.user_id)
            return

        async with self.store.lock:
            # (2) Build inbound envelope.
            inbound = self._build_inbound_envelope(update)

            # (2a) Content-based dedup for multi-bot groups. In a Telegram
            # group with multiple bot members, one user send can become two
            # physically distinct messages with sequential telegram_msg_ids
            # — one delivered to each bot's update queue — so the UNIQUE
            # constraint on telegram_msg_id cannot catch them. Drop any
            # inbound whose (chat_id, body) was already seen within the last
            # few seconds.
            if (
                update.kind == "group"
                and update.chat_id in self.allowed_chat_ids
                and self.store.messages().has_recent_user_text_in_group(
                    chat_id=update.chat_id,
                    body=update.text,
                    within_seconds=5,
                )
            ):
                log.info(
                    "content-dedup: dropping duplicate body=%r in chat=%s",
                    update.text,
                    update.chat_id,
                )
                return

            # (3) Insert. The msg_id UNIQUE constraint is the secondary
            # defense: it catches the case where the same physical Telegram
            # message is delivered to multiple pollers, which content-dedup
            # also handles but more conservatively.
            persisted = self.store.messages().insert(inbound)
            if persisted is None:
                log.info(
                    "msgid-dedup: dropping duplicate telegram msg chat=%s id=%s",
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

        final_focus_target: str = persisted.to_agent

        async with self.store.lock:
            if result.is_reply():
                await self._emit_reply(
                    parent=persisted,
                    speaker=persisted.to_agent,
                    text=result.reply_text or "",
                )
                reply_handled = True
            else:
                reply_handled = False

        if reply_handled:
            await self._fan_out_listeners(
                original_user_envelope=persisted,
                focus_target=final_focus_target,
            )
            return

        async with self.store.lock:
            # Delegation authority: only Manager may delegate.
            if persisted.to_agent != "manager":
                raise RoutingError(
                    f"only Manager may delegate; "
                    f"{persisted.to_agent} returned delegate_to={result.delegate_to}"
                )

            assert result.delegate_to is not None
            assert result.handoff_text is not None
            target = result.delegate_to
            if target not in AGENT_REGISTRY:
                raise RoutingError(f"unknown delegation target: {target!r}")

            # (a) Internal forward envelope — child of the original user
            #     envelope. We deliberately do NOT emit Manager's handoff_text
            #     to Telegram: the target agent (Secretary, Intelligence) will
            #     reply in its own voice, and a visible "forwarding to..."
            #     line from Manager is redundant noise in the group chat.
            #     The handoff_text is still kept on AgentResult for internal
            #     logging and for tests that need to assert on it.
            internal = Envelope(
                id=None,
                ts=_utc_now_iso(),
                parent_id=persisted.id,
                source="internal",
                telegram_chat_id=persisted.telegram_chat_id,
                telegram_msg_id=None,
                received_by_bot=None,
                from_kind="agent",
                from_agent="manager",
                to_agent=target,
                body=persisted.body,
                mentions=[],
                routing_reason="manager_delegation",
                payload=result.delegation_payload,
            )
            persisted_internal = self.store.messages().insert(internal)
            assert persisted_internal is not None

            # (c) Focus switches to the delegation target.
            assert persisted.telegram_chat_id is not None
            self.store.chat_focus().set(
                persisted.telegram_chat_id, target
            )

        # (d) Dispatch the target agent outside the lock.
        target_fn = AGENT_REGISTRY[target]
        target_result = await target_fn(persisted_internal)

        # Delegated agents are forbidden to delegate further (authority rule).
        if not target_result.is_reply():
            raise RoutingError(
                f"delegated agent {target!r} tried to return non-reply result"
            )

        final_focus_target = target

        async with self.store.lock:
            await self._emit_reply(
                parent=persisted_internal,
                speaker=target,
                text=target_result.reply_text or "",
            )

        await self._fan_out_listeners(
            original_user_envelope=persisted,
            focus_target=final_focus_target,
        )

    async def handle_pulse(self, pulse_env: Envelope) -> None:
        """Entry point for scheduled pulse ticks.

        Parallel to ``handle(update)``: persists the pulse envelope, then
        dispatches the target agent, then reuses the same reply / delegation
        paths. Does not touch chat_focus and does not fan out to listeners.
        """
        assert pulse_env.source == "pulse"
        assert pulse_env.routing_reason == "pulse"
        assert pulse_env.to_agent == "manager", (
            f"pulse target must be 'manager' in 6c; got {pulse_env.to_agent!r}"
        )

        async with self.store.lock:
            persisted = self.store.messages().insert(pulse_env)
            assert persisted is not None  # pulse envelopes never collide (no msg_id)

        # Dispatch via PULSE_REGISTRY (raw, un-adapted handler) so that a
        # None return propagates as "nothing to do" instead of being
        # inflated into a fail-visible fallback reply by the AGENT_REGISTRY
        # adapter. The pulse envelope itself is already persisted above,
        # so the audit trail is intact even when Manager stays silent.
        agent_fn = PULSE_REGISTRY[persisted.to_agent]
        result = await agent_fn(persisted)

        if result is None:
            log.debug("pulse %s: manager returned None", persisted.body)
            return

        if result.is_reply():
            async with self.store.lock:
                await self._emit_reply(
                    parent=persisted,
                    speaker=persisted.to_agent,
                    text=result.reply_text or "",
                )
            return

        # Delegation path — reuse the same structure as handle().
        async with self.store.lock:
            assert result.delegate_to is not None
            assert result.handoff_text is not None
            target = result.delegate_to
            if target not in AGENT_REGISTRY:
                raise RoutingError(f"unknown delegation target: {target!r}")

            # Manager's handoff_text is NOT emitted to Telegram for pulse
            # delegations either — only the target agent's reply reaches
            # the user. See handle() above for the same rationale.
            internal = Envelope(
                id=None,
                ts=_utc_now_iso(),
                parent_id=persisted.id,
                source="internal",
                telegram_chat_id=persisted.telegram_chat_id,
                telegram_msg_id=None,
                received_by_bot=None,
                from_kind="agent",
                from_agent="manager",
                to_agent=target,
                body=persisted.body,
                mentions=[],
                routing_reason="manager_delegation",
                payload=result.delegation_payload,
            )
            persisted_internal = self.store.messages().insert(internal)
            assert persisted_internal is not None

        target_fn = AGENT_REGISTRY[target]
        target_result = await target_fn(persisted_internal)
        if target_result is None or not target_result.is_reply():
            raise RoutingError(
                f"pulse-delegated agent {target!r} must return a reply"
            )

        async with self.store.lock:
            await self._emit_reply(
                parent=persisted_internal,
                speaker=target,
                text=target_result.reply_text or "",
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
            mentions = parse_mentions(
                u.text, AGENT_REGISTRY.keys(), self.username_to_agent
            )
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

    async def _fan_out_listeners(
        self,
        *,
        original_user_envelope: Envelope,
        focus_target: str,
    ) -> None:
        """Dispatch a listener_observation envelope to every listener
        whose name is not `focus_target`. Sequential; errors propagate.

        INVARIANT: callers MUST NOT hold ``self.store.lock`` when invoking
        this method. It acquires the lock internally for each sibling
        insertion and reply emission; because ``asyncio.Lock`` is
        non-reentrant, holding the lock on entry would deadlock silently.
        """
        if original_user_envelope.source != "telegram_group":
            return
        assert original_user_envelope.id is not None

        for listener_name, listener_fn in LISTENER_REGISTRY.items():
            if listener_name == focus_target:
                continue

            async with self.store.lock:
                sibling = Envelope(
                    id=None,
                    ts=_utc_now_iso(),
                    parent_id=original_user_envelope.id,
                    source="internal",
                    telegram_chat_id=original_user_envelope.telegram_chat_id,
                    telegram_msg_id=None,
                    received_by_bot=None,
                    from_kind="system",
                    from_agent=None,
                    to_agent=listener_name,
                    body=original_user_envelope.body,
                    mentions=[],
                    routing_reason="listener_observation",
                )
                persisted_sibling = self.store.messages().insert(sibling)
                assert persisted_sibling is not None

            # Dispatch outside the lock.
            result = await listener_fn(persisted_sibling)
            if result is None:
                log.debug("listener %s observed silently", listener_name)
                continue
            if result.delegate_to is not None:
                raise RoutingError(
                    f"listener {listener_name!r} returned delegate_to="
                    f"{result.delegate_to!r}; listeners cannot delegate"
                )
            if result.reply_text is None:
                log.warning("listener %s returned empty reply", listener_name)
                continue

            async with self.store.lock:
                await self._emit_reply(
                    parent=persisted_sibling,
                    speaker=listener_name,
                    text=result.reply_text,
                )
