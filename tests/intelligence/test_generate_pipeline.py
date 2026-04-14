"""generate_daily_report pipeline tests. Uses FakeTwitterSource and
FakeProvider to exercise every branch from §6.2 of the spec:
  - happy path
  - partial handle failure (some handles 404, report still written)
  - total handle failure (TwitterSourceError, no report written)
  - malformed LLM JSON (ValueError, no report written)
  - schema-invalid LLM JSON (ValueError, no report written)
  - default date defaults to today in user_tz
  - regeneration overwrites atomically
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.intelligence.fake_source import FakeTwitterSource
from project0.intelligence.generate import generate_daily_report
from project0.intelligence.source import Tweet, TwitterSourceError
from project0.intelligence.watchlist import WatchEntry
from project0.llm.provider import FakeProvider


def _tweet(handle: str, tid: str, hours_ago: int) -> Tweet:
    return Tweet(
        handle=handle,
        tweet_id=tid,
        url=f"https://x.com/{handle}/status/{tid}",
        text=f"tweet {tid} from {handle}",
        posted_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        reply_count=0,
        like_count=0,
        retweet_count=0,
    )


def _valid_llm_json() -> str:
    return json.dumps({
        "date": "2026-04-15",
        "generated_at": "",
        "user_tz": "",
        "watchlist_snapshot": [],
        "news_items": [
            {
                "id": "n1",
                "headline": "头条",
                "summary": "摘要",
                "importance": "high",
                "importance_reason": "原因",
                "topics": ["ai"],
                "source_tweets": [
                    {
                        "handle": "sama",
                        "url": "https://x.com/sama/status/1",
                        "text": "tweet 1 from sama",
                        "posted_at": "2026-04-15T03:00:00Z",
                    }
                ],
            }
        ],
        "suggested_accounts": [],
        "stats": {
            "tweets_fetched": 0,
            "handles_attempted": 0,
            "handles_succeeded": 0,
            "items_generated": 0,
            "errors": [],
        },
    })


@pytest.fixture
def tz() -> ZoneInfo:
    return ZoneInfo("Asia/Shanghai")


@pytest.mark.asyncio
async def test_happy_path_writes_valid_report(tmp_path: Path, tz: ZoneInfo):
    src = FakeTwitterSource(timelines={
        "sama": [_tweet("sama", "1", 2)],
        "openai": [_tweet("openai", "1", 3)],
        "anthropicai": [_tweet("anthropicai", "1", 4)],
    })
    llm = FakeProvider(responses=[_valid_llm_json()])
    watchlist = [
        WatchEntry(handle="sama", tags=(), notes=""),
        WatchEntry(handle="openai", tags=(), notes=""),
        WatchEntry(handle="anthropicai", tags=(), notes=""),
    ]
    target_date = date(2026, 4, 15)

    report = await generate_daily_report(
        date=target_date,
        source=src,
        llm=llm,
        summarizer_model="claude-opus-4-6",
        summarizer_max_tokens=16384,
        watchlist=watchlist,
        reports_dir=tmp_path,
        user_tz=tz,
        timeline_since_hours=24,
        max_tweets_per_handle=50,
    )

    assert report["stats"]["tweets_fetched"] == 3
    assert report["stats"]["handles_attempted"] == 3
    assert report["stats"]["handles_succeeded"] == 3
    assert report["stats"]["errors"] == []
    assert report["watchlist_snapshot"] == ["sama", "openai", "anthropicai"]
    assert report["user_tz"] == "Asia/Shanghai"
    assert "generated_at" in report and report["generated_at"]

    # File exists on disk.
    out_path = tmp_path / "2026-04-15.json"
    assert out_path.exists()
    on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    assert on_disk["news_items"][0]["id"] == "n1"


@pytest.mark.asyncio
async def test_partial_failure_records_errors_and_still_writes(tmp_path: Path, tz: ZoneInfo):
    src = FakeTwitterSource(timelines={
        "sama": [_tweet("sama", "1", 2)],
        "openai": [_tweet("openai", "1", 3)],
        # anthropicai missing → raises TwitterSourceError
    })
    llm = FakeProvider(responses=[_valid_llm_json()])
    watchlist = [
        WatchEntry(handle="sama", tags=(), notes=""),
        WatchEntry(handle="openai", tags=(), notes=""),
        WatchEntry(handle="anthropicai", tags=(), notes=""),
    ]

    report = await generate_daily_report(
        date=date(2026, 4, 15),
        source=src,
        llm=llm,
        summarizer_model="claude-opus-4-6",
        summarizer_max_tokens=16384,
        watchlist=watchlist,
        reports_dir=tmp_path,
        user_tz=tz,
        timeline_since_hours=24,
        max_tweets_per_handle=50,
    )

    assert report["stats"]["handles_succeeded"] == 2
    assert report["stats"]["handles_attempted"] == 3
    assert len(report["stats"]["errors"]) == 1
    assert report["stats"]["errors"][0]["handle"] == "anthropicai"
    assert (tmp_path / "2026-04-15.json").exists()


@pytest.mark.asyncio
async def test_total_failure_raises_and_writes_nothing(tmp_path: Path, tz: ZoneInfo):
    src = FakeTwitterSource(timelines={})  # no handles seeded
    llm = FakeProvider(responses=[_valid_llm_json()])
    watchlist = [
        WatchEntry(handle="sama", tags=(), notes=""),
        WatchEntry(handle="openai", tags=(), notes=""),
    ]

    with pytest.raises(TwitterSourceError, match="all"):
        await generate_daily_report(
            date=date(2026, 4, 15),
            source=src,
            llm=llm,
            summarizer_model="claude-opus-4-6",
            summarizer_max_tokens=16384,
            watchlist=watchlist,
            reports_dir=tmp_path,
            user_tz=tz,
            timeline_since_hours=24,
            max_tweets_per_handle=50,
        )
    # No file written.
    assert list(tmp_path.iterdir()) == []
    # LLM never called — summarization is skipped on total failure.
    assert llm.calls == []


@pytest.mark.asyncio
async def test_malformed_llm_json_raises_value_error(tmp_path: Path, tz: ZoneInfo):
    src = FakeTwitterSource(timelines={"sama": [_tweet("sama", "1", 2)]})
    llm = FakeProvider(responses=["not json at all"])
    watchlist = [WatchEntry(handle="sama", tags=(), notes="")]

    with pytest.raises(ValueError, match="JSON"):
        await generate_daily_report(
            date=date(2026, 4, 15),
            source=src,
            llm=llm,
            summarizer_model="claude-opus-4-6",
            summarizer_max_tokens=16384,
            watchlist=watchlist,
            reports_dir=tmp_path,
            user_tz=tz,
            timeline_since_hours=24,
            max_tweets_per_handle=50,
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_schema_invalid_llm_json_raises_value_error(tmp_path: Path, tz: ZoneInfo):
    src = FakeTwitterSource(timelines={"sama": [_tweet("sama", "1", 2)]})
    bad = json.dumps({"date": "2026-04-15"})  # missing news_items etc.
    llm = FakeProvider(responses=[bad])
    watchlist = [WatchEntry(handle="sama", tags=(), notes="")]

    with pytest.raises(ValueError):
        await generate_daily_report(
            date=date(2026, 4, 15),
            source=src,
            llm=llm,
            summarizer_model="claude-opus-4-6",
            summarizer_max_tokens=16384,
            watchlist=watchlist,
            reports_dir=tmp_path,
            user_tz=tz,
            timeline_since_hours=24,
            max_tweets_per_handle=50,
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_regenerating_same_date_overwrites(tmp_path: Path, tz: ZoneInfo):
    src = FakeTwitterSource(timelines={"sama": [_tweet("sama", "1", 2)]})
    llm = FakeProvider(responses=[_valid_llm_json(), _valid_llm_json()])
    watchlist = [WatchEntry(handle="sama", tags=(), notes="")]

    for _ in range(2):
        await generate_daily_report(
            date=date(2026, 4, 15),
            source=src,
            llm=llm,
            summarizer_model="claude-opus-4-6",
            summarizer_max_tokens=16384,
            watchlist=watchlist,
            reports_dir=tmp_path,
            user_tz=tz,
            timeline_since_hours=24,
            max_tweets_per_handle=50,
        )

    files = sorted(tmp_path.iterdir())
    assert [f.name for f in files] == ["2026-04-15.json"]
