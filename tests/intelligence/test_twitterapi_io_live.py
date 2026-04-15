"""Live smoke test for TwitterApiIoSource. Gated on TWITTERAPI_IO_API_KEY;
skipped entirely when the env var is missing. Matches the 6b
test_google_calendar_live.py pattern. Do NOT run in CI by default."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from project0.intelligence.twitterapi_io import TwitterApiIoSource

API_KEY = os.environ.get("TWITTERAPI_IO_API_KEY")

pytestmark = pytest.mark.skipif(
    not API_KEY, reason="TWITTERAPI_IO_API_KEY not set — skipping live smoke test"
)


@pytest.mark.asyncio
async def test_live_fetch_user_timeline_returns_tweets():
    src = TwitterApiIoSource(api_key=API_KEY or "")
    try:
        tweets = await src.fetch_user_timeline(
            "sama",
            since=datetime.now(UTC) - timedelta(days=7),
            max_results=5,
        )
    finally:
        await src.aclose()
    assert len(tweets) > 0, "expected at least one tweet in the last 7 days"
    t = tweets[0]
    assert t.tweet_id
    assert t.url.startswith("https://")
    assert t.posted_at.tzinfo is not None
