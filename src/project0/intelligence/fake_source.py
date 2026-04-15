"""In-memory TwitterSource for tests. Seeded from a dict of handle → list
of Tweet. Filters by ``since`` and truncates to ``max_results``. Unknown
handles raise TwitterSourceError so tests that exercise the per-handle
failure path in generate_daily_report actually hit the error branch."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from project0.intelligence.source import Tweet, TwitterSourceError


@dataclass
class FakeTwitterSource:
    timelines: dict[str, list[Tweet]] = field(default_factory=dict)

    async def fetch_user_timeline(
        self,
        handle: str,
        *,
        since: datetime,
        max_results: int,
    ) -> list[Tweet]:
        handle = handle.lstrip("@").lower()
        if handle not in self.timelines:
            raise TwitterSourceError(f"unknown handle: {handle}")
        filtered = [t for t in self.timelines[handle] if t.posted_at >= since]
        filtered.sort(key=lambda t: t.posted_at, reverse=True)
        return filtered[:max_results]

    async def fetch_tweet(self, url_or_id: str) -> Tweet:
        raise NotImplementedError("FakeTwitterSource.fetch_tweet not used in 6d")

    async def search(
        self,
        query: str,
        *,
        since: datetime,
        max_results: int,
    ) -> list[Tweet]:
        raise NotImplementedError("FakeTwitterSource.search not used in 6d")
