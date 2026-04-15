"""Protocol + data types for pulling tweets from an external source.

6d has one concrete implementation (``TwitterApiIoSource`` hitting
twitterapi.io) plus a ``FakeTwitterSource`` for tests. ``fetch_tweet`` and
``search`` are declared on the protocol but not used in 6d — they are
placeholders so 6f (on-demand tweet lookup) and 6g (search-based pulses)
don't need to reshape the protocol."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Tweet:
    handle: str
    tweet_id: str
    url: str
    text: str
    posted_at: datetime       # timezone-aware, UTC preferred
    reply_count: int
    like_count: int
    retweet_count: int


class TwitterSourceError(Exception):
    """Raised when a twitter source cannot fulfill a request.

    Concrete sources catch underlying HTTP/network/parse errors and
    re-raise as TwitterSourceError with a short human-readable message.
    Callers (e.g. generate_daily_report) catch this at the per-handle
    boundary and record the failure, letting other handles continue."""


class TwitterSource(Protocol):
    async def fetch_user_timeline(
        self,
        handle: str,
        *,
        since: datetime,
        max_results: int,
    ) -> list[Tweet]:
        ...

    async def fetch_tweet(self, url_or_id: str) -> Tweet:
        ...

    async def search(
        self,
        query: str,
        *,
        since: datetime,
        max_results: int,
    ) -> list[Tweet]:
        ...
