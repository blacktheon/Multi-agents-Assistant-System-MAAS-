"""Task 13: get_report_item tool — Intelligence fetches a single item's
full content on demand, so the cached system prompt only needs the slim
headline index."""
from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.agents._tool_loop import TurnState
from project0.agents.intelligence import (
    Intelligence,
    IntelligenceConfig,
    IntelligencePersona,
    get_report_item_tool_spec,
)
from project0.intelligence.fake_source import FakeTwitterSource
from project0.llm.provider import FakeProvider
from project0.llm.tools import ToolCall


def _persona() -> IntelligencePersona:
    return IntelligencePersona(
        core="情报 core",
        dm_mode="dm",
        group_addressed_mode="group",
        delegated_mode="delegated",
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


def _valid_item(item_id: str, summary: str) -> dict:
    return {
        "id": item_id,
        "headline": "h",
        "summary": summary,
        "importance": "high",
        "importance_reason": "r",
        "topics": ["ai"],
        "source_tweets": [
            {
                "handle": "sama",
                "url": "https://x.com/sama/status/1",
                "text": "t",
                "posted_at": "2026-04-16T03:00:00Z",
            }
        ],
    }


def _seed(tmp_path: Path, date_str: str = "2026-04-16") -> Path:
    d = tmp_path / "intelligence" / "reports"
    d.mkdir(parents=True)
    report = {
        "date": date_str,
        "generated_at": f"{date_str}T08:00:00+08:00",
        "user_tz": "Asia/Shanghai",
        "watchlist_snapshot": [],
        "news_items": [
            _valid_item("r01", "full A summary"),
            _valid_item("r02", "full B summary"),
        ],
        "suggested_accounts": [],
        "stats": {
            "tweets_fetched": 0,
            "handles_attempted": 0,
            "handles_succeeded": 0,
            "items_generated": 2,
            "errors": [],
        },
    }
    (d / f"{date_str}.json").write_text(json.dumps(report), encoding="utf-8")
    return tmp_path


def _make_intelligence(data_dir: Path) -> Intelligence:
    reports_dir = data_dir / "intelligence" / "reports"
    return Intelligence(
        llm_summarizer=FakeProvider(responses=[]),
        llm_qa=FakeProvider(tool_responses=[]),
        twitter=FakeTwitterSource(timelines={}),
        messages_store=None,
        persona=_persona(),
        config=_config(),
        watchlist=[],
        reports_dir=reports_dir,
        user_tz=ZoneInfo("Asia/Shanghai"),
        public_base_url="http://test.local:8080",
    )


def test_tool_spec_advertises_schema() -> None:
    spec = get_report_item_tool_spec()
    assert spec.name == "get_report_item"
    assert "item_id" in spec.input_schema["properties"]
    assert "item_id" in spec.input_schema["required"]


def test_tool_spec_is_in_intelligence_tool_list(tmp_path: Path) -> None:
    _seed(tmp_path)
    intel = _make_intelligence(tmp_path)
    names = [t.name for t in intel._tool_specs]
    assert "get_report_item" in names


@pytest.mark.asyncio
async def test_get_report_item_returns_full_item(tmp_path: Path) -> None:
    intel = _make_intelligence(_seed(tmp_path))
    call = ToolCall(
        id="tc1",
        name="get_report_item",
        input={"item_id": "r01", "date": "2026-04-16"},
    )
    result_str, is_err = await intel._dispatch_tool(call, TurnState())
    assert not is_err
    result = json.loads(result_str)
    assert result["id"] == "r01"
    assert result["summary"] == "full A summary"


@pytest.mark.asyncio
async def test_get_report_item_missing_report_returns_error(tmp_path: Path) -> None:
    intel = _make_intelligence(_seed(tmp_path))
    call = ToolCall(
        id="tc2",
        name="get_report_item",
        input={"item_id": "r01", "date": "2020-01-01"},
    )
    result_str, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err
    result = json.loads(result_str)
    assert "no report" in result["error"]


@pytest.mark.asyncio
async def test_get_report_item_missing_item_returns_error(tmp_path: Path) -> None:
    intel = _make_intelligence(_seed(tmp_path))
    call = ToolCall(
        id="tc3",
        name="get_report_item",
        input={"item_id": "r99", "date": "2026-04-16"},
    )
    result_str, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err
    result = json.loads(result_str)
    assert "r99" in result["error"]


@pytest.mark.asyncio
async def test_get_report_item_defaults_to_today(tmp_path: Path) -> None:
    # Seed with today's date in the user_tz used by the agent.
    from datetime import datetime
    today = datetime.now(tz=ZoneInfo("Asia/Shanghai")).date().isoformat()
    intel = _make_intelligence(_seed(tmp_path, date_str=today))
    call = ToolCall(
        id="tc4",
        name="get_report_item",
        input={"item_id": "r02"},
    )
    result_str, is_err = await intel._dispatch_tool(call, TurnState())
    assert not is_err
    result = json.loads(result_str)
    assert result["id"] == "r02"
    assert result["summary"] == "full B summary"
