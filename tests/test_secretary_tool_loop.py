"""Task 14: Secretary uses a bounded tool loop and can save user facts
via the ``remember_about_user`` tool."""
from __future__ import annotations

import pytest

from project0.agents.secretary import (
    Secretary,
    SecretaryConfig,
    SecretaryPersona,
    remember_about_user_tool_spec,
)
from project0.envelope import Envelope
from project0.llm.provider import FakeProvider
from project0.llm.tools import ToolCall, ToolUseResult
from project0.store import (
    AgentMemory,
    LLMUsageStore,
    MessagesStore,
    Store,
    UserFactsReader,
    UserFactsWriter,
    UserProfile,
)


def _make_secretary(
    llm: FakeProvider,
    usage: LLMUsageStore,
    store: Store,
    *,
    profile: UserProfile | None = None,
    reader: UserFactsReader | None = None,
    writer: UserFactsWriter | None = None,
) -> Secretary:
    del usage  # only needed so the fake records through it; already wired on llm
    persona = SecretaryPersona(
        core="秘书 core persona block",
        listener_mode="listener mode section",
        group_addressed_mode="addressed mode section",
        dm_mode="dm mode section",
        reminder_mode="reminder mode section",
    )
    cfg = SecretaryConfig(
        t_min_seconds=0,
        n_min_messages=0,
        l_min_weighted_chars=0,
        transcript_window=20,
        model="claude-sonnet-4-6",
        max_tokens_reply=800,
        max_tokens_listener=600,
        skip_sentinels=["[skip]"],
    )
    return Secretary(
        llm=llm,
        memory=AgentMemory(store.conn, "secretary"),
        messages_store=MessagesStore(store.conn),
        persona=persona,
        config=cfg,
        user_profile=profile,
        user_facts_reader=reader,
        user_facts_writer=writer,
    )


def test_remember_about_user_tool_spec_shape() -> None:
    spec = remember_about_user_tool_spec()
    assert spec.name == "remember_about_user"
    assert "fact_text" in spec.input_schema["properties"]
    assert "topic" in spec.input_schema["properties"]
    assert spec.input_schema["required"] == ["fact_text"]


@pytest.mark.asyncio
async def test_secretary_remembers_fact_via_tool_call() -> None:
    store = Store(":memory:")
    store.init_schema()
    usage = LLMUsageStore(store.conn)
    writer = UserFactsWriter("secretary", store.conn)
    reader = UserFactsReader("secretary", store.conn)

    # First call: tool_use with remember_about_user.
    # Second call: plain text reply.
    fake = FakeProvider(
        tool_responses=[
            ToolUseResult(
                kind="tool_use",
                text=None,
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="remember_about_user",
                        input={"fact_text": "最喜欢吃寿司", "topic": "food"},
                    )
                ],
                stop_reason="tool_use",
            ),
            ToolUseResult(
                kind="text",
                text="好的，记住了宝贝～",
                tool_calls=[],
                stop_reason="end_turn",
            ),
        ],
        usage_store=usage,
    )
    sec = _make_secretary(
        fake,
        usage,
        store,
        profile=UserProfile(),
        reader=reader,
        writer=writer,
    )

    env = Envelope(
        id=200,
        ts="2026-04-16T10:00:00Z",
        parent_id=None,
        source="telegram_dm",
        telegram_chat_id=1,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="user",
        from_agent=None,
        to_agent="secretary",
        body="我最喜欢吃寿司",
        mentions=[],
        routing_reason="direct_dm",
    )
    result = await sec.handle(env)
    assert result is not None
    assert "好的" in result.reply_text

    # Fact persisted
    facts = reader.active()
    assert len(facts) == 1
    assert facts[0].fact_text == "最喜欢吃寿司"
    assert facts[0].topic == "food"

    # Two LLM calls recorded (one per tool-loop turn)
    rows = store.conn.execute("SELECT count(*) FROM llm_usage").fetchone()
    assert rows[0] == 2

    # Both calls labeled (agent=secretary, purpose=reply)
    purposes = [
        r[0]
        for r in store.conn.execute(
            "SELECT purpose FROM llm_usage ORDER BY id"
        ).fetchall()
    ]
    assert purposes == ["reply", "reply"]
