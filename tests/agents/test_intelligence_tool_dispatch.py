"""Intelligence._dispatch_tool unit tests. Each tool is exercised in
isolation by constructing a minimal Intelligence with stub LLMs, a
FakeTwitterSource, and a tmp reports_dir. The tool-use loop itself is
tested separately in test_intelligence_class.py (Task 12)."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.agents._tool_loop import TurnState
from project0.agents.intelligence import (
    Intelligence,
    IntelligenceConfig,
    IntelligencePersona,
)
from project0.intelligence.fake_source import FakeTwitterSource
from project0.intelligence.report import atomic_write_json
from project0.intelligence.source import Tweet
from project0.intelligence.watchlist import WatchEntry
from project0.llm.provider import FakeProvider
from project0.llm.tools import ToolCall


def _persona() -> IntelligencePersona:
    return IntelligencePersona(
        core="core",
        dm_mode="dm",
        group_addressed_mode="group",
        delegated_mode="del",
        tool_use_guide="tools",
    )


def _config() -> IntelligenceConfig:
    return IntelligenceConfig(
        summarizer_model="claude-opus-4-6",
        summarizer_max_tokens=16384,
        summarizer_thinking_budget=None,
        qa_model="claude-sonnet-4-6",
        qa_max_tokens=2048,
        transcript_window=10,
        max_tool_iterations=6,
        timeline_since_hours=24,
        max_tweets_per_handle=50,
    )


def _tweet(handle: str, tid: str, hours_ago: int) -> Tweet:
    return Tweet(
        handle=handle,
        tweet_id=tid,
        url=f"https://x.com/{handle}/status/{tid}",
        text=f"t{tid}",
        posted_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        reply_count=0,
        like_count=0,
        retweet_count=0,
    )


def _valid_report_dict(date_str: str = "2026-04-15") -> dict:
    return {
        "date": date_str,
        "generated_at": f"{date_str}T08:00:00+08:00",
        "user_tz": "Asia/Shanghai",
        "watchlist_snapshot": ["sama"],
        "news_items": [
            {
                "id": "n1",
                "headline": "h",
                "summary": "s",
                "importance": "high",
                "importance_reason": "r",
                "topics": ["ai"],
                "source_tweets": [
                    {"handle": "sama", "url": "https://x.com/sama/status/1", "text": "t", "posted_at": "2026-04-15T03:00:00Z"},
                ],
            }
        ],
        "suggested_accounts": [],
        "stats": {
            "tweets_fetched": 1,
            "handles_attempted": 1,
            "handles_succeeded": 1,
            "items_generated": 1,
            "errors": [],
        },
    }


def _build_intelligence(
    tmp_path: Path,
    *,
    src: FakeTwitterSource | None = None,
    public_base_url: str = "http://test.local:8080",
) -> Intelligence:
    if src is None:
        src = FakeTwitterSource(timelines={"sama": [_tweet("sama", "1", 2)]})
    llm_summarizer = FakeProvider(responses=[json.dumps(_valid_report_dict())])
    llm_qa = FakeProvider(tool_responses=[])
    return Intelligence(
        llm_summarizer=llm_summarizer,
        llm_qa=llm_qa,
        twitter=src,
        messages_store=None,
        persona=_persona(),
        config=_config(),
        watchlist=[WatchEntry(handle="sama", tags=(), notes="")],
        reports_dir=tmp_path,
        user_tz=ZoneInfo("Asia/Shanghai"),
        public_base_url=public_base_url,
    )


@pytest.mark.asyncio
async def test_generate_daily_report_tool_writes_file(tmp_path: Path):
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="generate_daily_report", input={"date": "2026-04-15"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    data = json.loads(content)
    assert data["path"].endswith("2026-04-15.json")
    assert data["item_count"] == 1
    assert (tmp_path / "2026-04-15.json").exists()


@pytest.mark.asyncio
async def test_generate_daily_report_default_date(tmp_path: Path):
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="generate_daily_report", input={})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    assert (tmp_path / f"{today}.json").exists()


@pytest.mark.asyncio
async def test_generate_daily_report_total_failure_returns_error(tmp_path: Path):
    src = FakeTwitterSource(timelines={})  # empty → total failure
    intel = _build_intelligence(tmp_path, src=src)
    call = ToolCall(id="t1", name="generate_daily_report", input={"date": "2026-04-15"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is True
    assert "all" in content.lower() or "twitter" in content.lower()
    assert not (tmp_path / "2026-04-15.json").exists()


@pytest.mark.asyncio
async def test_get_latest_report_no_reports(tmp_path: Path):
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="get_latest_report", input={})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    assert "no reports" in content.lower()


@pytest.mark.asyncio
async def test_get_latest_report_returns_most_recent(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-14.json", _valid_report_dict("2026-04-14"))
    atomic_write_json(tmp_path / "2026-04-15.json", _valid_report_dict("2026-04-15"))
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="get_latest_report", input={})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    data = json.loads(content)
    assert data["date"] == "2026-04-15"


@pytest.mark.asyncio
async def test_get_report_by_date(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-14.json", _valid_report_dict("2026-04-14"))
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="get_report", input={"date": "2026-04-14"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    data = json.loads(content)
    assert data["date"] == "2026-04-14"


@pytest.mark.asyncio
async def test_get_report_missing_date_returns_not_found(tmp_path: Path):
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="get_report", input={"date": "2020-01-01"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    assert "no report" in content.lower()


@pytest.mark.asyncio
async def test_list_reports_returns_sorted_desc(tmp_path: Path):
    for d in ["2026-04-13", "2026-04-15", "2026-04-14"]:
        atomic_write_json(tmp_path / f"{d}.json", _valid_report_dict(d))
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="list_reports", input={"limit": 5})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    data = json.loads(content)
    assert [e["date"] for e in data] == ["2026-04-15", "2026-04-14", "2026-04-13"]


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(tmp_path: Path):
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="nonexistent_tool", input={})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is True
    assert "unknown" in content.lower()


# --- get_report_link (6e) ----------------------------------------------------


@pytest.mark.asyncio
async def test_get_report_link_latest_picks_newest(tmp_path: Path):
    for d in ["2026-04-10", "2026-04-15"]:
        atomic_write_json(tmp_path / f"{d}.json", _valid_report_dict(d))
    intel = _build_intelligence(tmp_path, public_base_url="http://host:8080")
    call = ToolCall(id="t1", name="get_report_link", input={"date": "latest"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    parsed = json.loads(content)
    assert parsed["url"] == "http://host:8080/reports/2026-04-15"
    assert parsed["date"] == "2026-04-15"


@pytest.mark.asyncio
async def test_get_report_link_specific_date_exists(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-15.json", _valid_report_dict("2026-04-15"))
    intel = _build_intelligence(tmp_path, public_base_url="http://host:8080")
    call = ToolCall(id="t1", name="get_report_link", input={"date": "2026-04-15"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    parsed = json.loads(content)
    assert parsed["url"] == "http://host:8080/reports/2026-04-15"


@pytest.mark.asyncio
async def test_get_report_link_nonexistent_date_returns_error(tmp_path: Path):
    intel = _build_intelligence(tmp_path, public_base_url="http://host:8080")
    call = ToolCall(id="t1", name="get_report_link", input={"date": "2020-01-01"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is True
    assert "2020-01-01" in content


@pytest.mark.asyncio
async def test_get_report_link_invalid_date_format_returns_error(tmp_path: Path):
    intel = _build_intelligence(tmp_path, public_base_url="http://host:8080")
    call = ToolCall(id="t1", name="get_report_link", input={"date": "not-a-date"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is True
    assert "not-a-date" in content or "invalid" in content.lower()


@pytest.mark.asyncio
async def test_get_report_link_latest_with_no_reports_returns_error(tmp_path: Path):
    intel = _build_intelligence(tmp_path, public_base_url="http://host:8080")
    call = ToolCall(id="t1", name="get_report_link", input={"date": "latest"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is True
    assert "no reports" in content.lower() or "generate" in content.lower()


# --- ensure_today_report (6e daily pulse) ------------------------------------


@pytest.mark.asyncio
async def test_ensure_today_report_skips_when_report_exists(tmp_path: Path):
    """If today's report file is already on disk, the pulse must skip
    generation and return False — we don't want a pulse tick to clobber
    a manually-generated report."""
    from datetime import datetime
    from zoneinfo import ZoneInfo as _ZI

    today = datetime.now(tz=_ZI("Asia/Shanghai")).date()
    atomic_write_json(tmp_path / f"{today.isoformat()}.json", _valid_report_dict(today.isoformat()))
    intel = _build_intelligence(tmp_path)

    generated = await intel.ensure_today_report()
    assert generated is False


@pytest.mark.asyncio
async def test_ensure_today_report_generates_when_missing(tmp_path: Path):
    """When no file exists for today, the pulse must call
    generate_daily_report and return True."""
    intel = _build_intelligence(tmp_path)
    from datetime import datetime
    from zoneinfo import ZoneInfo as _ZI

    today = datetime.now(tz=_ZI("Asia/Shanghai")).date()
    assert not (tmp_path / f"{today.isoformat()}.json").exists()

    generated = await intel.ensure_today_report()
    assert generated is True
    assert (tmp_path / f"{today.isoformat()}.json").exists()


@pytest.mark.asyncio
async def test_get_report_link_trims_trailing_slash_on_base_url(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-15.json", _valid_report_dict("2026-04-15"))
    intel = _build_intelligence(tmp_path, public_base_url="http://host:8080/")
    call = ToolCall(id="t1", name="get_report_link", input={"date": "2026-04-15"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    parsed = json.loads(content)
    assert parsed["url"] == "http://host:8080/reports/2026-04-15"
