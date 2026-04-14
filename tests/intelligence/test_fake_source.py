"""FakeTwitterSource behavior tests. The fake must: return seeded tweets,
filter by ``since``, respect ``max_results``, and raise TwitterSourceError
on unknown handles so tests that exercise the per-handle failure path
actually hit the error branch in generate_daily_report."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from project0.intelligence.fake_source import FakeTwitterSource
from project0.intelligence.source import Tweet, TwitterSourceError


def _tweet(handle: str, tid: str, posted_at: datetime, text: str = "t") -> Tweet:
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


@pytest.mark.asyncio
async def test_fetch_user_timeline_returns_seeded_tweets():
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    src = FakeTwitterSource(
        timelines={
            "sama": [
                _tweet("sama", "1", now - timedelta(hours=1)),
                _tweet("sama", "2", now - timedelta(hours=2)),
            ]
        }
    )
    got = await src.fetch_user_timeline("sama", since=now - timedelta(hours=3), max_results=10)
    assert [t.tweet_id for t in got] == ["1", "2"]


@pytest.mark.asyncio
async def test_fetch_user_timeline_filters_by_since():
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    src = FakeTwitterSource(
        timelines={
            "sama": [
                _tweet("sama", "recent", now - timedelta(hours=1)),
                _tweet("sama", "old", now - timedelta(days=5)),
            ]
        }
    )
    got = await src.fetch_user_timeline("sama", since=now - timedelta(hours=2), max_results=10)
    assert [t.tweet_id for t in got] == ["recent"]


@pytest.mark.asyncio
async def test_fetch_user_timeline_respects_max_results():
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    src = FakeTwitterSource(
        timelines={
            "sama": [_tweet("sama", str(i), now - timedelta(minutes=i)) for i in range(10)]
        }
    )
    got = await src.fetch_user_timeline("sama", since=now - timedelta(days=1), max_results=3)
    assert len(got) == 3
    # Newest first.
    assert [t.tweet_id for t in got] == ["0", "1", "2"]


@pytest.mark.asyncio
async def test_unknown_handle_raises_twitter_source_error():
    src = FakeTwitterSource(timelines={})
    with pytest.raises(TwitterSourceError, match="unknown handle"):
        await src.fetch_user_timeline("nobody", since=datetime(2026, 1, 1, tzinfo=UTC), max_results=5)


@pytest.mark.asyncio
async def test_fetch_tweet_and_search_not_implemented():
    src = FakeTwitterSource(timelines={})
    with pytest.raises(NotImplementedError):
        await src.fetch_tweet("x")
    with pytest.raises(NotImplementedError):
        await src.search("q", since=datetime(2026, 1, 1, tzinfo=UTC), max_results=5)
