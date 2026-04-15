"""Summarizer + Q&A prompt builder tests. Verifies tweet grouping,
ordering, error rendering, and the date-staleness hints the Q&A prompt
injects so the model knows whether 'today's report' is actually from
today."""
from __future__ import annotations

from datetime import UTC, date, datetime

from project0.envelope import Envelope
from project0.intelligence.source import Tweet
from project0.intelligence.summarizer_prompt import (
    SUMMARIZER_SYSTEM_PROMPT,
    build_delegated_user_prompt,
    build_qa_user_prompt,
    build_user_prompt,
)


def _t(handle: str, tid: str, posted_at: datetime, text: str = "body") -> Tweet:
    return Tweet(
        handle=handle,
        tweet_id=tid,
        url=f"https://x.com/{handle}/status/{tid}",
        text=text,
        posted_at=posted_at,
        reply_count=0,
        like_count=0,
        retweet_count=0,
    )


def test_summarizer_system_prompt_mentions_json_and_schema():
    assert "JSON" in SUMMARIZER_SYSTEM_PROMPT
    assert "news_items" in SUMMARIZER_SYSTEM_PROMPT
    assert "importance" in SUMMARIZER_SYSTEM_PROMPT
    assert "high" in SUMMARIZER_SYSTEM_PROMPT
    assert "suggested_accounts" in SUMMARIZER_SYSTEM_PROMPT


def test_build_user_prompt_groups_by_handle_newest_first():
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    tweets = [
        _t("sama", "s-old", now.replace(hour=1), "old"),
        _t("openai", "o1", now.replace(hour=5), "ship it"),
        _t("sama", "s-new", now.replace(hour=10), "new"),
    ]
    out = build_user_prompt(
        raw_tweets=tweets,
        watchlist_snapshot=["openai", "sama", "anthropicai"],
        errors=[],
        today_local=date(2026, 4, 15),
        user_tz_name="Asia/Shanghai",
    )
    assert "2026-04-15" in out
    assert "Asia/Shanghai" in out
    assert "@openai" in out
    assert "@sama" in out
    # Newest within a handle comes first.
    sama_block_start = out.index("@sama")
    assert out.index("s-new", sama_block_start) < out.index("s-old", sama_block_start)


def test_build_user_prompt_omits_handles_with_no_tweets():
    out = build_user_prompt(
        raw_tweets=[_t("sama", "s1", datetime(2026, 4, 15, 10, tzinfo=UTC))],
        watchlist_snapshot=["sama", "ghost"],
        errors=[],
        today_local=date(2026, 4, 15),
        user_tz_name="UTC",
    )
    assert "@sama" in out
    assert "@ghost" not in out


def test_build_user_prompt_renders_errors():
    out = build_user_prompt(
        raw_tweets=[_t("sama", "s1", datetime(2026, 4, 15, 10, tzinfo=UTC))],
        watchlist_snapshot=["sama", "flaky"],
        errors=[{"handle": "flaky", "error": "HTTP 404"}],
        today_local=date(2026, 4, 15),
        user_tz_name="UTC",
    )
    assert "flaky" in out
    assert "HTTP 404" in out


def test_build_qa_user_prompt_references_report_date_and_slim_index():
    latest = {"date": "2026-04-15", "news_items": [
        {"id": "n1", "headline": "h", "summary": "DO NOT INLINE ME"}
    ]}
    out = build_qa_user_prompt(
        latest_report=latest,
        current_date_local=date(2026, 4, 15),
        recent_messages=[],
        current_user_message="今天有什么 AI 消息？",
    )
    assert "2026-04-15" in out
    assert "get_report_item" in out
    assert "今天有什么 AI 消息？" in out
    # Full item payload must NOT be inlined — slim index lives in the
    # cached system prompt instead (see test_intelligence_slim_report).
    assert "DO NOT INLINE ME" not in out


def test_build_qa_user_prompt_flags_stale_report():
    latest = {"date": "2026-04-10", "news_items": []}
    out = build_qa_user_prompt(
        latest_report=latest,
        current_date_local=date(2026, 4, 15),
        recent_messages=[],
        current_user_message="news?",
    )
    assert "2026-04-10" in out
    assert "2026-04-15" in out


def test_build_qa_user_prompt_with_no_report():
    out = build_qa_user_prompt(
        latest_report=None,
        current_date_local=date(2026, 4, 15),
        recent_messages=[],
        current_user_message="news?",
    )
    assert "没有" in out or "no report" in out.lower()


def test_build_delegated_user_prompt_includes_query():
    out = build_delegated_user_prompt(
        latest_report={"date": "2026-04-15", "news_items": []},
        current_date_local=date(2026, 4, 15),
        query="帮我查一下 o5 发布的情况",
    )
    assert "o5" in out
    assert "2026-04-15" in out
