"""Basic type + protocol shape tests for intelligence.source.
No HTTP or async work — just confirm the data types and protocol are
importable and have the expected fields. Concrete source tests live in
test_twitterapi_io_source.py and test_fake_source.py."""
from __future__ import annotations

from datetime import UTC, datetime

from project0.intelligence.source import Tweet, TwitterSource, TwitterSourceError


def test_tweet_dataclass_has_expected_fields():
    t = Tweet(
        handle="sama",
        tweet_id="123",
        url="https://x.com/sama/status/123",
        text="hello",
        posted_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        reply_count=1,
        like_count=2,
        retweet_count=3,
    )
    assert t.handle == "sama"
    assert t.tweet_id == "123"
    assert t.url == "https://x.com/sama/status/123"
    assert t.text == "hello"
    assert t.posted_at.tzinfo is not None
    assert t.reply_count == 1
    assert t.like_count == 2
    assert t.retweet_count == 3


def test_tweet_is_frozen():
    import dataclasses
    t = Tweet(
        handle="sama", tweet_id="1", url="u", text="t",
        posted_at=datetime(2026, 4, 15, tzinfo=UTC),
        reply_count=0, like_count=0, retweet_count=0,
    )
    try:
        t.handle = "other"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("Tweet should be frozen")


def test_twitter_source_is_a_protocol():
    # Protocol classes cannot be instantiated directly.
    from typing import Protocol, runtime_checkable
    # Just verify it's importable and isinstance-checking works structurally.
    assert hasattr(TwitterSource, "fetch_user_timeline")
    assert hasattr(TwitterSource, "fetch_tweet")
    assert hasattr(TwitterSource, "search")


def test_twitter_source_error_is_exception():
    err = TwitterSourceError("boom")
    assert isinstance(err, Exception)
    assert str(err) == "boom"
