"""One test per live LLM call site, confirming (agent, purpose) labels
end up in llm_usage. Prevents silent drift when future refactors move
code around."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.agents.secretary import (
    Secretary,
    SecretaryConfig,
    SecretaryPersona,
)
from project0.envelope import Envelope
from project0.intelligence.generate import generate_daily_report
from project0.intelligence.source import Tweet, TwitterSource
from project0.intelligence.watchlist import WatchEntry
from project0.llm.provider import FakeProvider
from project0.store import (
    AgentMemory,
    LLMUsageStore,
    MessagesStore,
    Store,
)


def _mk_store() -> tuple[Store, LLMUsageStore]:
    store = Store(":memory:")
    store.init_schema()
    return store, LLMUsageStore(store.conn)


def _mk_secretary(llm: FakeProvider, store: Store) -> Secretary:
    persona = SecretaryPersona(
        core="core persona",
        listener_mode="listener mode",
        group_addressed_mode="addressed mode",
        dm_mode="dm mode",
        reminder_mode="reminder mode",
    )
    cfg = SecretaryConfig(
        t_min_seconds=0,
        n_min_messages=0,
        l_min_weighted_chars=0,
        transcript_window=20,
        model="claude-sonnet-4-6",
        max_tokens_reply=800,
        max_tokens_listener=600,
        skip_sentinels=["[skip]"],
    )
    return Secretary(
        llm=llm,
        memory=AgentMemory(store.conn, "secretary"),
        messages_store=MessagesStore(store.conn),
        persona=persona,
        config=cfg,
        # (UserProfile / UserFactsReader / UserFactsWriter wired in task 11)
    )


def _raw_purposes(store: Store) -> list[str]:
    return [
        r["purpose"]
        for r in store.conn.execute("SELECT purpose FROM llm_usage").fetchall()
    ]


@pytest.mark.asyncio
async def test_secretary_addressed_labels() -> None:
    store, usage = _mk_store()
    fake = FakeProvider(responses=["hi reply"], usage_store=usage)
    sec = _mk_secretary(fake, store)
    env = Envelope(
        id=101,
        ts="2026-04-16T10:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=-100,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="user",
        from_agent=None,
        to_agent="secretary",
        body="@secretary hi",
        mentions=["secretary"],
        routing_reason="mention",
    )
    await sec.handle(env)
    rows = usage.summary_since("1970-01-01T00:00:00Z")
    assert rows[0]["agent"] == "secretary"
    assert _raw_purposes(store) == ["reply"]


@pytest.mark.asyncio
async def test_secretary_listener_labels() -> None:
    store, usage = _mk_store()
    fake = FakeProvider(responses=["something to say"], usage_store=usage)
    sec = _mk_secretary(fake, store)
    env = Envelope(
        id=202,
        ts="2026-04-16T10:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=-200,
        telegram_msg_id=2,
        received_by_bot="secretary",
        from_kind="user",
        from_agent=None,
        to_agent="secretary",
        body="plain group chatter",
        mentions=[],
        routing_reason="listener_observation",
    )
    result = await sec.handle(env)
    assert result is not None
    rows = usage.summary_since("1970-01-01T00:00:00Z")
    assert rows[0]["agent"] == "secretary"
    assert _raw_purposes(store) == ["listener"]


@pytest.mark.asyncio
async def test_secretary_reminder_labels() -> None:
    store, usage = _mk_store()
    fake = FakeProvider(responses=["reminding you"], usage_store=usage)
    sec = _mk_secretary(fake, store)
    env = Envelope(
        id=303,
        ts="2026-04-16T10:00:00Z",
        parent_id=None,
        source="internal",
        telegram_chat_id=None,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="secretary",
        body="please remind user",
        mentions=[],
        routing_reason="manager_delegation",
        payload={
            "kind": "reminder_request",
            "appointment": "dentist",
            "when": "tomorrow 3pm",
            "note": "bring insurance card",
        },
    )
    result = await sec.handle(env)
    assert result is not None
    rows = usage.summary_since("1970-01-01T00:00:00Z")
    assert rows[0]["agent"] == "secretary"
    assert _raw_purposes(store) == ["reminder"]


class _FakeTwitterSource:
    """Minimal TwitterSource stub for the summarizer test."""

    async def fetch_user_timeline(
        self, handle: str, *, since: datetime, max_results: int
    ) -> list[Tweet]:
        return [
            Tweet(
                handle=handle,
                tweet_id="1",
                url=f"https://x.com/{handle}/status/1",
                text="hello world",
                posted_at=datetime.now(UTC),
                reply_count=0,
                like_count=0,
                retweet_count=0,
            )
        ]

    async def fetch_tweet(self, url_or_id: str) -> Tweet:  # pragma: no cover
        raise NotImplementedError

    async def search(
        self, query: str, *, since: datetime, max_results: int
    ) -> list[Tweet]:  # pragma: no cover
        raise NotImplementedError


_VALID_REPORT_JSON = """
{
  "date": "",
  "generated_at": "",
  "user_tz": "",
  "watchlist_snapshot": [],
  "news_items": [],
  "suggested_accounts": [],
  "stats": {}
}
"""


@pytest.mark.asyncio
async def test_intelligence_summarizer_labels(tmp_path: Path) -> None:
    store, usage = _mk_store()
    fake = FakeProvider(responses=[_VALID_REPORT_JSON], usage_store=usage)
    source: TwitterSource = _FakeTwitterSource()  # type: ignore[assignment]
    watchlist = [WatchEntry(handle="example", tags=(), notes="")]
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()

    await generate_daily_report(
        target_date=datetime(2026, 4, 16).date(),
        source=source,
        llm=fake,
        summarizer_max_tokens=1000,
        watchlist=watchlist,
        reports_dir=reports_dir,
        user_tz=ZoneInfo("UTC"),
        timeline_since_hours=24,
        max_tweets_per_handle=5,
    )
    rows = usage.summary_since("1970-01-01T00:00:00Z")
    assert rows[0]["agent"] == "intelligence_summarizer"
    assert _raw_purposes(store) == ["report_gen"]
