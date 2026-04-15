"""Deterministic daily-report generation pipeline.

Shape: fetch every watchlist handle (catching per-handle errors), feed
everything into ONE LLM call via ``llm.complete``, parse + validate the
returned JSON, atomically write the file. This is ordinary Python — not
an agentic loop. Cost is one Opus call per report (~$1) vs 20-50x more
for a per-handle agentic shape."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from project0.intelligence.report import (
    atomic_write_json,
    parse_json_strict,
    validate_report_dict,
)
from project0.intelligence.source import Tweet, TwitterSource, TwitterSourceError
from project0.intelligence.summarizer_prompt import (
    SUMMARIZER_SYSTEM_PROMPT,
    build_user_prompt,
)
from project0.intelligence.watchlist import WatchEntry
from project0.llm.provider import LLMProvider, Msg

log = logging.getLogger(__name__)


async def generate_daily_report(
    *,
    target_date: date,
    source: TwitterSource,
    llm: LLMProvider,
    summarizer_max_tokens: int,
    watchlist: Sequence[WatchEntry],
    reports_dir: Path,
    user_tz: ZoneInfo,
    timeline_since_hours: int,
    max_tweets_per_handle: int,
    summarizer_thinking_budget: int | None = None,
) -> dict[str, Any]:
    """Fetch tweets from the watchlist, summarize via one LLM call,
    validate, write. Returns the written report dict.

    The ``llm`` provider is expected to be pre-bound to the summarizer
    model (Opus) at construction time — this function does not accept a
    model override because ``LLMProvider.complete`` does not either.
    ``main.py`` constructs a dedicated ``AnthropicProvider`` for this.

    Raises:
        TwitterSourceError: if ALL watchlist handles fail to fetch.
        ValueError: if the LLM returns malformed or schema-invalid JSON.
    """
    since = datetime.now(tz=user_tz) - timedelta(hours=timeline_since_hours)

    raw_tweets: list[Tweet] = []
    errors: list[dict[str, Any]] = []

    for entry in watchlist:
        try:
            tweets = await source.fetch_user_timeline(
                entry.handle,
                since=since,
                max_results=max_tweets_per_handle,
            )
            raw_tweets.extend(tweets)
        except TwitterSourceError as e:
            errors.append({"handle": entry.handle, "error": str(e)})
            log.warning("generate_daily_report: %s failed: %s", entry.handle, e)

    if not raw_tweets:
        handles_preview = ", ".join(e["handle"] for e in errors[:5])
        if len(errors) > 5:
            handles_preview += "..."
        raise TwitterSourceError(
            f"all {len(watchlist)} fetches failed: {handles_preview}"
        )

    watchlist_snapshot = [e.handle for e in watchlist]
    user_prompt = build_user_prompt(
        raw_tweets=raw_tweets,
        watchlist_snapshot=watchlist_snapshot,
        errors=errors,
        today_local=target_date,
        user_tz_name=user_tz.key,
    )

    result_text = await llm.complete(
        system=SUMMARIZER_SYSTEM_PROMPT,
        messages=[Msg(role="user", content=user_prompt)],
        max_tokens=summarizer_max_tokens,
        thinking_budget_tokens=summarizer_thinking_budget,
        agent="intelligence_summarizer",
        purpose="report_gen",
        envelope_id=None,
    )

    report_dict = parse_json_strict(result_text)

    # Overwrite fields Python controls (LLM is told to leave these blank).
    report_dict["date"] = target_date.isoformat()
    report_dict["generated_at"] = datetime.now(tz=user_tz).isoformat()
    report_dict["user_tz"] = user_tz.key
    report_dict["watchlist_snapshot"] = watchlist_snapshot
    stats = report_dict.setdefault("stats", {})
    stats["tweets_fetched"] = len(raw_tweets)
    stats["handles_attempted"] = len(watchlist)
    stats["handles_succeeded"] = len(watchlist) - len(errors)
    stats["errors"] = errors
    stats["items_generated"] = len(report_dict.get("news_items", []))

    validate_report_dict(report_dict)

    out_path = reports_dir / f"{target_date.isoformat()}.json"
    atomic_write_json(out_path, report_dict)
    log.info("wrote daily report to %s", out_path)
    return report_dict
