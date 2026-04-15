"""Manual smoke test for 6d Intelligence generation pipeline.

Usage (three options):

    # 1) Put keys in a .env file at the worktree root (recommended):
    #    ANTHROPIC_API_KEY=sk-...
    #    TWITTERAPI_IO_API_KEY=new1_...
    uv run python scripts/smoke_generate_report.py

    # 2) Export once in your current shell:
    export ANTHROPIC_API_KEY=sk-...
    export TWITTERAPI_IO_API_KEY=new1_...
    uv run python scripts/smoke_generate_report.py

    # 3) Inline (keys and command on ONE line, no newline between them):
    ANTHROPIC_API_KEY=sk-... TWITTERAPI_IO_API_KEY=new1_... uv run python scripts/smoke_generate_report.py

Drives generate_daily_report against the real twitterapi.io + real
Anthropic API. Writes to data/intelligence/reports/YYYY-MM-DD.json.
Does NOT touch Telegram or load_settings() — this is a pure pipeline
smoke, not a full startup. Change USER_TZ below if you're not in
Asia/Shanghai.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from project0.agents.intelligence import load_intelligence_config
from project0.intelligence.generate import generate_daily_report
from project0.intelligence.twitterapi_io import TwitterApiIoSource
from project0.intelligence.watchlist import load_watchlist
from project0.llm.provider import AnthropicProvider

USER_TZ = ZoneInfo("Asia/Shanghai")


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        sys.exit(
            f"error: {name} is not set.\n"
            f"  put it in .env at the worktree root, or export it in your shell, "
            f"or pass it inline on the SAME line as the command:\n"
            f"  {name}=... uv run python scripts/smoke_generate_report.py"
        )
    return val


async def main() -> None:
    # Load .env if present; does not override vars already set in the environment.
    load_dotenv(override=False)

    anthropic_key = _require_env("ANTHROPIC_API_KEY")
    twitter_key = _require_env("TWITTERAPI_IO_API_KEY")

    cfg = load_intelligence_config(Path("prompts/intelligence.toml"))
    watchlist = load_watchlist(Path("prompts/intelligence.toml"))

    twitter = TwitterApiIoSource(api_key=twitter_key)
    llm = AnthropicProvider(api_key=anthropic_key, model=cfg.summarizer_model)

    reports_dir = Path("data/intelligence/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    try:
        report = await generate_daily_report(
            target_date=datetime.now(USER_TZ).date(),
            source=twitter,
            llm=llm,
            summarizer_max_tokens=cfg.summarizer_max_tokens,
            watchlist=watchlist,
            reports_dir=reports_dir,
            user_tz=USER_TZ,
            timeline_since_hours=cfg.timeline_since_hours,
            max_tweets_per_handle=cfg.max_tweets_per_handle,
        )
        items = len(report["news_items"])
        fetched = report["stats"]["tweets_fetched"]
        failures = len(report["stats"]["errors"])
        print(f"OK: {items} items, {fetched} tweets, {failures} handle failures")
        if failures:
            print("Failed handles:")
            for e in report["stats"]["errors"]:
                print(f"  - {e['handle']}: {e['error']}")
    finally:
        await twitter.aclose()


if __name__ == "__main__":
    asyncio.run(main())
