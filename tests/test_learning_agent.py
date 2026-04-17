"""Tests for the Learning agent — persona loading, config loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project0.agents._tool_loop import TurnState
from project0.envelope import Envelope
from project0.llm.tools import ToolCall
from project0.store import KnowledgeIndexStore, ReviewScheduleStore, Store


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def test_load_persona_has_all_sections() -> None:
    from project0.agents.learning import load_learning_persona

    persona = load_learning_persona(PROMPTS_DIR / "learning.md")

    # Each field should contain representative Chinese text from its section
    assert "温书瑶" in persona.core
    assert "角色设定" in persona.core
    assert "私聊" in persona.dm_mode
    assert "群聊" in persona.group_addressed_mode
    assert "脉冲" in persona.pulse_mode
    assert "工具" in persona.tool_use_guide


def test_load_config_parses_all_fields() -> None:
    from project0.agents.learning import load_learning_config

    cfg = load_learning_config(PROMPTS_DIR / "learning.toml")

    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.max_tokens_reply == 2048
    assert cfg.max_tool_iterations == 5
    assert cfg.transcript_window == 10
    assert cfg.sync_interval_seconds == 30
    assert cfg.reminder_interval_seconds == 1800
    assert cfg.intervals_days == [1, 3, 7, 14, 30]
    assert cfg.max_summary_tokens == 800


def test_load_persona_raises_on_missing_section(tmp_path: Path) -> None:
    from project0.agents.learning import load_learning_persona

    md = tmp_path / "bad.md"
    md.write_text("# 学习助手 — 角色设定\njust the core\n", encoding="utf-8")
    with pytest.raises(ValueError, match="模式：私聊"):
        load_learning_persona(md)


def test_load_persona_raises_on_malformed_header(tmp_path: Path) -> None:
    from project0.agents.learning import load_learning_persona

    md = tmp_path / "malformed.md"
    md.write_text(
        "#学习助手 — 角色设定\ncore text\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed section header"):
        load_learning_persona(md)


def test_load_config_raises_on_missing_key(tmp_path: Path) -> None:
    from project0.agents.learning import load_learning_config

    toml_path = tmp_path / "partial.toml"
    toml_path.write_text(
        """
[llm]
model = "test"
max_tokens_reply = 100
max_tool_iterations = 3

[context]
transcript_window = 5
""",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="notion.sync_interval_seconds"):
        load_learning_config(toml_path)


def test_learning_agent_builds_tool_specs() -> None:
    from project0.agents.learning import (
        LearningAgent,
        LearningConfig,
        LearningPersona,
    )

    persona = LearningPersona(
        core="core", dm_mode="dm", group_addressed_mode="group",
        pulse_mode="pulse", tool_use_guide="tools",
    )
    config = LearningConfig(
        model="test", max_tokens_reply=100, max_tool_iterations=3,
        transcript_window=5, sync_interval_seconds=30,
        reminder_interval_seconds=1800, intervals_days=[1, 3, 7],
        max_summary_tokens=400,
    )
    agent = LearningAgent(
        llm=None, notion=None, knowledge_index=None,
        review_schedule=None, messages_store=None,
        persona=persona, config=config,
    )
    names = {s.name for s in agent._tool_specs}
    assert names == {
        "process_link", "process_text", "list_upcoming_reviews",
        "mark_reviewed", "list_entries", "get_entry",
    }


# --- fixtures for tool dispatch / handle routing tests -----------------------

@pytest.fixture
def store() -> Store:
    s = Store(":memory:")
    s.init_schema()
    return s


@pytest.fixture
def knowledge_index(store: Store) -> KnowledgeIndexStore:
    return KnowledgeIndexStore(store.conn)


@pytest.fixture
def review_schedule(store: Store) -> ReviewScheduleStore:
    return ReviewScheduleStore(store.conn)


def _make_agent(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> "LearningAgent":  # type: ignore[name-defined]  # noqa: F821
    from project0.agents.learning import LearningAgent, load_learning_config, load_learning_persona
    persona = load_learning_persona(PROMPTS_DIR / "learning.md")
    config = load_learning_config(PROMPTS_DIR / "learning.toml")
    return LearningAgent(
        llm=None,
        notion=None,
        knowledge_index=knowledge_index,
        review_schedule=review_schedule,
        messages_store=None,
        persona=persona,
        config=config,
    )


# --- tool dispatch tests -----------------------------------------------------

@pytest.mark.asyncio
async def test_list_upcoming_reviews_tool(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    agent = _make_agent(knowledge_index, review_schedule)
    knowledge_index.upsert(
        notion_page_id="page-1", title="Test Entry", source_url=None,
        source_type="text", tags=["python"], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    review_schedule.create("page-1", first_review_date="2026-04-17")

    call = ToolCall(id="call-1", name="list_upcoming_reviews", input={"days_ahead": 365})
    turn_state = TurnState()
    content, is_err = await agent._dispatch_tool(call, turn_state)
    assert not is_err
    data = json.loads(content)
    assert len(data) == 1
    assert data[0]["page_id"] == "page-1"


@pytest.mark.asyncio
async def test_mark_reviewed_tool(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    agent = _make_agent(knowledge_index, review_schedule)
    knowledge_index.upsert(
        notion_page_id="page-1", title="Test Entry", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    review_schedule.create("page-1", first_review_date="2026-04-17")

    call = ToolCall(id="call-1", name="mark_reviewed", input={"page_id": "page-1"})
    turn_state = TurnState()
    content, is_err = await agent._dispatch_tool(call, turn_state)
    assert not is_err
    data = json.loads(content)
    assert data["marked"] == "page-1"


@pytest.mark.asyncio
async def test_list_entries_tool(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    agent = _make_agent(knowledge_index, review_schedule)
    knowledge_index.upsert(
        notion_page_id="page-1", title="Python GIL", source_url=None,
        source_type="text", tags=["python"], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    knowledge_index.upsert(
        notion_page_id="page-2", title="React Hooks", source_url=None,
        source_type="text", tags=["react"], user_notes=None, status="active",
        created_at="2026-04-16T11:00:00Z", last_edited="2026-04-16T11:00:00Z",
    )

    # No filter
    call = ToolCall(id="call-1", name="list_entries", input={})
    turn_state = TurnState()
    content, is_err = await agent._dispatch_tool(call, turn_state)
    assert not is_err
    data = json.loads(content)
    assert len(data) == 2

    # Filter by tag
    call = ToolCall(id="call-2", name="list_entries", input={"tag": "python"})
    content, is_err = await agent._dispatch_tool(call, turn_state)
    assert not is_err
    data = json.loads(content)
    assert len(data) == 1
    assert data[0]["title"] == "Python GIL"


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    agent = _make_agent(knowledge_index, review_schedule)
    call = ToolCall(id="call-1", name="nonexistent_tool", input={})
    turn_state = TurnState()
    content, is_err = await agent._dispatch_tool(call, turn_state)
    assert is_err
    assert "unknown tool" in content


# --- handle routing tests ----------------------------------------------------

@pytest.mark.asyncio
async def test_handle_routing_pulse_notion_sync(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    """notion_sync pulse returns None (silent) when notion client is absent."""
    agent = _make_agent(knowledge_index, review_schedule)
    env = Envelope(
        id=1, ts="2026-04-16T10:00:00Z", parent_id=None,
        source="pulse", telegram_chat_id=None, telegram_msg_id=None,
        received_by_bot=None, from_kind="system", from_agent=None,
        to_agent="learning", body="notion_sync", mentions=[],
        routing_reason="pulse",
        payload={"pulse_name": "notion_sync"},
    )
    result = await agent.handle(env)
    assert result is None


@pytest.mark.asyncio
async def test_handle_routing_unknown_reason(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    agent = _make_agent(knowledge_index, review_schedule)
    env = Envelope(
        id=1, ts="2026-04-16T10:00:00Z", parent_id=None,
        source="telegram_group", telegram_chat_id=123, telegram_msg_id=1,
        received_by_bot=None, from_kind="user", from_agent=None,
        to_agent="learning", body="hello", mentions=[],
        routing_reason="listener_observation",
        payload=None,
    )
    result = await agent.handle(env)
    assert result is None


# --- placeholder notification tests ------------------------------------------

import asyncio
from datetime import UTC, datetime, timedelta

from project0.llm.tools import ToolCall, ToolUseResult


class _CaptureSender:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, *, agent: str, chat_id: int, text: str) -> None:
        self.sent.append({"agent": agent, "chat_id": chat_id, "text": text})


def _make_agent_with_fake_notion(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
    fake_notion,
    fake_llm,
):
    from project0.agents.learning import LearningAgent, load_learning_config, load_learning_persona
    persona = load_learning_persona(PROMPTS_DIR / "learning.md")
    config = load_learning_config(PROMPTS_DIR / "learning.toml")
    return LearningAgent(
        llm=fake_llm,
        notion=fake_notion,
        knowledge_index=knowledge_index,
        review_schedule=review_schedule,
        messages_store=None,
        persona=persona,
        config=config,
    )


def test_process_text_sends_placeholder(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    """process_text must send a 'please wait' placeholder before the LLM call."""
    from project0.agents.learning import LearningAgent, load_learning_config, load_learning_persona
    from project0.notion.model import KnowledgeEntry

    persona = load_learning_persona(PROMPTS_DIR / "learning.md")
    config = load_learning_config(PROMPTS_DIR / "learning.toml")

    summary_response = json.dumps({
        "title": "Test Title",
        "summary": "Test summary.",
        "tags": ["test"],
    })

    class _FakeLLM:
        def __init__(self):
            self.n = 0
        async def complete(self, **kw):
            return summary_response
        async def complete_with_tools(self, *, system, messages, tools,
                                      max_tokens, agent, purpose, envelope_id=None):
            self.n += 1
            if self.n == 1:
                return ToolUseResult(
                    kind="tool_use", text=None,
                    tool_calls=[ToolCall(id="c1", name="process_text",
                                         input={"text": "some content to save"})],
                )
            return ToolUseResult(kind="text", text="好的少爷，已整理好了~", tool_calls=[])

    fake_entry = KnowledgeEntry(
        page_id="page-abc",
        title="Test Title",
        body="Test summary.",
        source_url=None,
        source_type="text",
        tags=["test"],
        user_notes=None,
        status="active",
        created_at=datetime(2026, 4, 18, tzinfo=UTC),
        last_edited=datetime(2026, 4, 18, tzinfo=UTC),
    )

    class _FakeNotion:
        async def create_page(self, **kw):
            return fake_entry

    agent = LearningAgent(
        llm=_FakeLLM(),
        notion=_FakeNotion(),
        knowledge_index=knowledge_index,
        review_schedule=review_schedule,
        messages_store=None,
        persona=persona,
        config=config,
    )

    sender = _CaptureSender()
    agent.set_sender(sender)

    env = Envelope(
        id=None, ts="2026-04-18T04:00:00Z", parent_id=None,
        source="telegram_group", telegram_chat_id=77, telegram_msg_id=10,
        received_by_bot="learning",
        from_kind="user", from_agent=None, to_agent="learning",
        body="帮我整理这段内容",
        routing_reason="mention",
    )
    asyncio.run(agent.handle(env))

    assert len(sender.sent) >= 1
    first = sender.sent[0]
    assert first["agent"] == "learning"
    assert first["chat_id"] == 77
    assert "等一下" in first["text"] or "稍等" in first["text"]


def test_notify_without_sender_is_silent_learning(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    """_notify must no-op when sender hasn't been injected."""
    agent = _make_agent(knowledge_index, review_schedule)
    # No set_sender call; _notify must swallow silently.
    asyncio.run(agent._notify(chat_id=77, text="anything"))
    # No exception; nothing further to assert.
