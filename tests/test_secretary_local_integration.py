"""End-to-end: Secretary wired with LocalProvider (mocked endpoint) replies
to a DM, emits a typing indicator, and never receives a tool list because
user_facts_writer is None. Also verifies that provider errors are caught
gracefully and do not crash the handler."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from project0.agents.secretary import Secretary, load_config, load_persona
from project0.envelope import Envelope
from project0.llm.local_provider import LocalProvider
from project0.store import LLMUsageStore, Store
from project0.telegram_io import FakeBotSender

BASE_URL = "http://127.0.0.1:8000/v1"
MODEL = "qwen2.5-72b-awq-8k"


def _dm_envelope() -> Envelope:
    return Envelope(
        id=None,
        ts="2026-04-20T00:00:00+00:00",
        parent_id=None,
        source="telegram_dm",
        telegram_chat_id=7,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="user",
        from_agent=None,
        to_agent="secretary",
        body="hi",
        routing_reason="direct_dm",
    )


def _make_secretary(store: Store, provider: LocalProvider, sender: FakeBotSender) -> Secretary:
    persona = load_persona(Path("prompts/secretary_free.md"))
    cfg = load_config(Path("prompts/secretary_free.toml"))
    return Secretary(
        llm=provider,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=cfg,
        user_facts_reader=None,
        user_facts_writer=None,
        bot_sender=sender,
    )


@pytest.mark.asyncio
@respx.mock
async def test_secretary_local_dm_reply_with_typing_and_no_tools(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "s.db"))
    store.init_schema()
    usage = LLMUsageStore(store.conn)

    captured: list[dict[str, object]] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {"role": "assistant", "content": "嗯，老公，我听着呢。"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            },
        )

    respx.post(f"{BASE_URL}/chat/completions").mock(side_effect=_capture)

    provider = LocalProvider(
        base_url=BASE_URL, model=MODEL, api_key="unused", usage_store=usage,
    )
    sender = FakeBotSender()
    secretary = _make_secretary(store, provider, sender)

    result = await secretary.handle(_dm_envelope())
    assert result is not None
    assert result.reply_text == "嗯，老公，我听着呢。"

    # Typing indicator fired at least once for this chat.
    typing = [a for a in sender.chat_actions if a["chat_id"] == 7]
    assert len(typing) >= 1

    # Request payload contains NO `tools` field (writer was None → no tool).
    assert len(captured) == 1
    assert "tools" not in captured[0]

    # user_facts table is untouched.
    cur = store.conn.execute("SELECT COUNT(*) FROM user_facts")
    assert cur.fetchone()[0] == 0


@pytest.mark.asyncio
@respx.mock
async def test_secretary_local_handles_unavailable_server_gracefully(tmp_path: Path) -> None:
    """If the local server is down, Secretary's handler must NOT raise —
    it must catch the provider error and return None (silent drop) or
    whatever the existing error path does. The daemon must keep running."""
    store = Store(str(tmp_path / "s.db"))
    store.init_schema()
    usage = LLMUsageStore(store.conn)

    respx.post(f"{BASE_URL}/chat/completions").mock(
        side_effect=httpx.ConnectError("refused"),
    )

    provider = LocalProvider(
        base_url=BASE_URL, model=MODEL, api_key="unused", usage_store=usage,
    )
    sender = FakeBotSender()
    secretary = _make_secretary(store, provider, sender)

    # Must NOT raise.
    result = await secretary.handle(_dm_envelope())
    # Existing Secretary error path returns None when the provider fails;
    # that is the expected behaviour (orchestrator treats None as silent drop).
    assert result is None
