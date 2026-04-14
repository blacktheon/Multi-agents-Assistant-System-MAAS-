# Sub-project 6d — Intelligence agent (Twitter/X ingestion + daily report + shallow Q&A)

**Date:** 2026-04-15
**Status:** Design approved, ready for implementation plan
**Depends on:** 6a (multi-agent skeleton + Intelligence `AGENT_SPECS` entry), 6c (Manager + `complete_with_tools` + shared tool-loop pattern)

---

## 1. Context and scope

Sub-project 6d replaces `intelligence_stub` (`src/project0/agents/intelligence.py`, a one-line echo) with a real LLM-backed agent that:

- ingests tweets from a static watchlist via **twitterapi.io**
- generates a structured **DailyReport** JSON file through a deterministic, one-LLM-call pipeline using **Claude Opus 4.6**
- answers user questions about the *latest* report through a tool-use agentic loop using **Claude Sonnet 4.6**
- runs behind its own Telegram bot (`TELEGRAM_BOT_TOKEN_INTELLIGENCE`, already wired into `AGENT_SPECS` since 6a)
- becomes a functional target for Manager's existing `delegate_to_intelligence` tool (landed as a stub-backed dead end in 6c)

6d is the **foundation** sub-project for Intelligence. It ships on-demand generation and shallow single-report Q&A. Pulse-driven daily reports, ad-hoc watch pulses, email/webpage delivery, cross-report retrieval, 7-day topic memory, and feedback-based preference learning are all deliberately deferred.

### In scope

- `TwitterSource` protocol + `Tweet` dataclass
- `TwitterApiIoSource` concrete HTTP client (twitterapi.io)
- `FakeTwitterSource` for tests
- Static watchlist loader (`[[watch]]` TOML array in `prompts/intelligence.toml`)
- `DailyReport` schema + strict validator + atomic JSON file writer
- Deterministic `generate_daily_report` pipeline (fetch → one Opus summarization call → validate → write)
- `Intelligence` agent class replacing `intelligence_stub`
- Four Intelligence tools: `generate_daily_report`, `get_latest_report`, `get_report`, `list_reports`
- Q&A chat path with eager latest-report injection into the agent's initial user prompt
- `Intelligence` persona + config files (`prompts/intelligence.md`, `prompts/intelligence.toml`), Chinese, five canonical sections mirroring Manager's shape
- `register_intelligence` symmetric with `register_manager`
- Composition-root wiring in `src/project0/main.py`
- **Refactor**: extract `_agentic_loop` from `agents/manager.py` into a shared `agents/_tool_loop.py` now that there are two callers
- Full unit/integration test coverage. One optional live smoke test gated on `TWITTERAPI_IO_API_KEY`, matching the 6b live-calendar test pattern

### Out of scope (explicitly deferred)

- **6e**: report delivery surface (email, webpage, browsable history UI)
- **6f**: cross-report retrieval, 7-day topic memory, deep topic understanding, follow-up web search during Q&A, extended thinking on the summarizer call
- **6g**: pulse-driven automatic daily reports, user-defined ad-hoc watch pulses ("check Iran news every 10 min", "new model releases every 3 days")
- **6h**: feedback loop, dynamic follow/unfollow via chat, per-entry preference learning, account trust scoring
- Shared Blackboard writes, Manager summary reads of Intelligence output
- Retry logic for Twitter fetches
- Report regeneration idempotency beyond "overwrite if same date requested twice"
- Writing into Layer D (formal knowledge base) — Intelligence stays briefing-only per master-doc §4.1 rule 4
- Cost monitoring or budget caps
- End-to-end Telegram-bot tests (trusting the existing I/O layer's tests, matching the 6a/6c pattern)

---

## 2. Module layout

Intelligence gets its own infrastructure package, sibling to `calendar/` and `llm/`. The agent class itself stays in `agents/` following the existing convention.

```
src/project0/
    calendar/              # existing (6b)
    llm/                   # existing
    intelligence/          # NEW
        __init__.py
        source.py              # TwitterSource protocol + Tweet dataclass + TwitterSourceError
        twitterapi_io.py       # TwitterApiIoSource concrete impl
        fake_source.py         # FakeTwitterSource
        watchlist.py           # WatchEntry + load_watchlist
        report.py              # DailyReport dataclass + validate_report_dict + atomic_write_json + read_report + list_report_dates
        generate.py            # generate_daily_report deterministic pipeline
        summarizer_prompt.py   # SUMMARIZER_SYSTEM_PROMPT + build_user_prompt
    agents/
        secretary.py       # existing
        manager.py         # existing (6c), minor refactor to use shared tool-loop
        intelligence.py    # REWRITTEN: Intelligence class
        _tool_loop.py      # NEW, extracted from manager.py
        registry.py        # gains register_intelligence
    ...

prompts/
    intelligence.md        # NEW: Chinese persona, 5 sections
    intelligence.toml      # NEW: [llm.summarizer], [llm.qa], [context], [twitter], [[watch]]

data/
    intelligence/
        reports/           # runtime-created, gitignored
            2026-04-15.json
            2026-04-14.json
            ...
```

### Module responsibilities

- **`source.py`**: defines `TwitterSource` protocol with three methods (`fetch_user_timeline`, `fetch_tweet`, `search`), the `Tweet` frozen dataclass, and the `TwitterSourceError` exception. 6d uses only `fetch_user_timeline`; `fetch_tweet` and `search` are in the protocol so 6g/6f don't need to reshape it.
- **`twitterapi_io.py`**: concrete `httpx.AsyncClient`-based implementation. Auth token from `TWITTERAPI_IO_API_KEY`. Translates HTTP errors → `TwitterSourceError`. No retries. Owns its own client lifecycle.
- **`fake_source.py`**: in-memory implementation seeded from a dict of `handle → list[Tweet]`. Used by every non-live test.
- **`watchlist.py`**: parses `[[watch]]` array from `prompts/intelligence.toml`. `WatchEntry(handle, tags, notes)`. Loader raises `RuntimeError` with file path and field name on malformed entries. Mirrors 6c's `load_pulse_entries`.
- **`report.py`**: `DailyReport` dataclass, `validate_report_dict(d) -> None` raising `ValueError`, `atomic_write_json(path, data)` via tmp+rename, `read_report(path)`, `list_report_dates(reports_dir)` returning sorted-descending list of `date` objects derived from filenames.
- **`generate.py`**: the `generate_daily_report` function. Takes source, llm, watchlist, summarizer-model/max-tokens/user-tz, reports_dir as parameters, returns the written path. Pure function shape — not a method. Easy to test without instantiating the agent.
- **`summarizer_prompt.py`**: `SUMMARIZER_SYSTEM_PROMPT` (stable ~1500-token string) and `build_user_prompt(raw_tweets, watchlist_snapshot, errors, today_local) -> str`.
- **`agents/intelligence.py`**: thin `Intelligence` class. Dispatches by `routing_reason`, calls shared `_agentic_loop` with its own tool specs and `_dispatch_tool` method. Never delegates.
- **`agents/_tool_loop.py`**: the `_agentic_loop` helper and `TurnState` dataclass, extracted from `manager.py`. Parameterized on a `dispatch_tool` callable so each agent supplies its own tool dispatcher.

### Architectural principles

1. **Generation is ordinary Python, not an agentic loop.** One LLM call per report. Deterministic, testable, cheap to reason about. The agentic loop is reserved for chat-turn Q&A where flexibility matters.
2. **The agent class is thin.** Real work lives in `intelligence/generate.py` and `intelligence/report.py`. You could delete `agents/intelligence.py` and still have a working report generator callable from a script.
3. **`TwitterSource` is a `typing.Protocol`**, matching `LLMProvider`. Concrete classes don't inherit from it; `FakeTwitterSource` is a structural substitute in tests.
4. **No new storage layer.** Reports are flat JSON files under `data/intelligence/reports/`. Nothing touches SQLite, nothing touches `store.py`.
5. **Q&A context loading is deterministic, not agentic.** When Intelligence gets a chat turn, it eagerly reads the latest report (if any) and injects it into the initial user prompt. The model does not call a tool to see today's report. The read-report tools exist only for fetching *older* reports on demand.
6. **Schema validation is hand-written**, not Pydantic. A ~30-line `validate_report_dict` walking expected keys. Pydantic is not currently a project dependency and I'm not dragging it in for one file.

---

## 3. Twitter source

### 3.1 Protocol and data types (`intelligence/source.py`)

```python
from datetime import datetime
from typing import Protocol
from dataclasses import dataclass

@dataclass(frozen=True)
class Tweet:
    handle: str
    tweet_id: str
    url: str
    text: str
    posted_at: datetime            # timezone-aware UTC
    reply_count: int
    like_count: int
    retweet_count: int

class TwitterSourceError(Exception):
    """Raised when a Twitter source cannot fulfill a request."""

class TwitterSource(Protocol):
    async def fetch_user_timeline(
        self, handle: str, *, since: datetime, max_results: int
    ) -> list[Tweet]: ...

    async def fetch_tweet(self, url_or_id: str) -> Tweet: ...

    async def search(
        self, query: str, *, since: datetime, max_results: int
    ) -> list[Tweet]: ...
```

6d uses only `fetch_user_timeline`. The other methods are declared so future sub-projects (6g search-based pulses, 6f on-demand tweet lookups) don't need protocol changes.

### 3.2 `TwitterApiIoSource` (`intelligence/twitterapi_io.py`)

- Single `httpx.AsyncClient` owned by the instance, created in `__init__`, closed via an explicit `aclose()` method (composition-root calls it during shutdown).
- Auth: `Authorization: Bearer <TWITTERAPI_IO_API_KEY>` or whichever header twitterapi.io uses (verified at implementation time from their API docs).
- `fetch_user_timeline(handle, since, max_results)` issues one GET request, parses the JSON response into `Tweet` instances. Tweets older than `since` are filtered client-side if the API returns more than requested.
- HTTP 4xx/5xx → `TwitterSourceError(f"HTTP {status}: {body_first_200_chars}")`.
- Network timeout → `TwitterSourceError("timeout after N seconds")`.
- JSON parse failure → `TwitterSourceError("malformed response")`.
- No retries. No request throttling (watchlist-sized loads don't hit rate limits).

### 3.3 `FakeTwitterSource` (`intelligence/fake_source.py`)

- `__init__(timelines: dict[str, list[Tweet]])` — seeded with per-handle tweet lists.
- `fetch_user_timeline` returns the seeded list filtered by `since` and truncated to `max_results`.
- Unknown handle → `TwitterSourceError("unknown handle")` (matches real client behavior so tests hitting "missing handle" paths actually exercise the error code path).
- `fetch_tweet` and `search` raise `NotImplementedError` — not used in 6d tests.

### 3.4 Cost budget

- Watchlist size: ~20–50 handles seeded by hand. Assume 50 as the plausible ceiling.
- Tweets per handle per day: ~10 average, ~20 worst case.
- Daily volume: 500–1000 tweets/day.
- twitterapi.io: $0.15 per 1,000 tweets.
- **Twitter fetch cost: ≤$5/month.**
- Opus summarizer at `max_tokens=16384`: ~$0.75 input + ~$0.30 output = **~$1/report, ~$30/month** at once-daily cadence.
- **Total 6d monthly ceiling: ~$35**, well under any noise-floor concern for a personal project. Pulse-driven (6g) is budget-bound separately.

---

## 4. Watchlist

### 4.1 Config shape (`prompts/intelligence.toml`)

```toml
[[watch]]
handle = "openai"
tags   = ["ai-labs", "first-party"]
notes  = "OpenAI official"

[[watch]]
handle = "sama"
tags   = ["ai-labs", "executive"]
notes  = "Sam Altman"

[[watch]]
handle = "anthropicai"
tags   = ["ai-labs", "first-party"]
notes  = "Anthropic official"

# ... 20–30 seed entries total
```

### 4.2 Loader (`intelligence/watchlist.py`)

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class WatchEntry:
    handle: str
    tags: tuple[str, ...]
    notes: str

def load_watchlist(toml_path: Path) -> list[WatchEntry]: ...
```

- Parses the `[[watch]]` array from the given TOML file
- Rules: `handle` required non-empty string, `tags` optional list (default empty), `notes` optional string (default empty)
- Duplicate `handle` → `RuntimeError` naming the handle and file path
- Missing `[[watch]]` array → empty list (legal, report generation will still run with zero input and fail per §6.2)
- Strips a leading `@` from handles if present, case-insensitive deduplication

### 4.3 Static for 6d, mutable in 6h

This is **static**. The loader runs once at startup. Adding/removing handles requires a process restart (or manual reload). Dynamic follows via chat (`follow_account` tool) are explicitly 6h work — they depend on the feedback loop that only exists once users have reports to react to. The static shape forces you to hand-curate the seed list, which is the right pressure at this stage.

---

## 5. Report schema and storage

### 5.1 JSON schema

```json
{
  "date": "2026-04-15",
  "generated_at": "2026-04-15T08:03:22+08:00",
  "user_tz": "Asia/Shanghai",
  "watchlist_snapshot": ["openai", "sama", "anthropicai", "..."],
  "news_items": [
    {
      "id": "n1",
      "headline": "OpenAI 发布 o5-mini，推理延迟降低 40%",
      "summary": "2–4 句中文概述，解释发生了什么、为什么重要、对技术用户的含义。",
      "importance": "high",
      "importance_reason": "主流模型迭代，直接影响用户在用的 API",
      "topics": ["ai-models", "openai", "inference"],
      "source_tweets": [
        {
          "handle": "sama",
          "url": "https://x.com/sama/status/123456",
          "text": "原始推文文本，保留原语言",
          "posted_at": "2026-04-15T03:17:00Z"
        }
      ]
    }
  ],
  "suggested_accounts": [
    {
      "handle": "some_researcher",
      "reason": "在 n1 条目中被 @sama 引用，连续多日讨论推理优化",
      "seen_in_items": ["n1"]
    }
  ],
  "stats": {
    "tweets_fetched": 487,
    "handles_attempted": 50,
    "handles_succeeded": 47,
    "items_generated": 12,
    "errors": [
      {"handle": "flaky_account", "error": "HTTP 404"}
    ]
  }
}
```

### 5.2 Field notes

- **`date`**: `user_tz`-local date this report represents. Filename matches exactly: `2026-04-15.json`.
- **`generated_at`**: ISO-8601 with timezone offset, captured at write time. Used by the Q&A prompt to show staleness.
- **`user_tz`**: stored alongside so the file is self-describing.
- **`watchlist_snapshot`**: frozen copy of handles actually used. Adding an account tomorrow doesn't retroactively change past reports.
- **`news_items[].id`**: opaque local ID (`n1`, `n2`, ...), unique within a single report. Used by 6h feedback to target specific items.
- **`importance`**: three-level enum `high` / `medium` / `low`. LLM-assigned via the prompt rubric. Deliberately coarse — three buckets are easier for the model to apply consistently than a 1–10 scale.
- **`importance_reason`**: the model's justification. Makes the judgment auditable.
- **`topics`**: flat list of lowercase-hyphenated tags. No taxonomy — the model picks freely. 6f may introduce controlled vocabulary.
- **`source_tweets`**: provenance. Always ≥1 per news_item. Multiple if several accounts covered the same story.
- **`suggested_accounts[].seen_in_items`**: links back to where in the report the model saw them.
- **`stats.errors`**: always present, may be empty.

### 5.3 Hard validation rules (`validate_report_dict`)

- All top-level keys present.
- `date` matches `YYYY-MM-DD`.
- `importance` ∈ `{high, medium, low}`.
- Every `news_items` entry has ≥1 `source_tweets`.
- Every `news_items[].id` is unique within the report.
- Every `suggested_accounts[].seen_in_items` entry references an existing `news_items[].id`.
- `stats.handles_succeeded <= stats.handles_attempted`.
- Violations raise `ValueError` with a path to the offending field.

### 5.4 Storage (`intelligence/report.py`)

- `reports_dir = Path("data/intelligence/reports")`.
- `atomic_write_json(path, data)`: writes to `path.with_suffix(".json.tmp")`, `fsync`s, then `os.rename` to the final path. No partial files left behind on crash.
- `read_report(path)`: reads, JSON-parses, calls `validate_report_dict`, returns the dict.
- `list_report_dates(reports_dir)`: lists matching `YYYY-MM-DD.json` files, returns sorted-descending `list[date]`. Ignores non-matching filenames.
- Overwrite semantics: regenerating the same date silently replaces the existing file via atomic rename.

---

## 6. Generation pipeline

### 6.1 Pipeline function (`intelligence/generate.py`)

```python
async def generate_daily_report(
    *,
    date: date,
    source: TwitterSource,
    llm: LLMProvider,
    summarizer_model: str,
    summarizer_max_tokens: int,
    watchlist: list[WatchEntry],
    reports_dir: Path,
    user_tz: ZoneInfo,
    timeline_since_hours: int,
    max_tweets_per_handle: int,
) -> dict[str, Any]:
    """
    Fetch tweets from the watchlist, summarize via one LLM call, validate, write.
    Returns the written report dict (also written to disk).
    Raises TwitterSourceError if ALL fetches fail.
    Raises ValueError if LLM output is malformed or schema-invalid.
    """
    since = datetime.now(tz=user_tz) - timedelta(hours=timeline_since_hours)
    raw_tweets: list[Tweet] = []
    errors: list[dict] = []

    for entry in watchlist:
        try:
            tweets = await source.fetch_user_timeline(
                entry.handle, since=since, max_results=max_tweets_per_handle,
            )
            raw_tweets.extend(tweets)
        except TwitterSourceError as e:
            errors.append({"handle": entry.handle, "error": str(e)})

    if not raw_tweets:
        # All fetches failed. Do not write a report.
        raise TwitterSourceError(
            f"all {len(watchlist)} fetches failed: "
            f"{', '.join(e['handle'] for e in errors[:5])}"
            + ("..." if len(errors) > 5 else "")
        )

    watchlist_snapshot = [e.handle for e in watchlist]
    user_prompt = build_user_prompt(
        raw_tweets=raw_tweets,
        watchlist_snapshot=watchlist_snapshot,
        errors=errors,
        today_local=date,
        user_tz_name=str(user_tz),
    )

    result_text = await llm.complete(
        system=SUMMARIZER_SYSTEM_PROMPT,
        messages=[Msg(role="user", content=user_prompt)],
        model=summarizer_model,
        max_tokens=summarizer_max_tokens,
    )

    report_dict = parse_json_strict(result_text)        # raises ValueError
    report_dict["generated_at"] = datetime.now(tz=user_tz).isoformat()
    report_dict["user_tz"] = str(user_tz)
    report_dict["watchlist_snapshot"] = watchlist_snapshot
    report_dict.setdefault("stats", {})
    report_dict["stats"]["tweets_fetched"] = len(raw_tweets)
    report_dict["stats"]["handles_attempted"] = len(watchlist)
    report_dict["stats"]["handles_succeeded"] = len(watchlist) - len(errors)
    report_dict["stats"]["errors"] = errors
    report_dict["stats"]["items_generated"] = len(report_dict.get("news_items", []))

    validate_report_dict(report_dict)                    # raises ValueError

    path = reports_dir / f"{date.isoformat()}.json"
    atomic_write_json(path, report_dict)
    return report_dict
```

### 6.2 Failure modes

Per §3.3 of the brainstorming transcript, four named paths:

1. **Partial Twitter failure** (some handles OK, some fail) → report written with errors recorded in `stats.errors`. Tool returns `{item_count, tweets_fetched, errors: N}`. Agent acknowledges partial coverage.
2. **Total Twitter failure** (zero tweets collected) → `TwitterSourceError` raised. No file written. Tool dispatcher surfaces `is_error=True` with the failure list. Agent apologizes in text and offers to retry.
3. **Malformed LLM JSON** → `parse_json_strict` raises `ValueError`. Tool dispatcher surfaces `is_error=True`. Agent apologizes. **No retry** in 6d. If recurring, fix the prompt.
4. **LLM JSON fails schema validation** → `validate_report_dict` raises `ValueError`. Same surfacing, same no-retry.

`parse_json_strict` trims leading/trailing whitespace and an optional markdown code fence (```json … ```) defensively, then calls `json.loads`. Beyond that, strict.

### 6.3 Why deterministic, not agentic

One LLM call per report costs ~$1 on Opus. An agentic generation path (fetch_timeline as a tool, model driving each fetch) would cost 20–50× more, introduce emergent failure modes, and add no quality over a single well-crafted prompt at this scale. The interesting LLM work in 6d lives in the Q&A turn, not generation.

---

## 7. Summarizer prompt

### 7.1 Location and caching

`intelligence/summarizer_prompt.py` defines:

- `SUMMARIZER_SYSTEM_PROMPT: str` — stable across every call, ~1500 tokens. Placed behind `cache_control: ephemeral` on the Anthropic system block when `llm.complete` is called with it. (Assumes `llm.complete` already supports cached system prompts per 6a; if not, this is a trivial extension to pass through.)
- `build_user_prompt(raw_tweets, watchlist_snapshot, errors, today_local, user_tz_name) -> str` — per-call user content.

### 7.2 System prompt structure (English)

English system prompt, Chinese output. Mirrors Manager's pattern (internal instructions English, user-facing text Chinese) — cheaper to iterate on and more reliable for structured output.

Outline:

```
You are the Intelligence agent's daily-report summarizer. You take a batch of raw
tweets from a watchlist and produce a structured daily report in JSON.

## Your job
1. Cluster tweets by topic. Multiple accounts covering the same story become ONE
   news_item.
2. Rank items by importance to a technically sophisticated user who cares about AI,
   ML, infrastructure, and tech industry news.
3. Write concise simplified-Chinese summaries (2–4 sentences each).
4. Flag accounts referenced in the tweets that are not already on the watchlist but
   look worth following (suggested_accounts).
5. Fill out the stats block using the numbers provided in the user message.

## Importance rubric
- high:   major model releases, significant industry moves, named hardware launches,
          safety/regulatory events affecting the field, or anything the user would
          regret missing.
- medium: notable technical posts, thoughtful analyses, mid-tier company news.
- low:    routine updates, personal takes, minor announcements. Include only if the
          tweet volume on the topic justifies it.

## Source trust heuristics
- First-party announcements (company official accounts) > researchers > commentary.
- If a claim appears in only one tweet from an unverified account, mark the
  news_item's summary with "(未经证实)".
- Prefer citing the original source tweet over reposts.

## Hard output rules
- Output ONLY a single JSON object matching the schema below. No prose, no markdown
  fences, no preamble.
- Every news_item must cite at least one source tweet by URL.
- Chinese summaries only. Keep tweet `text` fields in their original language.
- If no tweets warrant a news_item, return an empty `news_items` array. Do not
  invent content.
- `suggested_accounts` may be empty. Quality over quantity.

## Schema
<full JSON schema with inline comments on each field>

## Example
<one short worked example: 3 fake tweets → 1 news_item + 1 suggested_account>
```

### 7.3 User prompt structure

```
Today is 2026-04-15 (Asia/Shanghai). Generate the daily report for this date.

Watchlist snapshot (50 handles): openai, sama, anthropicai, ...

Handles attempted: 50
Handles succeeded: 47
Handles failed: [{"handle": "flaky_account", "error": "HTTP 404"}, ...]
Tweets fetched: 487

Raw tweets follow, grouped by handle, newest first:

=== @sama ===
[2026-04-15T03:17:00Z] url=https://x.com/sama/status/123456
Full tweet text here...

[2026-04-15T01:05:00Z] url=https://x.com/sama/status/123455
Another tweet...

=== @anthropicai ===
...
```

Grouped by handle, newest first. Handles with zero tweets are omitted (not worth the tokens; the watchlist snapshot already names them).

---

## 8. Intelligence agent class

### 8.1 Files

- `src/project0/agents/intelligence.py` — full rewrite, replaces `intelligence_stub`
- `src/project0/agents/_tool_loop.py` — NEW, extracted from `manager.py`
- `src/project0/agents/manager.py` — refactored to import `_agentic_loop` from `_tool_loop` instead of defining it locally

### 8.2 `_tool_loop.py` — shared agentic loop

```python
# src/project0/agents/_tool_loop.py
@dataclass
class TurnState:
    delegation_target: str | None = None
    delegation_handoff: str | None = None
    delegation_payload: dict[str, Any] | None = None

DispatchTool = Callable[[ToolCall, TurnState], Awaitable[tuple[str, bool]]]

async def run_agentic_loop(
    *,
    llm: LLMProvider,
    system: str,
    initial_user_text: str,
    tools: list[ToolSpec],
    dispatch_tool: DispatchTool,
    max_iterations: int,
    max_tokens: int,
) -> AgentResult:
    turn_state = TurnState()
    messages: list = [Msg(role="user", content=initial_user_text)]

    for _ in range(max_iterations):
        result = await llm.complete_with_tools(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens,
        )
        if result.kind == "text":
            return AgentResult(
                reply_text=result.text or "",
                delegate_to=turn_state.delegation_target,
                handoff_text=turn_state.delegation_handoff,
                delegation_payload=turn_state.delegation_payload,
            )
        messages.append(AssistantToolUseMsg(tool_calls=result.tool_calls, text=result.text))
        for call in result.tool_calls:
            content_str, is_error = await dispatch_tool(call, turn_state)
            messages.append(ToolResultMsg(
                tool_use_id=call.id, content=content_str, is_error=is_error,
            ))

    raise LLMProviderError(f"agentic loop exceeded max_iterations={max_iterations}")
```

Manager's existing `_agentic_loop` is replaced by a one-line call into `run_agentic_loop(...)`. All existing Manager tests remain green — the refactor is behavior-preserving.

### 8.3 `Intelligence` class

```python
class Intelligence:
    def __init__(
        self, *,
        llm: LLMProvider,
        twitter: TwitterSource,
        messages_store: MessagesStore,
        persona: IntelligencePersona,
        config: IntelligenceConfig,
        watchlist: list[WatchEntry],
        reports_dir: Path,
        user_tz: ZoneInfo,
    ):
        self._llm = llm
        self._twitter = twitter
        self._messages = messages_store
        self._persona = persona
        self._config = config
        self._watchlist = watchlist
        self._reports_dir = reports_dir
        self._user_tz = user_tz
        self._tool_specs = self._build_tool_specs()

    async def handle(self, env: Envelope) -> AgentResult | None:
        reason = env.routing_reason
        if reason == "direct_dm":
            return await self._run_chat_turn(env, self._persona.dm_mode)
        if reason in ("mention", "focus"):
            return await self._run_chat_turn(env, self._persona.group_addressed_mode)
        if reason == "default_manager":
            return await self._run_delegated_turn(env)
        log.debug("intelligence: ignoring routing_reason=%s", reason)
        return None
```

### 8.4 Tool surface (four tools)

| Tool | Input schema sketch | Returns |
|---|---|---|
| `generate_daily_report` | `{"date": "YYYY-MM-DD"?}` — optional, defaults to today in user_tz | `{"path": str, "item_count": int, "tweets_fetched": int, "handles_failed": int}` |
| `get_latest_report` | `{}` | JSON-stringified latest report, or `"no reports available"` |
| `get_report` | `{"date": "YYYY-MM-DD"}` | JSON-stringified report for that date, or `"no report for <date>"` |
| `list_reports` | `{"limit": int}` (default 7) | JSON-stringified list `[{"date": "YYYY-MM-DD", "item_count": int}, ...]` |

`generate_daily_report` is the only state-mutating tool. The other three are cheap reads.

**Why `get_latest_report` exists despite being redundant with `list_reports`+`get_report`:** it resolves the common "what's in today's report" question in one tool call instead of two. Worth the small redundancy.

### 8.5 Tool dispatch (`_dispatch_tool`)

```python
async def _dispatch_tool(self, call: ToolCall, turn_state: TurnState) -> tuple[str, bool]:
    try:
        if call.name == "generate_daily_report":
            date_str = call.input.get("date")
            target_date = (
                date.fromisoformat(date_str) if date_str
                else datetime.now(tz=self._user_tz).date()
            )
            report = await generate_daily_report(
                date=target_date,
                source=self._twitter,
                llm=self._llm,
                summarizer_model=self._config.summarizer_model,
                summarizer_max_tokens=self._config.summarizer_max_tokens,
                watchlist=self._watchlist,
                reports_dir=self._reports_dir,
                user_tz=self._user_tz,
                timeline_since_hours=self._config.timeline_since_hours,
                max_tweets_per_handle=self._config.max_tweets_per_handle,
            )
            return json.dumps({
                "path": str(self._reports_dir / f"{target_date}.json"),
                "item_count": len(report.get("news_items", [])),
                "tweets_fetched": report["stats"]["tweets_fetched"],
                "handles_failed": len(report["stats"]["errors"]),
            }, ensure_ascii=False), False

        if call.name == "get_latest_report":
            dates = list_report_dates(self._reports_dir)
            if not dates:
                return "no reports available", False
            return json.dumps(read_report(self._reports_dir / f"{dates[0]}.json"), ensure_ascii=False), False

        if call.name == "get_report":
            target = date.fromisoformat(call.input["date"])
            path = self._reports_dir / f"{target}.json"
            if not path.exists():
                return f"no report for {target}", False
            return json.dumps(read_report(path), ensure_ascii=False), False

        if call.name == "list_reports":
            limit = int(call.input.get("limit", 7))
            dates = list_report_dates(self._reports_dir)[:limit]
            results = []
            for d in dates:
                rep = read_report(self._reports_dir / f"{d}.json")
                results.append({"date": d.isoformat(), "item_count": len(rep.get("news_items", []))})
            return json.dumps(results, ensure_ascii=False), False

        return f"unknown tool: {call.name}", True

    except TwitterSourceError as e:
        return f"twitter source error: {e}", True
    except (ValueError, KeyError) as e:
        return f"invalid input or data: {e}", True
```

**Intelligence never delegates.** No `delegate_to_*` tools. `TurnState` is still used (for the shared loop's API shape) but its delegation fields are always `None`.

### 8.6 Chat turn entry (`_run_chat_turn`)

```python
async def _run_chat_turn(self, env: Envelope, mode_persona: str) -> AgentResult:
    latest_report = self._try_read_latest_report()         # dict or None
    recent_messages = self._messages.recent_for_chat(
        env.telegram_chat_id, window=self._config.transcript_window,
    )
    system = self._persona.core + mode_persona + self._persona.tool_use_guide
    initial_user_text = build_qa_user_prompt(
        latest_report=latest_report,
        current_date_local=datetime.now(tz=self._user_tz).date(),
        recent_messages=recent_messages,
        current_user_message=env.body,
    )
    return await run_agentic_loop(
        llm=self._llm,
        system=system,
        initial_user_text=initial_user_text,
        tools=self._tool_specs,
        dispatch_tool=self._dispatch_tool,
        max_iterations=self._config.max_tool_iterations,
        max_tokens=self._config.qa_max_tokens,
    )
```

`_try_read_latest_report` returns `None` on no-reports-found, logs and returns `None` on read errors. The Q&A prompt handles staleness and missing reports in text — the model is instructed to compare `latest_report["date"]` against `current_date_local`.

### 8.7 Delegated turn entry (`_run_delegated_turn`)

```python
async def _run_delegated_turn(self, env: Envelope) -> AgentResult:
    query = (env.payload or {}).get("query", env.body)
    latest_report = self._try_read_latest_report()
    system = self._persona.core + self._persona.delegated_mode + self._persona.tool_use_guide
    initial_user_text = build_delegated_user_prompt(
        latest_report=latest_report,
        current_date_local=datetime.now(tz=self._user_tz).date(),
        query=query,
    )
    return await run_agentic_loop(
        llm=self._llm,
        system=system,
        initial_user_text=initial_user_text,
        tools=self._tool_specs,
        dispatch_tool=self._dispatch_tool,
        max_iterations=self._config.max_tool_iterations,
        max_tokens=self._config.qa_max_tokens,
    )
```

Delegated turns have no `recent_messages` — the query from Manager is treated as self-contained, per the 6c persona rule that instructs Manager to make delegation queries self-contained.

### 8.8 Generation is synchronous on the loop

The `generate_daily_report` tool call blocks the agent's tool-use loop until the pipeline finishes. A 50-handle fetch + one Opus call is realistically 30–90 seconds. No backgrounding, no progress reports, no task IDs in 6d. User sees nothing during the wait except (optionally) a "typing…" Telegram indicator if we wire one up. Backgrounding belongs to 6g where pulses need it.

---

## 9. Persona (`prompts/intelligence.md`)

Five Chinese sections, same parser as Manager/Secretary (exact header match + near-miss detection with suggestion).

### 9.1 `# 情报 — 角色设定` — core identity

The Intelligence agent is a briefing specialist. Monitors tech/AI news from Twitter/X. Does not inspect other agents' memory. Never fabricates. Speaks concisely, cites sources when making claims. Focus: helping the user stay informed without drowning them.

### 9.2 `# 模式：私聊` — DM mode

User is talking to Intelligence directly. Two sub-cases:

1. If the user asks Intelligence to generate a report ("生成今天的报告", "给我来份今天的简报", etc.), call `generate_daily_report` and return a short ack with stats (item count, tweets fetched, any handles that failed). Offer to highlight the top items next.
2. Otherwise, the user is asking about content. Answer from the pre-loaded latest report. If the report is stale (older than today), mention the date. If no report exists yet, say so and offer to generate one.

### 9.3 `# 模式：群聊点名` — group addressed mode

Same Q&A shape as DM mode but reply frame is less intimate. No report-generation in group mode unless explicitly asked.

### 9.4 `# 模式：被经理委派` — delegated mode

Manager called `delegate_to_intelligence` with a self-contained query. Answer the query from the latest report. If the report doesn't cover the query or is stale, say so briefly rather than inventing. Do not generate a new report from a delegated turn (Manager can ask the user to do that).

### 9.5 `# 模式：工具使用守则` — tool-use rules

- The latest report is already in your prompt. **Do not call `get_latest_report`** — that wastes a tool turn.
- Only call `generate_daily_report` when the user clearly asked to generate one.
- Never claim a news item exists without citing its `source_tweets[].url`.
- If no report exists yet and the user asks a content question, tell them and offer to generate one.
- `get_report` and `list_reports` are for questions about *older* reports ("yesterday", "last week"). Use them only when the user asks about past days.
- After generating a report, stop calling tools and emit a final text ack.

---

## 10. Config (`prompts/intelligence.toml`)

```toml
[llm.summarizer]
model            = "claude-opus-4-6"
max_tokens       = 16384

[llm.qa]
model            = "claude-sonnet-4-6"
max_tokens       = 2048

[context]
transcript_window   = 10
max_tool_iterations = 6

[twitter]
timeline_since_hours  = 24
max_tweets_per_handle = 50

[[watch]]
handle = "openai"
tags   = ["ai-labs", "first-party"]
notes  = "OpenAI official"

[[watch]]
handle = "sama"
tags   = ["ai-labs", "executive"]
notes  = "Sam Altman"

# ... 20-30 seed entries
```

Loader: `load_intelligence_config(Path("prompts/intelligence.toml")) -> IntelligenceConfig`, mirroring `load_manager_config`. Missing keys raise `RuntimeError` with file path and key name.

`load_watchlist(same path)` reads only the `[[watch]]` array, independent of `load_intelligence_config`. The two loaders can be called separately (as they are from `main.py`).

### 10.1 Dataclass shapes

```python
@dataclass(frozen=True)
class IntelligenceConfig:
    summarizer_model: str
    summarizer_max_tokens: int
    qa_model: str
    qa_max_tokens: int
    transcript_window: int
    max_tool_iterations: int
    timeline_since_hours: int
    max_tweets_per_handle: int

@dataclass(frozen=True)
class IntelligencePersona:
    core: str                    # # 情报 — 角色设定
    dm_mode: str                 # # 模式：私聊
    group_addressed_mode: str    # # 模式：群聊点名
    delegated_mode: str          # # 模式：被经理委派
    tool_use_guide: str          # # 模式：工具使用守则
```

Both dataclasses live in `src/project0/agents/intelligence.py` alongside the `Intelligence` class, mirroring how `ManagerConfig` and `ManagerPersona` live in `agents/manager.py`.

### 10.2 Helper functions referenced in §8

The chat-turn entry points call three helper functions that live in `intelligence/summarizer_prompt.py` alongside the summarizer prompt (same module because they share prompt-building logic):

- `build_user_prompt(raw_tweets, watchlist_snapshot, errors, today_local, user_tz_name) -> str` — used by the generation pipeline (§6.1).
- `build_qa_user_prompt(latest_report, current_date_local, recent_messages, current_user_message) -> str` — used by `_run_chat_turn` (§8.6).
- `build_delegated_user_prompt(latest_report, current_date_local, query) -> str` — used by `_run_delegated_turn` (§8.7).

`parse_json_strict(text) -> dict` is a small helper in `intelligence/report.py`, next to `validate_report_dict`.

### 10.3 `LLMProvider.complete` signature assumption

The pipeline in §6.1 calls `llm.complete(system=..., messages=..., model=..., max_tokens=...)`. This assumes `LLMProvider.complete` accepts `model` and `max_tokens` as keyword arguments, letting callers override the provider's default per-call. If the existing signature in 6a does not accept these, 6d's implementation plan must extend it as a prerequisite task — a small, backwards-compatible change (default values matching the current behavior).

---

## 11. Composition root wiring (`src/project0/main.py`)

Additions to `_run`, alongside the existing Manager and Secretary wiring:

```python
# 6d: Intelligence agent
from project0.intelligence.twitterapi_io import TwitterApiIoSource
from project0.intelligence.watchlist import load_watchlist
from project0.agents.intelligence import Intelligence, load_intelligence_persona, load_intelligence_config
from project0.agents.registry import register_intelligence

intelligence_persona   = load_intelligence_persona(Path("prompts/intelligence.md"))
intelligence_cfg       = load_intelligence_config(Path("prompts/intelligence.toml"))
intelligence_watchlist = load_watchlist(Path("prompts/intelligence.toml"))

twitter_source = TwitterApiIoSource(api_key=os.environ["TWITTERAPI_IO_API_KEY"])

reports_dir = Path("data/intelligence/reports")
reports_dir.mkdir(parents=True, exist_ok=True)

intelligence = Intelligence(
    llm=llm,
    twitter=twitter_source,
    messages_store=store.messages(),
    persona=intelligence_persona,
    config=intelligence_cfg,
    watchlist=intelligence_watchlist,
    reports_dir=reports_dir,
    user_tz=ZoneInfo(settings.user_tz),
)
register_intelligence(intelligence.handle)
log.info(
    "intelligence registered (summarizer=%s, qa=%s, watchlist=%d)",
    intelligence_cfg.summarizer_model,
    intelligence_cfg.qa_model,
    len(intelligence_watchlist),
)

# Inside the existing TaskGroup shutdown, close twitter_source.
# (await twitter_source.aclose() alongside other teardown)
```

### New environment variables

- `TWITTERAPI_IO_API_KEY` — twitterapi.io auth. Required. Read directly from `os.environ` at startup, not through `Settings` (matches 6c's pattern of per-agent runtime keys).

### Existing environment variables (already wired from 6a)

- `TELEGRAM_BOT_TOKEN_INTELLIGENCE` — Intelligence's own Telegram bot token. Already present in `AGENT_SPECS["intelligence"]`. Bot poller fan-out picks it up automatically once `register_intelligence(...)` installs the real handler.

### `registry.py`

Add `register_intelligence(handle)` symmetric with `register_manager`. The existing `intelligence_stub` import is removed from `AGENT_REGISTRY` so the key is populated only at runtime via `register_intelligence`.

---

## 12. Testing strategy

All tests unit/integration. No live twitterapi.io calls in CI. One optional live smoke test gated on `TWITTERAPI_IO_API_KEY`, matching the 6b live-calendar pattern.

### 12.1 New test files

**`tests/intelligence/test_twitterapi_io_source.py`**
- Mocked `httpx` client. Verify request URL, headers, query params, auth.
- Recorded JSON fixture → `list[Tweet]` with all fields populated.
- HTTP 4xx/5xx → `TwitterSourceError` with status code in message.
- Network timeout → `TwitterSourceError`.
- Empty timeline → empty list, no error.

**`tests/intelligence/test_fake_source.py`**
- Seeded `FakeTwitterSource` returns expected tweets.
- `fetch_user_timeline(since=...)` filters by `posted_at`.
- Unknown handle → `TwitterSourceError`.

**`tests/intelligence/test_watchlist_loader.py`**
- Valid TOML → `list[WatchEntry]`.
- Missing `handle` → `RuntimeError` with file path and field name.
- Duplicate handle → `RuntimeError`.
- Missing `[[watch]]` array → empty list.
- Leading `@` stripped, case-insensitive dedup.

**`tests/intelligence/test_report_schema.py`**
- Valid dict passes `validate_report_dict`.
- Each hard rule from §5.3 has a failing case: bad date format, invalid importance, empty source_tweets, dangling `seen_in_items`, `handles_succeeded > handles_attempted`, duplicate `news_items[].id`.
- `atomic_write_json` uses `.tmp` + rename; patched-rename test ensures no partial file remains on crash.
- `read_report` round-trips.
- `list_report_dates` returns sorted desc, ignores non-matching filenames.

**`tests/intelligence/test_generate_pipeline.py`**
- Happy path: 3 handles seeded, `FakeProvider` scripted with valid JSON → file written, returned dict matches.
- Partial fetch failure: 2 of 3 handles succeed, 1 raises → report written, errors recorded.
- Total fetch failure → `TwitterSourceError` raised, no file written.
- LLM returns malformed JSON → `ValueError`, no file written.
- LLM returns JSON that fails schema validation → `ValueError`, no file written.
- `date` param defaults to "today in `user_tz`" when omitted.
- Regenerating same date overwrites atomically.
- `parse_json_strict` tolerates markdown code fence wrapping.

**`tests/intelligence/test_summarizer_prompt_build.py`**
- `build_user_prompt` groups tweets by handle, newest first.
- Handle with zero tweets is omitted.
- Errors list rendered correctly.
- Watchlist snapshot included verbatim.
- Golden-fixture snapshot test of the full formatted prompt (catches accidental drift).

**`tests/agents/test_intelligence_class.py`**
Uses `FakeProvider.complete_with_tools` to script tool-use turns.
- DM chat turn, no report exists, content question → plain text reply ("no report, want me to generate one?").
- DM chat turn, no report exists, generation request → model calls `generate_daily_report`, pipeline runs with `FakeTwitterSource`, ack emitted.
- DM chat turn, latest report exists → report JSON injected into `initial_user_text`, model replies from context without calling tools.
- DM chat turn, user asks about yesterday → model calls `get_report(date=yesterday)`, replies.
- DM chat turn, user asks about last week → model calls `list_reports(limit=7)`, then `get_report` for a specific date.
- Delegated turn (`routing_reason="default_manager"`, payload `{kind: "query", query: "..."}`) → agent runs, replies via handoff chain. No listener fan-out, no further delegation.
- Tool raising `TwitterSourceError` → `is_error=True` tool_result fed back → next iteration emits apology.
- Exceeding `max_tool_iterations` → `LLMProviderError`.
- `delegate_to` always `None`.

**`tests/agents/test_intelligence_persona_load.py`**
- Five-section parser, near-miss header detection (copy Manager's test pattern).

**`tests/agents/test_register_intelligence.py`**
- `register_intelligence(handle)` installs handler in `AGENT_REGISTRY`.
- `AGENT_SPECS["intelligence"].token_env_key == "TELEGRAM_BOT_TOKEN_INTELLIGENCE"` (verifies the 6a wiring is still intact after stub removal).

**`tests/agents/test_tool_loop_shared.py`**
- After extracting `run_agentic_loop` into `_tool_loop.py`, verify Manager's existing tool-loop tests still pass (behavior-preserving refactor).
- One direct test of `run_agentic_loop` against a custom dispatch function, independent of any agent class.

### 12.2 Optional live smoke test

**`tests/intelligence/test_twitterapi_io_live.py`** — gated on `TWITTERAPI_IO_API_KEY`, skipped with `pytest.skip(...)` if unset (matching `tests/calendar/test_google_calendar_live.py` from 6b).
- `fetch_user_timeline("sama", since=24h ago)` returns non-empty list.
- Tweet fields populated (text, url, posted_at, counts).

### 12.3 Deliberately not tested

- End-to-end Telegram-bot smoke. Trust the existing I/O layer.
- LLM output quality. Manual iteration via the live smoke path.
- Cost/budget. Runtime concern.

### 12.4 Test support additions

- `tests/intelligence/conftest.py`: `build_fake_report_dict()` returns a valid minimal report dict for reuse, plus a `FakeTwitterSource` fixture seeded with 3 handles × ~5 tweets each.

---

## 13. Open risks

1. **twitterapi.io single-vendor dependency.** If the service degrades or disappears, reports stop. Mitigation: `TwitterSource` protocol makes swapping to Apify or another provider a one-file change.

2. **Opus cost ceiling on busy days.** Rich reports could burn ~$1.50+ per generation. Fine at once-daily but explodes if 6g adds pulse-driven reports without budget awareness. Flag for 6g scope: will need either a Sonnet fallback for pulse-driven runs, or explicit budget caps.

3. **JSON parse failures from Opus.** Less likely than Sonnet, but possible. No retry in 6d means one bad generation = one lost day's report. Fix is prompt iteration (stronger "output ONLY JSON" framing), not retry logic.

4. **Stale-report staleness is a soft model judgment.** Q&A relies on the model noticing "this report is 3 days old" and saying so. The dm_mode persona instructs it to compare dates, but the rule is not enforced in code. If the model silently answers from stale context, the user gets plausible-sounding out-of-date briefings.

5. **Watchlist quality is entirely hand-curated.** Bad seeds → bad reports. No automatic quality signal until 6h. Mitigation: start with first-party company accounts + a small number of trusted researchers; expand from early reports' `suggested_accounts` field.

6. **Extended thinking deferred.** Opus without extended thinking still does reasonable clustering, but extended thinking would likely improve importance ranking and cross-source dedup. Deferred to 6f (or earlier if summarization quality is insufficient). Enabling it requires extending the `LLMProvider.complete` protocol to accept thinking parameters — outside 6d scope.

7. **Delegated turn context gap.** When Manager delegates to Intelligence, the delegation payload carries only `{kind, query}`. Intelligence has no access to Manager's transcript. If the query is ambiguous ("what did he say?"), Intelligence will answer from the latest report without the missing context. Mitigation: Manager's persona (6c) instructs it to make delegation queries self-contained. Soft rule.

8. **Report regeneration is last-write-wins.** Calling `generate_daily_report` twice on the same date silently overwrites. Useful for "I added a handle, regenerate" but leaves no audit trail. 6h's feedback loop will need to reconcile this if users react to a report that later gets overwritten.

9. **Tool-loop refactor scope creep.** Extracting `_agentic_loop` from Manager into `_tool_loop.py` touches 6c code. Behavior-preserving, but a reviewer should verify all Manager tests remain green after the extraction. If this feels risky, the alternative is to duplicate the loop in Intelligence for 6d and file the extraction as a small 6d.5 follow-up.
