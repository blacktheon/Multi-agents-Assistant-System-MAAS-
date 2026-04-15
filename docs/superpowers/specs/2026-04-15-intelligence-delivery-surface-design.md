# Sub-project 6e — Intelligence delivery surface (webapp + thumbs feedback + link tool + extended thinking)

**Date:** 2026-04-15
**Status:** Design approved, ready for implementation plan
**Depends on:** 6d (Intelligence agent, DailyReport schema, `list_report_dates`, `read_report`, `prompts/intelligence.toml`)

---

## 1. Context and scope

Sub-project 6d shipped the Intelligence agent: deterministic daily-report generation (Opus summarizer, one LLM call, validated JSON written to `data/intelligence/reports/YYYY-MM-DD.json`) and shallow Q&A over the latest report via a Sonnet tool-use loop. The output is currently only inspectable via `cat | jq`. There is no user-visible rendering surface.

6e gives Intelligence a **reading surface**: a local FastAPI webapp, reached from any device (including the user's phone on cellular) via Tailscale, that renders the latest report, lets the user browse back through history, and captures per-item thumbs-up/down feedback as an append-only event log. It also adds a single new agent tool so 顾瑾 can reply in Telegram with a stable URL link to any report page, and enables extended thinking on the Opus summarizer call as a quality bump.

6e deliberately stays within the reading/presentation surface and the minimum feedback-capture signal needed to unblock preference learning in a future sub-project. It does **not** read feedback events back to influence generation, memory, or ranking. Learning is a later concern.

### Roadmap reshape

6e also serves as a formal reshape of the Intelligence sub-project roadmap:

- **Old 6f (deep conversational layer — cross-report retrieval, 7-day topic memory, follow-up web search, extended thinking) is retired as a named sub-project.** Cross-report retrieval is folded into 6e via the existing `get_report` tool (Intelligence already has it from 6d; the Q&A behavior tweak is persona-only). Extended thinking on the summarizer call is folded into 6e as a quality bump. The richer retrieval / topic-memory / follow-up-search behaviors are pushed out indefinitely.
- **Next sub-project (formerly two-source thinking) is renumbered to 6f.** It covers the dedicated intel Twitter account + dynamic follows fetching + automatic discovery via Twitter search queries. Decoupled from 6e — 6e's renderer works identically for one-source or two-source reports.
- **Old 6g (pulse-driven scheduling)** and **old 6h (preference learning + feedback loop consumption)** are pushed further out. Not on the near-term roadmap.

### In scope

- New package `src/project0/intelligence_web/` (FastAPI app factory, routes, rendering adapter, Jinja2 templates, vanilla-JS thumbs client, static CSS, feedback read/write module, config dataclass).
- Five HTTP routes: `GET /`, `GET /reports/{date}`, `GET /history`, `POST /api/feedback/thumbs`, `GET /healthz`, plus static file mount at `/static`.
- Layout B from brainstorming: top date navigator (prev/next arrows + native `<select>` for jump), full-width report body below. Mobile-friendly, single-page server-rendered, no client-side framework.
- Thumbs feedback: one event type (`thumbs`, `score ∈ {-1, 0, 1}`), monthly append-only JSONL files under `data/intelligence/feedback/YYYY-MM.jsonl`, current-state derivation via latest-write-wins scan of current + previous month.
- New Intelligence tool `get_report_link(date: str | "latest") -> {"url": ..., "date": ...}` with existence validation.
- Persona additions to `prompts/intelligence.md`: rules for pasting link URLs verbatim, citing source-tweet URLs in Q&A replies when relevant.
- Extended thinking enabled on the Opus summarizer call with `budget_tokens = 16384`; `summarizer_max_tokens` bumped to `32768`.
- `LLMProvider.complete` extended with optional `thinking_budget_tokens` parameter (default `None`).
- New `[web]` section in `prompts/intelligence.toml` with `public_base_url`, `bind_host`, `bind_port`, `reports_dir`, `feedback_dir`, `user_tz`.
- Webapp integrated into `main.py`'s existing `TaskGroup` as one more `asyncio.Task` alongside the Telegram bot pollers. Shared shutdown event. Uvicorn's signal handlers disabled so `main.py` remains the sole signal authority.
- New dev convenience script `scripts/dev_web.sh` running uvicorn `--reload` in isolation against the real reports directory (for template/CSS iteration loops).
- New manual smoke script `scripts/smoke_web.sh` for developer sanity.
- Full unit + integration test coverage. Estimated +60–80 tests, project total going from 256 (post-6d) to roughly 320.
- README update: new "Webapp" section explaining Tailscale access, TOML fields, disk layout.
- `.gitignore`: add `data/intelligence/feedback/` alongside existing reports entry.

### Out of scope (explicitly deferred)

- **Two-source generation** (dedicated intel Twitter account, dynamic follows, search-based discovery) — next sub-project (new 6f).
- **Pulse-driven scheduling / cron generation** — pushed out indefinitely.
- **Preference learning / memory writes from feedback** — no code in 6e reads the feedback log back. The `type` discriminator exists so future sub-projects can add events without migration.
- **Additional feedback event types** — no `more_like_this`, no `mute_topic`, no `follow_account`, no free-text notes, no report-level ratings. Just thumbs.
- **Chat surface on the webpage** — Q&A stays in Telegram.
- **Auth / TLS / rate limiting / CSRF** — Tailscale is the gate. Documented and accepted.
- **Multi-user support** — single-user tool.
- **Report editing, annotation, deletion, search** — reports are read-only rendered artifacts.
- **Cross-report deep retrieval / topic memory / follow-up web search during Q&A** — retired from the roadmap for now.
- **Email / PDF / Obsidian / RSS delivery** — no non-HTTP surfaces in 6e.
- **Reverse proxy / HTTPS via `tailscale serve` / systemd unit** — deployment concerns, handled outside the repo.
- **Docker packaging** — not planned.
- **Analytics beyond uvicorn's access log** — no custom tracking.
- **Accessibility audit / i18n framework** — single-user, single-language-first.
- **Regenerate-now button on the webpage** — generation stays a Telegram-initiated action.
- **Caching of report reads** — every request reads from disk. Reports are small (<100KB) and reads are cheap.

---

## 2. Module layout

The webapp lives as a new sibling package under `src/project0/`, alongside `intelligence/` (infrastructure) and `agents/` (the agent classes themselves). It imports from `intelligence` but nothing in `intelligence` or `agents` imports from it.

```
src/project0/
    intelligence/              # existing (6d), unchanged
        generate.py            # +thinking_budget_tokens param plumbed through
        report.py              # unchanged
        source.py              # unchanged
        twitterapi_io.py       # unchanged
        fake_source.py         # unchanged
        watchlist.py           # unchanged
        summarizer_prompt.py   # unchanged
    intelligence_web/          # NEW
        __init__.py
        app.py                 # create_app(config: WebConfig) -> FastAPI factory
        routes.py              # APIRouter with all routes
        rendering.py           # build_report_context + Jinja2 filters
        feedback.py            # FeedbackEvent + append_thumbs + load_thumbs_state_for
        config.py              # WebConfig dataclass + from_toml_section
        templates/
            base.html
            report.html
            history.html
            empty.html
        static/
            style.css
            thumbs.js
    agents/
        intelligence.py        # +get_report_link tool, +public_base_url param
        manager.py             # unchanged
        secretary.py           # unchanged
        registry.py            # unchanged
        _tool_loop.py          # unchanged
    llm/
        provider.py            # LLMProvider protocol + AnthropicProvider concrete impl;
                               # complete() gains thinking_budget_tokens keyword and
                               # AnthropicProvider maps it to the SDK thinking param
    main.py                    # +1 task in _run: _run_web as asyncio.Task

prompts/
    intelligence.md            # +persona block on link-sharing and source citations
    intelligence.toml          # +[web] section, +thinking_budget_tokens in [llm.summarizer],
                               #  bumped summarizer max_tokens

data/
    intelligence/
        reports/               # existing, gitignored, read-only from webapp's perspective
        feedback/              # NEW, gitignored, write-only from webapp's perspective
            2026-04.jsonl
            2026-03.jsonl
            ...

scripts/
    dev_web.sh                 # NEW: uvicorn --reload on port 8081 for template iteration
    smoke_web.sh               # NEW: manual end-to-end sanity check

tests/
    intelligence_web/          # NEW
        conftest.py
        test_config.py
        test_feedback_append.py
        test_feedback_load_state.py
        test_rendering.py
        test_routes_report.py
        test_routes_history.py
        test_routes_thumbs.py
        test_routes_errors.py
        test_app_factory.py
    agents/
        test_intelligence_tool_dispatch.py    # extended with get_report_link tests
        test_intelligence_persona_load.py     # extended with link-rule guard
    intelligence/
        test_generate_pipeline.py             # extended with thinking-budget test
    llm/                                      # extended (or new) test file
        test_anthropic_thinking.py            # provider-level thinking param tests
```

### Architectural principles

1. **One-way dependency.** `intelligence_web` imports from `intelligence` (`read_report`, `list_report_dates`, `DailyReport`) but `intelligence` and `agents` never import from `intelligence_web`. The agent package has no knowledge that a webapp exists; its only connection is the `public_base_url` string it uses to construct link URLs.
2. **Webapp is stateless w.r.t. reports.** Every request reads from disk. No caching. Reports are small and reads are cheap; staleness bugs from caching are not worth negligible perf wins.
3. **Feedback is append-only.** The webapp never updates or deletes feedback events. Current state is derived by scanning history with latest-write-wins.
4. **Agent ↔ webapp communication goes through config and filesystem, not shared memory.** They could run in separate processes with zero code change.
5. **Jinja2 templates are dumb.** All logic (sorting, grouping, formatting, merging feedback state) lives in `rendering.py`. Templates only iterate and emit.
6. **Single `WebConfig` loaded once at startup, shared between agent and webapp.** No drift possible on base URL or directory paths.

---

## 3. URL map and routes

### 3.1 URL map

| Method | Path | Purpose | Response |
|---|---|---|---|
| `GET` | `/` | Latest report (auto-pick newest from disk) | `text/html` — rendered `report.html` |
| `GET` | `/reports/{date}` | Specific report by `YYYY-MM-DD` | `text/html`; 400 on bad format, 404 on missing file |
| `GET` | `/history` | Browsable index of all past reports | `text/html` — rendered `history.html` |
| `POST` | `/api/feedback/thumbs` | Record a thumbs event | `application/json` — `{"ok": true}` |
| `GET` | `/healthz` | Liveness probe | `text/plain` — `"ok"` |
| `GET` | `/static/*` | CSS, JS | Static file via FastAPI `StaticFiles` |

No trailing-slash redirects. No query-parameter-based date selection. No API versioning.

### 3.2 Route handlers (`routes.py`)

```python
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from datetime import date

router = APIRouter()

class ThumbsPayload(BaseModel):
    report_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    item_id: str = Field(min_length=1, max_length=64)
    score: int = Field(ge=-1, le=1)

@router.get("/", response_class=HTMLResponse)
async def root(request: Request, cfg: WebConfig = Depends(get_config)) -> HTMLResponse: ...

@router.get("/reports/{date_str}", response_class=HTMLResponse)
async def report_by_date(request: Request, date_str: str,
                          cfg: WebConfig = Depends(get_config)) -> HTMLResponse: ...

@router.get("/history", response_class=HTMLResponse)
async def history(request: Request, cfg: WebConfig = Depends(get_config)) -> HTMLResponse: ...

@router.post("/api/feedback/thumbs")
async def post_thumbs(payload: ThumbsPayload,
                      cfg: WebConfig = Depends(get_config)) -> JSONResponse: ...

@router.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"
```

`cfg: WebConfig = Depends(get_config)` is a FastAPI dependency-provider bound at `create_app` time to the `WebConfig` instance built from `intelligence.toml`. The factory shape in `app.py` lets tests construct an isolated app with a fake `WebConfig` pointing at a tmp directory.

### 3.3 The prev/next navigator (layout B)

```python
def build_report_context(
    *,
    report_dict: dict,
    feedback_state: dict[str, int],
    all_dates: list[date],
    current: date,
    public_base_url: str,
) -> dict:
    idx = all_dates.index(current)
    prev_date = all_dates[idx + 1] if idx + 1 < len(all_dates) else None
    next_date = all_dates[idx - 1] if idx - 1 >= 0 else None
    return {
        "report": report_dict,
        "feedback": feedback_state,
        "current_date": current.isoformat(),
        "prev_href": f"/reports/{prev_date.isoformat()}" if prev_date else None,
        "next_href": f"/reports/{next_date.isoformat()}" if next_date else None,
        "all_dates": [d.isoformat() for d in all_dates],
        "public_base_url": public_base_url,
    }
```

`all_dates` is sorted descending (newest first), so "prev" (the button with `‹`) navigates to **older** reports and "next" (`›`) navigates to **newer** reports. The `<select>` dropdown in the nav bar uses `all_dates` directly.

### 3.4 Error and empty-state handling

- `GET /reports/not-a-date` → 400 with plain-text FastAPI default error page.
- `GET /reports/2099-01-01` (valid format, no file) → 404 rendered as a small HTML page: "No report for 2099-01-01. Ask 顾瑾 to generate one, or check /history."
- `GET /` when no reports exist → rendered `empty.html`: "No reports yet. Ask 顾瑾 to generate your first daily report."
- `GET /history` with empty reports dir → renders the history template with an empty list and the same empty-state message.

### 3.5 Template files

```
base.html       — <html> shell, <head>, /static/style.css link, header, footer;
                  defines {% block content %}{% endblock %}
report.html     — extends base; date nav bar, news items sorted high→medium→low,
                  suggested accounts grid, thumbs buttons
history.html    — extends base; list of all report dates grouped by month,
                  each linked to /reports/{date}
empty.html      — extends base; short message for "no reports yet"
```

**`report.html` structure (key fragments):**

```html
{% extends "base.html" %}
{% block content %}

<nav class="date-nav">
  <a href="{{ prev_href }}" class="nav-btn {{ 'disabled' if not prev_href }}">‹</a>
  <form class="date-picker">
    <select onchange="location.href='/reports/'+this.value">
      {% for d in all_dates %}
        <option value="{{ d }}" {{ 'selected' if d == current_date }}>{{ d }}</option>
      {% endfor %}
    </select>
  </form>
  <a href="{{ next_href }}" class="nav-btn {{ 'disabled' if not next_href }}">›</a>
</nav>

<header class="report-header">
  <h1>{{ report.date }}</h1>
  <p class="meta">
    Generated {{ report.generated_at | format_time }} ·
    {{ report.stats.items_generated }} items ·
    {{ report.stats.tweets_fetched }} tweets
    {% if report.stats.errors %}
      · <span class="err">{{ report.stats.errors | length }} fetch errors</span>
    {% endif %}
  </p>
</header>

<section class="news-items">
  {% for item in report.news_items | sort_by_importance %}
    <article class="item importance-{{ item.importance }}">
      <div class="item-header">
        <span class="importance-badge">{{ item.importance | upper }}</span>
        <h2>{{ item.headline }}</h2>
      </div>
      <p class="summary">{{ item.summary }}</p>
      <p class="reason">{{ item.importance_reason }}</p>
      <ul class="sources">
        {% for src in item.source_tweets %}
          <li><a href="{{ src.url }}" target="_blank" rel="noopener">@{{ src.handle }}</a></li>
        {% endfor %}
      </ul>
      <div class="thumbs"
           data-item-id="{{ item.id }}"
           data-report-date="{{ current_date }}">
        <button class="thumb-up {{ 'active' if feedback.get(item.id) == 1 }}">👍</button>
        <button class="thumb-down {{ 'active' if feedback.get(item.id) == -1 }}">👎</button>
      </div>
    </article>
  {% endfor %}
</section>

{% if report.suggested_accounts %}
<section class="suggested">
  <h2>Suggested accounts</h2>
  <div class="suggested-grid">
    {% for acc in report.suggested_accounts %}
      <a class="suggested-card"
         href="https://x.com/{{ acc.handle }}"
         target="_blank" rel="noopener">
        <div class="handle">@{{ acc.handle }}</div>
        <div class="reason">{{ acc.reason }}</div>
        <div class="cue">↗ open on X</div>
      </a>
    {% endfor %}
  </div>
</section>
{% endif %}

{% endblock %}
```

### 3.6 Jinja2 filters

Three custom filters, defined in `rendering.py`, registered in `app.py` via `Jinja2Templates.env.filters`:

- `format_time(iso_string) -> str` — takes a `generated_at` ISO-8601 string, returns `"08:03 (3 hours ago)"` using the report's `user_tz`.
- `sort_by_importance(items) -> list` — stable sort: `high` first, then `medium`, then `low`. Preserves within-group order (the model's own ranking).
- `groupby_month(dates) -> list[tuple[str, list[date]]]` — groups a sorted date list by `YYYY-MM` for the history page.

### 3.7 Client-side thumbs (`static/thumbs.js`)

```javascript
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".thumb-up, .thumb-down");
  if (!btn) return;
  const container = btn.closest(".thumbs");
  const wasActive = btn.classList.contains("active");
  const baseScore = btn.classList.contains("thumb-up") ? 1 : -1;
  const score = wasActive ? 0 : baseScore;
  const res = await fetch("/api/feedback/thumbs", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      report_date: container.dataset.reportDate,
      item_id: container.dataset.itemId,
      score: score,
    }),
  });
  if (res.ok) {
    container.querySelectorAll("button").forEach(b => b.classList.remove("active"));
    if (score !== 0) btn.classList.add("active");
  }
});
```

Vanilla JS, no framework, no build step, single event-delegated click handler. Toggle semantics: clicking an active thumb sends `score: 0` (clear); clicking a different thumb sends the new score.

---

## 4. Feedback storage and event schema

### 4.1 Event shape

One JSON object per line in `data/intelligence/feedback/YYYY-MM.jsonl`. Every event has a shared envelope; the `type` field discriminates payloads. Only `thumbs` exists in 6e.

```json
{"ts": "2026-04-15T10:30:00+08:00", "type": "thumbs", "report_date": "2026-04-15", "item_id": "n3", "score": 1}
```

**Envelope fields (present for every event type):**
- `ts` — ISO-8601 with timezone offset, stamped server-side at write time. Not trusted from the client.
- `type` — discriminator. Only `"thumbs"` in 6e. Future event types can be added without migration.
- `report_date` — `YYYY-MM-DD`. Which report the event is about.

**Thumbs-specific fields:**
- `item_id` — opaque local ID from the report (`n1`, `n2`, ...), matching the ID assigned by the summarizer per 6d §5.2.
- `score` — `1` (up), `-1` (down), or `0` (clear).

### 4.2 `FeedbackEvent` dataclass (`intelligence_web/feedback.py`)

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import os

ThumbsScore = Literal[-1, 0, 1]

@dataclass(frozen=True)
class FeedbackEvent:
    ts: datetime            # tz-aware
    type: Literal["thumbs"]
    report_date: str        # YYYY-MM-DD
    item_id: str
    score: ThumbsScore

    @classmethod
    def thumbs(
        cls, *, report_date: str, item_id: str, score: ThumbsScore, tz: ZoneInfo
    ) -> "FeedbackEvent":
        return cls(
            ts=datetime.now(tz=tz),
            type="thumbs",
            report_date=report_date,
            item_id=item_id,
            score=score,
        )

    def to_jsonl_line(self) -> str:
        return json.dumps({
            "ts": self.ts.isoformat(),
            "type": self.type,
            "report_date": self.report_date,
            "item_id": self.item_id,
            "score": self.score,
        }, ensure_ascii=False) + "\n"
```

### 4.3 Append path

```python
def append_thumbs(event: FeedbackEvent, feedback_dir: Path) -> None:
    feedback_dir.mkdir(parents=True, exist_ok=True)
    month = event.ts.strftime("%Y-%m")
    path = feedback_dir / f"{month}.jsonl"
    line = event.to_jsonl_line()
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
```

**Crash-safety reasoning.** Each event is a single short line (~120 bytes, well below `PIPE_BUF` of 4096). POSIX guarantees `O_APPEND` writes below `PIPE_BUF` are atomic — they either fully land at the end of the file or don't. `fsync` additionally guarantees durability before the handler returns 200 to the client. If the process crashes mid-POST, the client retries; either the event is on disk (retry produces a harmless duplicate; see §4.4) or it isn't (retry produces the canonical record).

**Single-writer assumption.** The webapp is the only process writing to `feedback/`. No multi-writer coordination is needed at 6e scale.

**Thumbs POST does not validate report existence.** The feedback endpoint is intentionally write-only — it does not check whether the `(report_date, item_id)` pair refers to a real report on disk. Reasoning: adding that check forces a disk read per click for a validation that would only catch impossible UI bugs (the thumbs buttons are server-rendered from real reports), and the ledger is tolerant of stale entries by design.

### 4.4 Read path: deriving current thumbs state

```python
def load_thumbs_state_for(
    report_date: str, feedback_dir: Path
) -> dict[str, int]:
    """
    Returns the current thumbs state for a given report date as {item_id: score}.
    Only includes items with a non-zero current score. Scans the current month's
    file plus the previous month's file (handles events stamped in month M+1
    for reports from month M).
    """
    state: dict[str, int] = {}
    for path in _relevant_month_files(report_date, feedback_dir):
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue   # silently skip corrupt lines; log a warning
                if evt.get("type") != "thumbs":
                    continue
                if evt.get("report_date") != report_date:
                    continue
                item_id = evt.get("item_id")
                score = evt.get("score")
                if not isinstance(item_id, str) or score not in (-1, 0, 1):
                    continue
                state[item_id] = score
    return {k: v for k, v in state.items() if v != 0}
```

**Latest-write-wins is correct.** Within a file, append order equals chronological order (since `ts` is stamped at write time and append preserves order). Across files we iterate oldest → newest. The final assignment to `state[item_id]` is the current state.

**Scanning current + previous month** handles the edge case where a user views a March report in April and clicks a thumb: the event's `ts` lands in `2026-04.jsonl` but the `report_date` is `2026-03-31`. Always checking both months covers this without complicating the rule.

**Corrupt-line handling.** Malformed JSON lines (unlikely given §4.3 atomicity but possible on very old filesystems) are skipped and logged as warnings, not raised — a single bad line should never crash the report page render.

### 4.5 What the webapp does NOT do with feedback

- No reading the log to influence generation. `intelligence/generate.py` has no reference to the feedback directory.
- No aggregation, analytics, or stats endpoints.
- No deletion or edit endpoints. Manual `rm` if ever needed.
- No memory writes. Feedback does not touch `store.py`, does not land in the messages table, does not affect any agent's long-term memory.

### 4.6 Storage growth

- Heavy day (12 items, every thumb clicked twice to toggle): ~3 KB/day.
- Monthly file: ~100 KB worst case.
- Annual total: ~1 MB across 12 files.

Never becomes a concern at this scale.

---

## 5. Intelligence agent changes

Four small touches on `agents/intelligence.py`, `intelligence/generate.py`, `llm/provider.py`, `llm/anthropic.py`, `prompts/intelligence.md`, and `prompts/intelligence.toml`. None reshape the agent's loop or storage.

### 5.1 New tool: `get_report_link`

Added to Intelligence's tool spec list alongside the existing four (`generate_daily_report`, `get_latest_report`, `get_report`, `list_reports`).

**Tool specification (Anthropic tool-use format):**

```python
GET_REPORT_LINK_SPEC = {
    "name": "get_report_link",
    "description": (
        "Return a stable URL to the webpage rendering of a daily report. "
        "Use this whenever the user asks to 'send me', 'share', 'open', or "
        "'give me a link to' a daily report — in any language. Do not "
        "paraphrase the returned URL; paste it verbatim into your reply so "
        "the user can tap it. Pass 'latest' to get the most recent report."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": (
                    "Either a YYYY-MM-DD date string or the literal string "
                    "'latest'. If 'latest', the tool picks the newest report "
                    "that exists on disk."
                ),
            },
        },
        "required": ["date"],
    },
}
```

**Dispatch implementation** in `Intelligence._dispatch_tool`:

```python
elif name == "get_report_link":
    raw = (input_.get("date") or "").strip()
    if raw == "latest":
        dates = list_report_dates(self._reports_dir)
        if not dates:
            return {
                "is_error": True,
                "content": "No reports exist yet. Generate one first.",
            }
        target = dates[0]
    else:
        try:
            target = date.fromisoformat(raw)
        except ValueError:
            return {
                "is_error": True,
                "content": f"Invalid date: {raw!r}. Expected YYYY-MM-DD or 'latest'.",
            }
        if not (self._reports_dir / f"{target.isoformat()}.json").exists():
            return {
                "is_error": True,
                "content": f"No report for {target.isoformat()}.",
            }
    base = self._public_base_url.rstrip("/")
    url = f"{base}/reports/{target.isoformat()}"
    return {
        "content": json.dumps({"url": url, "date": target.isoformat()}),
    }
```

**Design notes:**
- The tool never returns a URL for a report that doesn't exist — existence is validated before composition.
- `"latest"` is a first-class value rather than a separate tool, keeping the tool surface minimal.
- The return shape (`content` as a JSON string) matches the pattern used by existing `get_latest_report` and `get_report` tools.
- The canonical link shape is `/reports/{date}` even for latest (not bare `/`), so the agent-pasted URL is self-documenting — the user sees the date before tapping.
- `public_base_url` is trimmed of trailing slashes defensively to handle TOML-edit accidents.

### 5.2 Persona additions in `prompts/intelligence.md`

A new block added to the "Tools and behavior" section of 顾瑾's persona (Chinese, matches existing voice):

```markdown
## 分享每日情报的网页链接

当用户请求查看、发送、分享、或"把今天的情报给我"时（无论中英文表达），
你必须使用 `get_report_link` 工具获取网页链接，然后把链接**原样**贴到回复里。
不要缩短、改写、或把链接放在代码块里。用户会直接点击链接打开网页。

示例：
- 用户："把今天的日报发给我。"
  你调用 `get_report_link(date="latest")`，得到 `{"url": "...", "date": "2026-04-15"}`。
  回复："（指尖轻点）今日情报整理完毕 → https://intel.tailnet.ts.net/reports/2026-04-15"

- 用户："昨天有什么新的？"
  你先用 `get_report` 查看昨天的内容做出简短回答。如果用户追问细节或
  要求"完整版"、"链接"、"发给我"，再用 `get_report_link` 返回链接。

## 源推文链接

在文字回复里讨论具体新闻条目时，如果条目有 `source_tweets`，可以直接把
其中一条推文 URL 贴出来做引用。不要把所有推文都贴上——挑最权威的那条
（通常是第一方账号或事件原始发布者）。
```

**Reasoning for each rule:**
- "Paste verbatim, not in a code block" — LLMs sometimes shorten, paraphrase, or wrap URLs in markdown Telegram doesn't render as tappable. Explicit rule removes ambiguity.
- "Brief answer first, link on follow-up" — matches real usage: casual questions get direct answers with inline citations; explicit "give me the link" gets the link.
- Source-tweet citation rule — unlocks the "cite source links in Q&A" behavior the user asked for, while telling 顾瑾 to pick the most authoritative source rather than dumping all of them.

### 5.3 Composition-root wiring

`Intelligence.__init__` gains one parameter:

```python
def __init__(
    self,
    *,
    # ... existing params ...
    public_base_url: str,
) -> None:
    # ... existing init ...
    self._public_base_url = public_base_url
```

In `main.py`'s `_run`, the web config is loaded once from `intelligence.toml`'s `[web]` section and shared:

```python
intel_toml = load_toml(Path("prompts/intelligence.toml"))
web_config = WebConfig.from_toml_section(intel_toml["web"])

intelligence = Intelligence(
    # ... existing ...
    public_base_url=web_config.public_base_url,
)
# web_config also passed to _run_web later in the same function
```

Single source of truth. If `[web].public_base_url` is edited, both the agent's link output and the webapp's binding change in lockstep on restart.

### 5.4 Extended thinking on the summarizer call

**Step 1 — extend `LLMProvider.complete`.** The protocol (in `llm/provider.py`) gains an optional parameter:

```python
async def complete(
    self,
    *,
    system: str,
    messages: list[Msg],
    model: str,
    max_tokens: int,
    thinking_budget_tokens: int | None = None,
) -> str: ...
```

The Anthropic-backed concrete implementation maps `thinking_budget_tokens` to the SDK's `thinking={"type": "enabled", "budget_tokens": N}` parameter when non-None. Thinking blocks in the response are skipped over; callers see the same `str` return type as before. Existing callers (Secretary, Manager, Intelligence Q&A loop) pass no `thinking_budget_tokens` and see no behavior change.

**Step 2 — use it in `generate.py`.** The `generate_daily_report` function gains one parameter threaded through from `Intelligence`:

```python
result_text = await llm.complete(
    system=SUMMARIZER_SYSTEM_PROMPT,
    messages=[Msg(role="user", content=user_prompt)],
    model=summarizer_model,
    max_tokens=summarizer_max_tokens,
    thinking_budget_tokens=summarizer_thinking_budget,
)
```

**TOML changes (`prompts/intelligence.toml`):**

```toml
[llm.summarizer]
model = "claude-opus-4-6"
max_tokens = 32768                  # bumped from 16384; must be > thinking_budget
thinking_budget_tokens = 16384      # NEW

[web]                                # NEW section
public_base_url = "http://intel-server.tailnet-abc.ts.net:8080"
bind_host = "0.0.0.0"
bind_port = 8080
reports_dir = "data/intelligence/reports"
feedback_dir = "data/intelligence/feedback"
user_tz = "Asia/Shanghai"
```

`Intelligence` reads all three `[llm.summarizer]` fields and passes them through to `generate_daily_report`.

### 5.5 Cost impact

Extended thinking at `budget_tokens=16384` adds ~$0.24 per report (input-token rates on Opus). At the current on-demand-only generation cadence, monthly impact is small.

- 6d estimated monthly ceiling: ~$35.
- 6e additional thinking cost: ~$7/month at once-daily generation.
- **6e total monthly ceiling: ~$42.** Still in noise-floor territory for a personal project.

### 5.6 What does NOT change on the agent side

- The Q&A tool loop (`_agentic_loop` from `agents/_tool_loop.py`) is unchanged. Same eager latest-report injection, same tool dispatch. The only new fact is that it knows about one more tool.
- The `DailyReport` schema and `validate_report_dict` are unchanged. The webapp reads the exact same files the agent writes.
- No prompt changes to Secretary or Manager.
- Extended thinking is only enabled on the Opus summarizer call, not on Sonnet Q&A turns.

---

## 6. Process integration and lifecycle

### 6.1 Single-process model

The webapp runs inside the same Python process as the Telegram bot pollers, as one additional `asyncio.Task` inside `main.py`'s existing `TaskGroup`. The rationale:

- Single entry point (`python -m project0.main`) matches the current project shape.
- Shared shutdown event means `Ctrl-C` stops all subsystems in lockstep.
- Blast radius is naturally contained — the webapp only reads from and appends to disk.
- Moving to a separate-process model later is a trivial refactor (extract `_run_web` into its own `__main__`); the code is structured to support that split without rework.

### 6.2 The `_run_web` task (`main.py`)

```python
async def _run_web(
    *,
    web_config: WebConfig,
    shutdown_event: asyncio.Event,
) -> None:
    from project0.intelligence_web.app import create_app
    import uvicorn

    app = create_app(web_config)
    config = uvicorn.Config(
        app,
        host=web_config.bind_host,
        port=web_config.bind_port,
        log_level="info",
        access_log=True,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # main.py owns signals

    server_task = asyncio.create_task(server.serve())
    try:
        await shutdown_event.wait()
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=5.0)
        except asyncio.TimeoutError:
            server.force_exit = True
            await server_task
```

**Key lifecycle points:**
- Uvicorn's internal signal handlers are disabled (`install_signal_handlers = lambda: None`) so `main.py` remains the sole signal authority. Avoids the "Ctrl-C kills the web server but not the bots" footgun.
- Shutdown drains in-flight requests for up to 5 seconds, then force-exits. Plenty for the longest endpoint (report render, ~50ms).

### 6.3 Composition-root wiring in `_run`

```python
async def _run() -> None:
    load_dotenv()
    intel_toml = load_toml(Path("prompts/intelligence.toml"))

    web_config = WebConfig.from_toml_section(intel_toml["web"])

    # ... store, llm provider, etc ...

    intelligence = Intelligence(
        # ... existing params ...
        public_base_url=web_config.public_base_url,
    )

    shutdown_event = asyncio.Event()
    _install_signal_handlers(shutdown_event)

    async with asyncio.TaskGroup() as tg:
        for spec in AGENT_SPECS:
            tg.create_task(_run_bot(spec, shutdown_event=shutdown_event, ...))
        tg.create_task(_run_web(web_config=web_config, shutdown_event=shutdown_event))
```

If `main.py` does not already have a centralized signal handler, 6e adds one as part of this change. The Telegram bot tasks are extended (if needed) to await the same `shutdown_event`.

### 6.4 `WebConfig` dataclass (`intelligence_web/config.py`)

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

@dataclass(frozen=True)
class WebConfig:
    public_base_url: str
    bind_host: str
    bind_port: int
    reports_dir: Path
    feedback_dir: Path
    user_tz: ZoneInfo

    @classmethod
    def from_toml_section(cls, section: dict[str, Any]) -> "WebConfig":
        public_base_url = section["public_base_url"]
        if not public_base_url.startswith(("http://", "https://")):
            raise RuntimeError(
                f"[web].public_base_url must start with http:// or https://, "
                f"got {public_base_url!r}"
            )
        return cls(
            public_base_url=public_base_url,
            bind_host=section.get("bind_host", "0.0.0.0"),
            bind_port=int(section.get("bind_port", 8080)),
            reports_dir=Path(section.get("reports_dir", "data/intelligence/reports")),
            feedback_dir=Path(section.get("feedback_dir", "data/intelligence/feedback")),
            user_tz=ZoneInfo(section.get("user_tz", "Asia/Shanghai")),
        )
```

Strict validation on `public_base_url` because it's the one field that silently breaks link sharing when misconfigured. Bind host/port/dirs/tz get defaults for dev-friendliness.

### 6.5 Crash isolation and failure semantics

- **Handler exception** → FastAPI returns 500, logs the traceback, webapp continues, bots unaffected.
- **Route filesystem error** → same as above.
- **Uvicorn crashes on bind** (e.g., port in use) → `server_task` raises, propagates through the TaskGroup, cancels sibling tasks. Startup fails loudly. This is the correct behavior.
- **A Telegram bot task crashes** → TaskGroup cancels sibling tasks including the web task. Single-process all-or-nothing lifecycle. Restart the whole thing.

If the user ever wants partial-running states (bot restart without webapp downtime), that's the signal to split into separate processes. Don't preempt in 6e.

### 6.6 Startup and logging

- Uvicorn binds synchronously at the top of `server.serve()`. Bind failures happen immediately on startup, not later.
- Startup log now includes both "Telegram bot started: ..." lines and "Uvicorn running on http://0.0.0.0:8080" lines. Expected.
- Uvicorn's default logger is bridged into the project's logging setup via `log_level="info"` on the uvicorn config. No new log files, no new formats.

### 6.7 Development UX

- **Production path**: `python -m project0.main` runs bots + webapp together. Single process, single `Ctrl-C` stops everything.
- **Template/CSS iteration loop**: `scripts/dev_web.sh` runs:

  ```bash
  uv run uvicorn project0.intelligence_web.app:create_app --factory --reload --port 8081
  ```

  The webapp reads the real `data/intelligence/reports/` directory and live-reloads on template/CSS changes. Doesn't talk to the agent. Port 8081 avoids collision with the production 8080.

---

## 7. Testing strategy

Three layers mirroring 6d's pattern: pure unit → FastAPI TestClient integration → optional live smoke. Everything except the live smoke runs on every `pytest` invocation with no env vars, no network, no external services.

### 7.1 Shared test fixtures (`tests/intelligence_web/conftest.py`)

```python
@pytest.fixture
def tmp_reports_dir(tmp_path: Path) -> Path:
    d = tmp_path / "reports"
    d.mkdir()
    return d

@pytest.fixture
def tmp_feedback_dir(tmp_path: Path) -> Path:
    return tmp_path / "feedback"   # intentionally not created

@pytest.fixture
def web_config(tmp_reports_dir, tmp_feedback_dir) -> WebConfig:
    return WebConfig(
        public_base_url="http://test.local:8080",
        bind_host="127.0.0.1",
        bind_port=8080,
        reports_dir=tmp_reports_dir,
        feedback_dir=tmp_feedback_dir,
        user_tz=ZoneInfo("Asia/Shanghai"),
    )

@pytest.fixture
def client(web_config) -> TestClient:
    app = create_app(web_config)
    return TestClient(app)

@pytest.fixture
def sample_report() -> dict:
    # Full report dict conforming to the 6d schema, with items of varying
    # importance, source_tweets populated, and suggested_accounts present.
    ...

def _write_report(reports_dir: Path, report: dict) -> Path: ...
```

### 7.2 Layer 1 — pure unit tests (no FastAPI, no HTTP)

**`test_config.py`:**
- `test_valid_full_config`
- `test_valid_minimal_config` (defaults applied)
- `test_rejects_base_url_without_scheme`
- `test_rejects_base_url_with_ftp_scheme`
- `test_rejects_missing_public_base_url`

**`test_feedback_append.py`:**
- `test_writes_one_line_per_event`
- `test_uses_monthly_filename`
- `test_creates_feedback_dir_if_missing`
- `test_appends_to_existing_file`
- `test_line_is_valid_json`
- `test_unicode_preserved`
- `test_event_has_server_side_timestamp`

**`test_feedback_load_state.py`:**
- `test_empty_dir_returns_empty_state`
- `test_single_thumbs_up`
- `test_latest_write_wins`
- `test_score_zero_clears_entry`
- `test_filters_other_report_dates`
- `test_reads_current_and_previous_month`
- `test_skips_corrupt_lines`
- `test_skips_unknown_event_type`
- `test_skips_events_with_invalid_score`

**`test_rendering.py`:**
- `test_groups_items_by_importance`
- `test_stable_sort_within_importance`
- `test_formats_generated_at_with_tz_offset`
- `test_merges_feedback_state`
- `test_builds_prev_next_hrefs`
- `test_prev_href_none_for_oldest_date`
- `test_next_href_none_for_newest_date`
- `test_empty_suggested_accounts_omitted_from_context`

### 7.3 Layer 2 — FastAPI `TestClient` integration tests

**`test_routes_report.py`:**
- `test_root_returns_latest_report`
- `test_root_with_no_reports_returns_empty_html`
- `test_get_report_by_date`
- `test_get_nonexistent_date` (404)
- `test_get_bad_date_format` (400)
- `test_items_rendered_in_importance_order`
- `test_source_tweet_links_rendered`
- `test_suggested_accounts_rendered_with_x_links`
- `test_prev_next_hrefs_rendered_when_applicable`
- `test_date_dropdown_contains_all_dates`
- `test_feedback_state_reflected_in_rendered_buttons`

**`test_routes_history.py`:**
- `test_history_lists_all_dates_descending`
- `test_history_with_no_reports`
- `test_history_dates_link_to_report_pages`

**`test_routes_thumbs.py`:**
- `test_thumbs_up_writes_event_to_log`
- `test_thumbs_down_writes_event`
- `test_thumbs_zero_writes_clear_event`
- `test_thumbs_invalid_score_rejected` (422)
- `test_thumbs_missing_field_rejected` (422)
- `test_thumbs_unknown_report_date_still_accepted` (write-only ledger)
- `test_thumbs_event_has_server_timestamp`
- `test_subsequent_thumbs_updates_derived_state` (up → down → GET report → down active)

**`test_routes_errors.py`:**
- `test_healthz_returns_200_ok`
- `test_static_css_served`
- `test_unknown_route_returns_404`

**`test_app_factory.py`:**
- `test_create_app_returns_fastapi_instance`
- `test_create_app_with_nonexistent_reports_dir_still_constructs`
- `test_templates_mounted` (real packaged template dir, not CWD-relative)

### 7.4 Layer 3 — agent-side tests

**Extends `tests/agents/test_intelligence_tool_dispatch.py`:**
- `test_get_report_link_latest_picks_newest`
- `test_get_report_link_specific_date_exists`
- `test_get_report_link_date_not_exists` (`is_error: True`)
- `test_get_report_link_invalid_date_format` (`is_error: True`)
- `test_get_report_link_latest_with_no_reports` (`is_error: True`)
- `test_get_report_link_url_has_no_trailing_slash_issue` (base URL ending in `/` handled)

**Extends `tests/intelligence/test_generate_pipeline.py`:**
- `test_generate_passes_thinking_budget_to_llm`
- `test_generate_thinking_budget_respected_from_toml`

**New or extended `tests/llm/test_anthropic_thinking.py`:**
- `test_anthropic_complete_maps_thinking_param_to_sdk`
- `test_anthropic_complete_omits_thinking_when_none`
- `test_anthropic_complete_skips_thinking_blocks_in_response`

**Extends `tests/agents/test_intelligence_persona_load.py`:**
- `test_intelligence_persona_contains_link_sharing_rule` (reads `prompts/intelligence.md`, asserts `get_report_link` mention — regression guard)

### 7.5 Layer 4 — manual smoke script

**`scripts/smoke_web.sh`:**

1. Seeds a fake report into `data/intelligence/reports/2099-12-31.json`.
2. Starts uvicorn on a free port.
3. curls `/`, `/reports/2099-12-31`, `/history`.
4. POSTs a thumbs event, re-curls the report page, greps for `active` class.
5. Shuts down uvicorn.
6. Cleans up the fake report file and the feedback file.

Developer sanity check. Manual only. Does not run in CI. No network required.

**No automated Tailscale test** — Tailscale is a deployment concern, not code. Verify reachability from phone manually.

### 7.6 Coverage expectations

Not a strict percentage target, but implementation review should confirm:
- Every route has at least one success test and one error-path test.
- Every rendering branch (has/no suggested accounts, has/no feedback state, every importance level) has a test.
- Every `is_error: True` branch in `get_report_link` is covered.
- Feedback append and load paths are covered independently and together.

Order-of-magnitude estimate: **+60–80 tests**, total going from 256 → ~320.

### 7.7 Tests intentionally NOT written

- No end-to-end Telegram → agent → link flow test (matches 6d decision).
- No HTML snapshot / visual regression tests. Tests assert semantic facts only.
- No Jinja2 template syntax tests. Syntax errors surface via route tests.
- No concurrency / load tests. Single-user tool.
- No tests for uvicorn signal handlers or TaskGroup crash propagation (stdlib-covered).
- No CSS tests.

---

## 8. Dependencies, documentation, gitignore

### 8.1 New dependencies

Added to `pyproject.toml`:

```toml
dependencies = [
    # ... existing ...
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "jinja2>=3.1",
]
```

All pure-Python, no compiled components, no system deps. Install adds <5 seconds to a clean `uv sync`. No new dev deps.

### 8.2 Documentation updates

**`README.md`:** new "Intelligence webapp" section after the existing Intelligence agent section, explaining:
- How to reach the webpage from a phone via Tailscale.
- Where reports and feedback live on disk.
- The new `[web]` TOML fields and what each does.
- How to run `scripts/dev_web.sh` for template iteration.
- Security note: `0.0.0.0:8080` is only safe behind a firewall / Tailscale gate.

### 8.3 `.gitignore` updates

```
# Intelligence agent runtime data (6d)
data/intelligence/reports/

# Intelligence webapp feedback log (6e)
data/intelligence/feedback/
```

---

## 9. Backwards compatibility

Everything 6d shipped continues to work unchanged after 6e lands:

- Running `python -m project0.main` without using the webapp: Telegram bots still work exactly as in 6d. Visible differences: "Uvicorn running on..." in startup log, port 8080 bound.
- The `intelligence/reports/` file format is unchanged. Every existing report on disk renders correctly without migration.
- The existing four Intelligence tools are unchanged in shape and behavior.
- The summarizer's output JSON schema is unchanged. Extended thinking affects internal reasoning, not output shape.
- `LLMProvider.complete`'s existing callers (Secretary, Manager, Intelligence Q&A loop) pass no `thinking_budget_tokens` and observe no behavior change.
- `intelligence.toml` changes are purely additive: new `[web]` section, two new keys in `[llm.summarizer]`. Existing keys unchanged.

A `git checkout` to a pre-6e commit and back is a zero-friction round-trip aside from the new dep install.

---

## 10. Sub-project size and shape

6e is comparable to 6d in code volume though smaller in conceptual complexity:

- **6d** shipped the entire Intelligence agent infrastructure (Twitter source, watchlist, schema, generation pipeline, summarizer prompt, agent class, shared tool-loop extraction). 256 tests post-6d (up from 179 pre-6d).
- **6e** ships a webapp layer (routes, templates, rendering adapter, feedback I/O, config), one new agent tool, extended-thinking plumbing, and integration into `main.py`. Templates and tests dominate the line count. Adding +60–80 tests.

Expect the implementation plan to produce roughly **15–25 discrete steps**, similar to 6d's plan shape.
