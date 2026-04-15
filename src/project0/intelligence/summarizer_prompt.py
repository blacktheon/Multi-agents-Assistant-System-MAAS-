"""Prompt strings + builders for Intelligence.

Three user-prompt builders share this file because they all share the
same shape and helpers:
  - ``build_user_prompt``: feeds raw tweets into the Opus summarizer
  - ``build_qa_user_prompt``: feeds the latest report into the Sonnet
    Q&A loop
  - ``build_delegated_user_prompt``: feeds a Manager-delegated query
    into the Sonnet Q&A loop

The system prompt is stable and is cached on the Anthropic system block
(see llm/provider.py). The user prompts change every call."""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Any

from project0.envelope import Envelope
from project0.intelligence.source import Tweet


SUMMARIZER_SYSTEM_PROMPT = """You are the Intelligence agent's daily-report summarizer for a Project 0
multi-agent personal assistant. You take a batch of raw tweets from a
watchlist and produce a structured daily report in JSON.

## Your job
1. Cluster tweets by topic. Multiple accounts covering the same story
   become ONE news_item.
2. Rank items by importance to a technically sophisticated user who cares
   about AI, ML, infrastructure, and tech industry news.
3. Write concise simplified-Chinese summaries (2–4 sentences each).
4. Flag accounts referenced in the tweets that are not already on the
   watchlist but look worth following (suggested_accounts).
5. Fill out the stats block using the numbers in the user message.

## Importance rubric
- high:   major model releases, significant industry moves, named hardware
          launches, safety/regulatory events affecting the field, or
          anything the user would regret missing.
- medium: notable technical posts, thoughtful analyses, mid-tier company
          news.
- low:    routine updates, personal takes, minor announcements. Include
          only if the tweet volume on the topic justifies it.

## Source trust heuristics
- First-party announcements (company official accounts) > researchers >
  commentary.
- If a claim appears in only one tweet from an unverified account, mark
  the news_item's summary with "(未经证实)".
- Prefer citing the original source tweet over reposts.

## Hard output rules
- Output ONLY a single JSON object matching the schema below. No prose,
  no markdown code fences, no preamble.
- Every news_item must cite at least one source tweet by URL.
- Chinese summaries only. Keep tweet ``text`` fields in their original
  language.
- If no tweets warrant a news_item, return an empty ``news_items`` array.
  Do NOT invent content.
- ``suggested_accounts`` may be empty. Quality over quantity.

## Schema
{
  "date": "YYYY-MM-DD",                    // fill with the date the user message gives you
  "generated_at": "",                      // leave empty — Python fills this in after your response
  "user_tz": "",                           // leave empty — Python fills this in
  "watchlist_snapshot": [],                // leave empty — Python fills this in
  "news_items": [
    {
      "id": "n1",                          // unique within this report: n1, n2, n3...
      "headline": "",                      // short Chinese headline
      "summary": "",                       // 2-4 Chinese sentences
      "importance": "high" | "medium" | "low",
      "importance_reason": "",             // why this matters, Chinese
      "topics": ["lowercase-hyphenated"],
      "source_tweets": [
        {
          "handle": "",                    // no @
          "url": "",                       // full URL
          "text": "",                      // original language
          "posted_at": ""                  // ISO8601
        }
      ]
    }
  ],
  "suggested_accounts": [
    {
      "handle": "",                        // no @
      "reason": "",                        // Chinese
      "seen_in_items": ["n1"]              // must reference existing news_items ids
    }
  ],
  "stats": {
    "tweets_fetched": 0,                   // leave 0 — Python fills these
    "handles_attempted": 0,
    "handles_succeeded": 0,
    "items_generated": 0,
    "errors": []
  }
}

## Example
Input tweets:
  @sama: "gm, o5-mini is live, 40% lower latency"
  @openai: "Introducing o5-mini: faster reasoning for your apps"
  @some_researcher: "o5-mini's routing trick is neat, explained here..."

Output:
{
  "date": "2026-04-15",
  "generated_at": "",
  "user_tz": "",
  "watchlist_snapshot": [],
  "news_items": [
    {
      "id": "n1",
      "headline": "OpenAI 发布 o5-mini，推理延迟降低 40%",
      "summary": "OpenAI 宣布 o5-mini 正式上线，比前代模型推理延迟降低约 40%。Sam Altman 同步确认发布。",
      "importance": "high",
      "importance_reason": "主流模型迭代，直接影响用户在用的 API",
      "topics": ["ai-models", "openai", "inference"],
      "source_tweets": [
        {"handle": "openai", "url": "https://x.com/openai/status/1", "text": "Introducing o5-mini: faster reasoning for your apps", "posted_at": "2026-04-15T03:00:00Z"},
        {"handle": "sama",   "url": "https://x.com/sama/status/2",   "text": "gm, o5-mini is live, 40% lower latency",                "posted_at": "2026-04-15T03:05:00Z"}
      ]
    }
  ],
  "suggested_accounts": [
    {"handle": "some_researcher", "reason": "在 n1 中对 o5-mini 的路由机制做了解读", "seen_in_items": ["n1"]}
  ],
  "stats": {"tweets_fetched": 0, "handles_attempted": 0, "handles_succeeded": 0, "items_generated": 0, "errors": []}
}
"""


def build_user_prompt(
    *,
    raw_tweets: Sequence[Tweet],
    watchlist_snapshot: Sequence[str],
    errors: Sequence[dict[str, Any]],
    today_local: date,
    user_tz_name: str,
) -> str:
    """Render the user-message payload for the summarizer call. Tweets are
    grouped by handle, newest first. Handles with no tweets are omitted."""
    by_handle: dict[str, list[Tweet]] = defaultdict(list)
    for t in raw_tweets:
        by_handle[t.handle.lstrip("@").lower()].append(t)
    for handle in by_handle:
        by_handle[handle].sort(key=lambda x: x.posted_at, reverse=True)

    lines: list[str] = []
    lines.append(
        f"Today is {today_local.isoformat()} ({user_tz_name}). "
        f"Generate the daily report for this date."
    )
    lines.append("")
    lines.append(
        f"Watchlist snapshot ({len(watchlist_snapshot)} handles): "
        + ", ".join(watchlist_snapshot)
    )
    lines.append(f"Handles attempted: {len(watchlist_snapshot)}")
    lines.append(
        f"Handles succeeded: {len(watchlist_snapshot) - len(errors)}"
    )
    if errors:
        lines.append(
            "Handles failed: "
            + json.dumps(list(errors), ensure_ascii=False)
        )
    lines.append(f"Tweets fetched: {len(raw_tweets)}")
    lines.append("")
    lines.append("Raw tweets follow, grouped by handle, newest first:")
    lines.append("")

    # Stable handle ordering: by watchlist_snapshot order when possible,
    # falling back to alphabetical for any handle not in the snapshot.
    ordered_handles = [h for h in watchlist_snapshot if h in by_handle]
    extras = sorted(h for h in by_handle if h not in set(watchlist_snapshot))
    for h in ordered_handles + extras:
        tweets = by_handle[h]
        lines.append(f"=== @{h} ===")
        for t in tweets:
            lines.append(f"[{t.posted_at.isoformat()}] url={t.url}")
            lines.append(t.text)
            lines.append("")
    return "\n".join(lines)


def build_qa_user_prompt(
    *,
    latest_report: dict[str, Any] | None,
    current_date_local: date,
    recent_messages: Sequence[Envelope],
    current_user_message: str,
) -> str:
    """Build the initial user message for an Intelligence chat turn.

    The full daily report is NOT inlined here — the cached system prompt
    carries a slim headline-only index, and the model fetches individual
    items on demand via the ``get_report_item`` tool. This keeps per-call
    input tokens bounded and the system-prompt cache warm across turns."""
    lines: list[str] = []
    lines.append(f"Today is {current_date_local.isoformat()}.")
    lines.append("")
    if latest_report is None:
        lines.append(
            "当前没有任何日报文件。如果用户在问新闻内容，请告诉他先让你生成一份"
            "（调用 generate_daily_report 工具）。(no report available)"
        )
    else:
        report_date = latest_report.get("date", "unknown")
        lines.append(f"最新日报日期：{report_date}")
        if report_date != current_date_local.isoformat():
            lines.append(
                f"注意：最新日报是 {report_date} 的，不是今天 "
                f"({current_date_local.isoformat()})。回答用户前请明确提到日期，"
                f"避免把旧闻当成今天的事。"
            )
        lines.append(
            "系统提示里已附上日报索引（headline 列表）。"
            "要深入某条请调用 get_report_item 工具。"
        )

    if recent_messages:
        lines.append("")
        lines.append("最近对话记录：")
        for e in recent_messages:
            who = e.from_agent or e.from_kind
            lines.append(f"  {who}: {e.body}")

    lines.append("")
    lines.append(f"用户刚发的消息：{current_user_message}")
    return "\n".join(lines)


def build_delegated_user_prompt(
    *,
    latest_report: dict[str, Any] | None,
    current_date_local: date,
    query: str,
) -> str:
    """Build the initial user message for a Manager-delegated turn. No
    transcript — the query is assumed self-contained."""
    lines: list[str] = []
    lines.append(f"Today is {current_date_local.isoformat()}.")
    lines.append("")
    lines.append("经理把一个查询转给了你。请基于最新日报作答，"
                 "如果日报覆盖不到或过期，直接说清楚，不要编造。")
    lines.append("")
    if latest_report is None:
        lines.append("当前没有任何日报文件。(no report available)")
    else:
        report_date = latest_report.get("date", "unknown")
        lines.append(f"最新日报日期：{report_date}")
        if report_date != current_date_local.isoformat():
            lines.append(
                f"（最新日报是 {report_date}，今天是 "
                f"{current_date_local.isoformat()}，请在回答中体现日期）"
            )
        lines.append(
            "系统提示里已附上日报索引（headline 列表）。"
            "要深入某条请调用 get_report_item 工具。"
        )
    lines.append("")
    lines.append(f"查询：{query}")
    return "\n".join(lines)
