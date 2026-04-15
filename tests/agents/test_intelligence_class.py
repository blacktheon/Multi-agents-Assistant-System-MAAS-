"""Intelligence agentic-turn tests. Drives Intelligence.handle end-to-end
using FakeProvider scripted tool_responses. Covers:
  - DM no-report content question → plain text reply
  - DM generation request → tool call succeeds → ack text
  - DM with latest report → plain text reply from context
  - DM 'yesterday' → get_report tool call → reply
  - delegated turn (default_manager routing)
  - LLM error → returns None
  - iteration overflow → LLMProviderError
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.agents.intelligence import (
    Intelligence,
    IntelligenceConfig,
    IntelligencePersona,
)
from project0.envelope import Envelope
from project0.intelligence.fake_source import FakeTwitterSource
from project0.intelligence.report import atomic_write_json
from project0.intelligence.source import Tweet
from project0.intelligence.watchlist import WatchEntry
from project0.llm.provider import FakeProvider, LLMProviderError
from project0.llm.tools import ToolCall, ToolUseResult


def _persona() -> IntelligencePersona:
    return IntelligencePersona(
        core="core",
        dm_mode="dm",
        group_addressed_mode="group",
        delegated_mode="del",
        tool_use_guide="tools",
    )


def _config(max_iter: int = 6) -> IntelligenceConfig:
    return IntelligenceConfig(
        summarizer_model="claude-opus-4-6",
        summarizer_max_tokens=16384,
        summarizer_thinking_budget=None,
        qa_model="claude-sonnet-4-6",
        qa_max_tokens=2048,
        transcript_window=10,
        max_tool_iterations=max_iter,
        timeline_since_hours=24,
        max_tweets_per_handle=50,
    )


def _tweet(handle: str, tid: str, hours_ago: int) -> Tweet:
    return Tweet(
        handle=handle, tweet_id=tid, url=f"https://x.com/{handle}/status/{tid}",
        text=f"t{tid}",
        posted_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        reply_count=0, like_count=0, retweet_count=0,
    )


def _valid_report_dict(date_str: str = "2026-04-15") -> dict:
    return {
        "date": date_str,
        "generated_at": f"{date_str}T08:00:00+08:00",
        "user_tz": "Asia/Shanghai",
        "watchlist_snapshot": ["sama"],
        "news_items": [
            {
                "id": "n1", "headline": "h", "summary": "s",
                "importance": "high", "importance_reason": "r",
                "topics": ["ai"],
                "source_tweets": [{"handle": "sama", "url": "https://x.com/sama/status/1", "text": "t", "posted_at": "2026-04-15T03:00:00Z"}],
            }
        ],
        "suggested_accounts": [],
        "stats": {
            "tweets_fetched": 1, "handles_attempted": 1, "handles_succeeded": 1,
            "items_generated": 1, "errors": [],
        },
    }


class _StubMessagesStore:
    """Minimal MessagesStore stand-in returning an empty transcript."""
    def recent_for_chat(self, *, chat_id: int, limit: int) -> list[Envelope]:
        return []

    def recent_for_dm(self, *, chat_id: int, agent: str, limit: int) -> list[Envelope]:
        return []


def _build_intelligence(
    tmp_path: Path,
    *,
    qa_tool_responses: list[ToolUseResult],
    summarizer_responses: list[str] | None = None,
) -> Intelligence:
    src = FakeTwitterSource(timelines={"sama": [_tweet("sama", "1", 2)]})
    llm_summarizer = FakeProvider(responses=summarizer_responses or [json.dumps(_valid_report_dict())])
    llm_qa = FakeProvider(tool_responses=qa_tool_responses)
    return Intelligence(
        llm_summarizer=llm_summarizer,
        llm_qa=llm_qa,
        twitter=src,
        messages_store=_StubMessagesStore(),
        persona=_persona(),
        config=_config(),
        watchlist=[WatchEntry(handle="sama", tags=(), notes="")],
        reports_dir=tmp_path,
        user_tz=ZoneInfo("Asia/Shanghai"),
        public_base_url="http://test.local:8080",
    )


def _dm_envelope(body: str) -> Envelope:
    return Envelope(
        id=None,
        ts="2026-04-15T10:00:00Z",
        parent_id=None,
        source="telegram_dm",
        from_kind="user",
        from_agent=None,
        to_agent="intelligence",
        routing_reason="direct_dm",
        telegram_chat_id=42,
        telegram_msg_id=1,
        received_by_bot="intelligence",
        body=body,
        payload=None,
        mentions=[],
    )


@pytest.mark.asyncio
async def test_dm_no_report_content_question_replies_plain_text(tmp_path: Path):
    intel = _build_intelligence(
        tmp_path,
        qa_tool_responses=[
            ToolUseResult(kind="text", text="目前还没有日报，要我现在生成一份吗？", tool_calls=[], stop_reason="end_turn"),
        ],
    )
    result = await intel.handle(_dm_envelope("今天有什么 AI 消息？"))
    assert result is not None
    assert result.reply_text and "日报" in result.reply_text
    assert result.delegate_to is None


@pytest.mark.asyncio
async def test_dm_generation_request_runs_tool_then_acks(tmp_path: Path):
    intel = _build_intelligence(
        tmp_path,
        qa_tool_responses=[
            ToolUseResult(
                kind="tool_use", text=None,
                tool_calls=[ToolCall(id="t1", name="generate_daily_report", input={"date": "2026-04-15"})],
                stop_reason="tool_use",
            ),
            ToolUseResult(kind="text", text="好，今天的日报写好了。", tool_calls=[], stop_reason="end_turn"),
        ],
    )
    result = await intel.handle(_dm_envelope("生成今天的报告"))
    assert result is not None
    assert result.reply_text and "日报" in result.reply_text
    assert (tmp_path / "2026-04-15.json").exists()


@pytest.mark.asyncio
async def test_dm_with_latest_report_replies_from_context(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-15.json", _valid_report_dict("2026-04-15"))
    intel = _build_intelligence(
        tmp_path,
        qa_tool_responses=[
            ToolUseResult(kind="text", text="今天最要紧的是 n1。", tool_calls=[], stop_reason="end_turn"),
        ],
    )
    result = await intel.handle(_dm_envelope("今天有什么？"))
    assert result is not None
    assert "n1" in result.reply_text
    # Should not have called the summarizer provider.
    assert intel._llm_summarizer.calls == []
    # Verify the report's slim headline index was actually injected into
    # the cached system prompt — without this check the test would pass
    # even if injection broke, because the "n1" in reply_text only proves
    # the scripted FakeProvider returned that string, not that the model
    # saw the report. Task 12 moved the report index from the per-turn
    # user message into the stable system segment.
    assert len(intel._llm_qa.tool_calls_log) == 1
    system_str = intel._llm_qa.tool_calls_log[0]["system"]
    assert "[n1]" in system_str
    assert "2026-04-15" in system_str


@pytest.mark.asyncio
async def test_dm_yesterday_triggers_get_report(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-14.json", _valid_report_dict("2026-04-14"))
    atomic_write_json(tmp_path / "2026-04-15.json", _valid_report_dict("2026-04-15"))
    intel = _build_intelligence(
        tmp_path,
        qa_tool_responses=[
            ToolUseResult(
                kind="tool_use", text=None,
                tool_calls=[ToolCall(id="t1", name="get_report", input={"date": "2026-04-14"})],
                stop_reason="tool_use",
            ),
            ToolUseResult(kind="text", text="昨天的重点是...", tool_calls=[], stop_reason="end_turn"),
        ],
    )
    result = await intel.handle(_dm_envelope("昨天有什么？"))
    assert result is not None
    assert result.reply_text.startswith("昨天的重点")


@pytest.mark.asyncio
async def test_delegated_turn_routes_through_delegated_mode(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-15.json", _valid_report_dict("2026-04-15"))
    intel = _build_intelligence(
        tmp_path,
        qa_tool_responses=[
            ToolUseResult(kind="text", text="经理交代的事我看过日报了，n1 条和你问的相关。", tool_calls=[], stop_reason="end_turn"),
        ],
    )
    env = Envelope(
        id=None,
        ts="2026-04-15T10:00:00Z",
        parent_id=None,
        source="telegram_dm",
        from_kind="agent",
        from_agent="manager",
        to_agent="intelligence",
        routing_reason="default_manager",
        telegram_chat_id=42,
        telegram_msg_id=2,
        received_by_bot="manager",
        body="(delegated)",
        payload={"kind": "query", "query": "o5 的发布怎么样？"},
        mentions=[],
    )
    result = await intel.handle(env)
    assert result is not None
    assert "n1" in result.reply_text


@pytest.mark.asyncio
async def test_llm_error_returns_none(tmp_path: Path):
    intel = _build_intelligence(tmp_path, qa_tool_responses=[])  # exhausted → LLMProviderError
    result = await intel.handle(_dm_envelope("hi"))
    assert result is None


@pytest.mark.asyncio
async def test_iteration_overflow_raises(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-15.json", _valid_report_dict("2026-04-15"))
    # Force the model to keep calling tools forever → exceeds max_tool_iterations.
    infinite = [
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(id=f"t{i}", name="list_reports", input={"limit": 1})],
            stop_reason="tool_use",
        )
        for i in range(20)
    ]
    intel = Intelligence(
        llm_summarizer=FakeProvider(responses=[]),
        llm_qa=FakeProvider(tool_responses=infinite),
        twitter=FakeTwitterSource(timelines={}),
        messages_store=_StubMessagesStore(),
        persona=_persona(),
        config=_config(max_iter=3),
        watchlist=[],
        reports_dir=tmp_path,
        user_tz=ZoneInfo("Asia/Shanghai"),
        public_base_url="http://test.local:8080",
    )
    with pytest.raises(LLMProviderError, match="max_iterations"):
        await intel.handle(_dm_envelope("hi"))


@pytest.mark.asyncio
async def test_unknown_routing_reason_returns_none(tmp_path: Path):
    intel = _build_intelligence(tmp_path, qa_tool_responses=[])
    env = Envelope(
        id=None,
        ts="2026-04-15T10:00:00Z",
        parent_id=None,
        source="telegram_group",
        from_kind="user",
        from_agent=None,
        to_agent="intelligence",
        routing_reason="listener_observation",
        telegram_chat_id=42,
        telegram_msg_id=1,
        received_by_bot="intelligence",
        body="random group chatter",
        payload=None,
        mentions=[],
    )
    result = await intel.handle(env)
    assert result is None
