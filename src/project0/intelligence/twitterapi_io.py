"""Concrete TwitterSource talking to twitterapi.io.

One httpx.AsyncClient owned per instance. Auth via ``x-api-key`` header.
No retries: twitterapi.io is reliable at watchlist-sized daily load, and
generate_daily_report's partial-failure handling already deals with
per-handle failures.

If twitterapi.io changes its response shape, the only code that needs
updating is ``_parse_tweet`` — everything else (URL, auth, error handling)
is stable."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import httpx

from project0.intelligence.source import Tweet, TwitterSourceError

log = logging.getLogger(__name__)

_BASE_URL = "https://api.twitterapi.io"

# Twitter's legacy "Fri Apr 10 22:58:13 +0000 2026" format that twitterapi.io
# mirrors in the ``createdAt`` field. ISO 8601 is also tolerated as a fallback
# in case the upstream ever normalizes (or for mocked tests that use ISO).
_LEGACY_TWITTER_FMT = "%a %b %d %H:%M:%S %z %Y"


def _parse_created_at(raw: str) -> datetime:
    raw = raw.strip()
    try:
        return datetime.strptime(raw, _LEGACY_TWITTER_FMT)
    except ValueError:
        pass
    # ISO 8601 fallback (with trailing 'Z' for UTC).
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


class TwitterApiIoSource:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers={"x-api-key": api_key, "accept": "application/json"},
            timeout=timeout_seconds,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_user_timeline(
        self,
        handle: str,
        *,
        since: datetime,
        max_results: int,
    ) -> list[Tweet]:
        handle = handle.lstrip("@")
        try:
            resp = await self._client.get(
                "/twitter/user/last_tweets",
                params={"userName": handle, "count": max_results},
            )
        except httpx.TimeoutException as e:
            raise TwitterSourceError(f"timeout fetching {handle}: {e}") from e
        except httpx.HTTPError as e:
            raise TwitterSourceError(f"http error fetching {handle}: {e}") from e

        if resp.status_code >= 400:
            body = resp.text[:200] if resp.text else ""
            raise TwitterSourceError(f"HTTP {resp.status_code} fetching {handle}: {body}")

        try:
            data = resp.json()
        except (ValueError, json.JSONDecodeError) as e:
            raise TwitterSourceError(f"malformed response for {handle}: {e}") from e

        # twitterapi.io wraps everything in {status, code, msg, data: {tweets: [...]}}.
        # Fall back to a flat ``tweets`` key so the mocked unit tests (which use
        # a flat fixture for simplicity) keep working.
        envelope = data.get("data")
        if isinstance(envelope, dict):
            raw_tweets = envelope.get("tweets") or []
        else:
            raw_tweets = data.get("tweets") or []
        if not isinstance(raw_tweets, list):
            raise TwitterSourceError(
                f"malformed response for {handle}: 'tweets' is not a list"
            )

        out: list[Tweet] = []
        for raw in raw_tweets:
            try:
                t = self._parse_tweet(raw, fallback_handle=handle)
            except (KeyError, ValueError, TypeError) as e:
                log.warning("twitterapi_io: skipping malformed tweet: %s", e)
                continue
            if t.posted_at >= since:
                out.append(t)
        # Newest first.
        out.sort(key=lambda t: t.posted_at, reverse=True)
        return out[:max_results]

    async def fetch_tweet(self, url_or_id: str) -> Tweet:
        raise NotImplementedError("fetch_tweet not used in 6d")

    async def search(
        self,
        query: str,
        *,
        since: datetime,
        max_results: int,
    ) -> list[Tweet]:
        raise NotImplementedError("search not used in 6d")

    @staticmethod
    def _parse_tweet(raw: dict[str, Any], *, fallback_handle: str) -> Tweet:
        tid = str(raw["id"])
        url = str(raw.get("url") or f"https://x.com/{fallback_handle}/status/{tid}")
        text = str(raw.get("text") or "")
        created_at = str(raw["createdAt"])
        posted_at = _parse_created_at(created_at)
        author = raw.get("author") or {}
        handle = str(author.get("userName") or fallback_handle).lstrip("@")
        return Tweet(
            handle=handle,
            tweet_id=tid,
            url=url,
            text=text,
            posted_at=posted_at,
            reply_count=int(raw.get("replyCount") or 0),
            like_count=int(raw.get("likeCount") or 0),
            retweet_count=int(raw.get("retweetCount") or 0),
        )
