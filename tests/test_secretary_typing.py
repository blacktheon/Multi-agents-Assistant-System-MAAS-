"""Secretary emits a typing indicator during reply LLM calls when a sender
is wired. When no sender is wired (legacy tests), reply paths must still
work without any chat_action being sent."""

from __future__ import annotations

from pathlib import Path

import pytest

from project0.agents.secretary import Secretary, SecretaryConfig, load_persona
from project0.envelope import Envelope
from project0.llm.provider import FakeProvider
from project0.store import Store
from project0.telegram_io import FakeBotSender


def _cfg() -> SecretaryConfig:
    return SecretaryConfig(
        t_min_seconds=90,
        n_min_messages=4,
        l_min_weighted_chars=200,
        transcript_window=20,
        model="test",
        max_tokens_reply=100,
        max_tokens_listener=50,
        skip_sentinels=["[skip]"],
    )


def _dm_envelope() -> Envelope:
    return Envelope(
        id=None,
        ts="2026-04-20T00:00:00+00:00",
        parent_id=None,
        source="telegram_dm",
        telegram_chat_id=7,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="human",
        from_agent=None,
        to_agent="secretary",
        body="hi",
        routing_reason="direct_dm",
    )


@pytest.mark.asyncio
async def test_dm_reply_emits_typing_when_sender_wired(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "s.db"))
    store.init_schema()
    sender = FakeBotSender()
    persona = load_persona(Path("prompts/secretary.md"))
    secretary = Secretary(
        llm=FakeProvider(responses=["好的"]),
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=_cfg(),
        bot_sender=sender,
    )
    env = _dm_envelope()
    result = await secretary.handle(env)
    assert result is not None and result.reply_text == "好的"
    typing_to_chat_7 = [a for a in sender.chat_actions if a["chat_id"] == 7]
    assert len(typing_to_chat_7) >= 1


@pytest.mark.asyncio
async def test_dm_reply_works_without_sender(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "s.db"))
    store.init_schema()
    persona = load_persona(Path("prompts/secretary.md"))
    secretary = Secretary(
        llm=FakeProvider(responses=["好的"]),
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=_cfg(),
    )
    env = _dm_envelope()
    result = await secretary.handle(env)
    assert result is not None and result.reply_text == "好的"


@pytest.mark.asyncio
async def test_set_bot_sender_enables_typing_after_construction(tmp_path: Path) -> None:
    """Secretary can be constructed with bot_sender=None and have a real
    sender injected later via set_bot_sender. This matches how main.py
    wires RealBotSender after build_bot_applications returns."""
    store = Store(str(tmp_path / "s.db"))
    store.init_schema()
    persona = load_persona(Path("prompts/secretary.md"))
    secretary = Secretary(
        llm=FakeProvider(responses=["好的"]),
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=_cfg(),
    )  # no bot_sender at construction

    late_sender = FakeBotSender()
    secretary.set_bot_sender(late_sender)

    result = await secretary.handle(_dm_envelope())
    assert result is not None and result.reply_text == "好的"
    typing_to_chat_7 = [a for a in late_sender.chat_actions if a["chat_id"] == 7]
    assert len(typing_to_chat_7) >= 1
