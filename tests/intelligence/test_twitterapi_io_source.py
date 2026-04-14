"""TwitterApiIoSource tests. All HTTP is mocked via a fake httpx transport.
The response fixture mirrors the shape the real twitterapi.io endpoint
returns; if the real API changes, update ``_FIXTURE`` and the parsing code
in twitterapi_io.py together."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from project0.intelligence.source import TwitterSourceError
from project0.intelligence.twitterapi_io import TwitterApiIoSource

_FIXTURE = {
    "tweets": [
        {
            "id": "123456789",
            "url": "https://x.com/sama/status/123456789",
            "text": "gm, shipping today",
            "createdAt": "2026-04-15T03:17:00.000Z",
            "author": {"userName": "sama"},
            "replyCount": 12,
            "likeCount": 345,
            "retweetCount": 67,
        },
        {
            "id": "123456788",
            "url": "https://x.com/sama/status/123456788",
            "text": "older tweet",
            "createdAt": "2026-04-14T22:00:00.000Z",
            "author": {"userName": "sama"},
            "replyCount": 1,
            "likeCount": 20,
            "retweetCount": 2,
        },
    ]
}


def _mock_transport(handler):
    return httpx.MockTransport(handler)


_REAL_ENVELOPE_FIXTURE = {
    # Mirrors the actual shape twitterapi.io returns (verified with a live
    # call): wrapped in {status, code, msg, data: {...}} with Twitter's
    # legacy date format in createdAt.
    "status": "success",
    "code": 0,
    "msg": "success",
    "data": {
        "pin_tweet": None,
        "tweets": [
            {
                "id": "2042738954550603884",
                "url": "https://x.com/sama/status/2042738954550603884",
                "text": "wrapped envelope, legacy date",
                "createdAt": "Fri Apr 10 22:58:13 +0000 2026",
                "author": {"userName": "sama"},
                "replyCount": 2791,
                "likeCount": 15683,
                "retweetCount": 1227,
            }
        ],
    },
    "has_next_page": False,
    "next_cursor": None,
}


@pytest.mark.asyncio
async def test_fetch_user_timeline_real_envelope_and_legacy_date():
    """Regression guard: real twitterapi.io wraps tweets in data.tweets and
    uses Twitter's legacy "Fri Apr 10 22:58:13 +0000 2026" format. Both
    the envelope unwrapping and the legacy date parser live in
    twitterapi_io.py and must stay in sync with the live API."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_REAL_ENVELOPE_FIXTURE)

    src = TwitterApiIoSource(api_key="sk-test", transport=_mock_transport(handler))
    tweets = await src.fetch_user_timeline(
        "sama",
        since=datetime(2026, 4, 1, tzinfo=UTC),
        max_results=50,
    )
    await src.aclose()

    assert len(tweets) == 1
    t = tweets[0]
    assert t.tweet_id == "2042738954550603884"
    assert t.text == "wrapped envelope, legacy date"
    assert t.posted_at.tzinfo is not None
    assert t.posted_at.year == 2026 and t.posted_at.month == 4 and t.posted_at.day == 10
    assert t.like_count == 15683


@pytest.mark.asyncio
async def test_fetch_user_timeline_happy_path():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json=_FIXTURE)

    src = TwitterApiIoSource(api_key="sk-test", transport=_mock_transport(handler))
    tweets = await src.fetch_user_timeline(
        "sama",
        since=datetime(2026, 4, 14, 0, 0, tzinfo=UTC),
        max_results=50,
    )
    await src.aclose()

    assert "sama" in seen["url"]
    assert seen["headers"].get("x-api-key") == "sk-test"
    assert len(tweets) == 2
    assert tweets[0].tweet_id == "123456789"
    assert tweets[0].handle == "sama"
    assert tweets[0].text == "gm, shipping today"
    assert tweets[0].posted_at.tzinfo is not None
    assert tweets[0].like_count == 345


@pytest.mark.asyncio
async def test_fetch_user_timeline_filters_by_since_client_side():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_FIXTURE)

    src = TwitterApiIoSource(api_key="sk-test", transport=_mock_transport(handler))
    tweets = await src.fetch_user_timeline(
        "sama",
        since=datetime(2026, 4, 15, 0, 0, tzinfo=UTC),
        max_results=50,
    )
    await src.aclose()
    # Only the tweet from 03:17 on the 15th; the 14th 22:00 one is filtered out.
    assert [t.tweet_id for t in tweets] == ["123456789"]


@pytest.mark.asyncio
async def test_http_error_raises_twitter_source_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server blew up")

    src = TwitterApiIoSource(api_key="sk-test", transport=_mock_transport(handler))
    with pytest.raises(TwitterSourceError, match="HTTP 500"):
        await src.fetch_user_timeline(
            "sama", since=datetime(2026, 4, 14, tzinfo=UTC), max_results=10,
        )
    await src.aclose()


@pytest.mark.asyncio
async def test_malformed_json_raises_twitter_source_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    src = TwitterApiIoSource(api_key="sk-test", transport=_mock_transport(handler))
    with pytest.raises(TwitterSourceError, match="malformed response"):
        await src.fetch_user_timeline(
            "sama", since=datetime(2026, 4, 14, tzinfo=UTC), max_results=10,
        )
    await src.aclose()


@pytest.mark.asyncio
async def test_empty_timeline_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tweets": []})

    src = TwitterApiIoSource(api_key="sk-test", transport=_mock_transport(handler))
    tweets = await src.fetch_user_timeline(
        "sama", since=datetime(2026, 4, 14, tzinfo=UTC), max_results=10,
    )
    await src.aclose()
    assert tweets == []
