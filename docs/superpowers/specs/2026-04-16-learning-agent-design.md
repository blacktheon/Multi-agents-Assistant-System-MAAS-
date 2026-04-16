# Sub-project — Learning Agent (Notion knowledge base curator + spaced repetition coach)

**Date:** 2026-04-16
**Status:** Design approved, ready for implementation plan
**Depends on:** Multi-agent skeleton (6a), shared tool-loop pattern (6c), memory hardening (user_facts pattern)

---

## 1. Context and scope

The Learning Agent (温书瑶) is the fourth agent in MAAS. She owns Layer D — the user's formal knowledge base — and manages it through Notion as the single source of truth, following the same pattern Manager uses with Google Calendar: the external application holds the real data, MAAS keeps only lightweight local metadata for coordination.

**Primary role:** Knowledge input processor and spaced-repetition review coach.

**Persona:** Warm older-sister (姐姐) figure. Three-character name 温书瑶. Calls the user 少爷. Playful, encouraging, and considerate — makes the user *want* to come back and review. Never nags; gently persuades.

### Core use cases (in order of frequency)

1. **Review coaching** (daily) — proactive reminders for due review items, answering "what's coming up for review?", marking items reviewed
2. **Input processing** (on demand) — user sends a link or text, agent summarizes + extracts key points, stores to Notion, schedules review
3. **Knowledge browsing** (rare) — listing entries by tag, fetching entry details

### In scope

- `NotionClient` async wrapper (create/update/get/archive pages, query by last_edited_time)
- `KnowledgeEntry` domain model
- `knowledge_index` SQLite table (lightweight mirror of Notion page metadata, soft-delete)
- `review_schedule` SQLite table (spaced repetition state per entry)
- `KnowledgeIndexStore` and `ReviewScheduleStore` access classes in `store.py`
- `LearningAgent` class with six tools: `process_link`, `process_text`, `list_upcoming_reviews`, `mark_reviewed`, `list_entries`, `get_entry`
- Two pulse entries: `notion_sync` (30s reconciliation) and `review_reminder` (30min review check)
- Persona file (`prompts/learning.md`, Chinese, five canonical sections)
- Config file (`prompts/learning.toml`, model/token/interval settings)
- `register_learning` in registry, composition wiring in `main.py`
- Own Telegram bot token (`TELEGRAM_BOT_TOKEN_LEARNING` in `AGENT_SPECS`)
- Full unit/integration test coverage

### Out of scope (explicitly deferred)

- SM-2 or adaptive spaced repetition (v1 uses fixed intervals `[1, 3, 7, 14, 30]` days)
- Automated ingestion from Intelligence reports (user can paste manually)
- Conversation extract processing (user can paste manually)
- Deduplication and contradiction detection across entries
- Concept maps or cross-entry relationship graphs
- Notion AI integration from MAAS side (user accesses Notion AI directly)
- Multiple Notion databases or cross-database operations
- Obsidian/NotebookLM adapters (design is Notion-specific for now)
- Writing to `user_facts` (Learning does not participate in the Secretary-owned fact system)
- Manager delegation to Learning (can be added later)

---

## 2. Architecture

### External service pattern

```
┌─────────────┐      Notion API       ┌──────────────────┐
│ NotionClient │ ◄──────────────────► │ Notion Database   │
│ (async)      │   create/update/     │ (source of truth) │
│              │   query/archive      │                   │
└──────┬───────┘                      └──────────────────┘
       │
       │ KnowledgeEntry
       ▼
┌──────────────┐    ┌──────────────────┐
│ LearningAgent│───►│ Local SQLite      │
│              │    │ knowledge_index   │ ◄─ lightweight mirror
│              │    │ review_schedule   │ ◄─ MAAS-only scheduling
└──────────────┘    └──────────────────┘
```

**Notion is the source of truth** for knowledge content. The user can freely browse, edit, delete, and reorganize entries in Notion from any device. MAAS reconciles every 30 seconds.

**Local SQLite** stores only:
- Index metadata (page IDs, titles, tags, timestamps) for fast lookups without API calls
- Review scheduling state (interval step, next review date) which is MAAS-internal

### Data flow

**Input flow (user → Notion):**
1. User sends link or text + optional notes/focus direction
2. Agent fetches URL content (for links) or takes text as-is
3. Agent calls LLM to summarize + extract key points (output capped at ~500-800 tokens)
4. Agent creates Notion page with structured properties + page body
5. Agent inserts into local `knowledge_index` + creates `review_schedule` row (first review = tomorrow)
6. Agent confirms to user with title, tags, and Notion link

**Reconciliation flow (Notion → local):**
1. Pulse fires every 30 seconds
2. Agent queries Notion: `last_edited_time > last_sync_timestamp`
3. For each changed page: upsert `knowledge_index` row
4. For deleted/archived pages: set `status = 'deleted'/'archived'`, set `review_schedule.is_active = 0`
5. For restored pages: set status back to `active`, set `review_schedule.is_active = 1`
6. Silent — no Telegram message

**Review flow (agent → user):**
1. Pulse fires every 30 minutes
2. Agent queries `review_schedule` for `next_review <= now AND is_active = 1`
3. If due items exist, sends Telegram message listing titles + Notion links
4. User replies "已复习 N" → agent calls `mark_reviewed`, advances interval
5. User ignores → items stay due, appear in next reminder

---

## 3. Notion database structure

A single Notion database. Each page is one knowledge entry.

### Properties

| Property | Notion Type | Purpose |
|----------|-------------|---------|
| Title | Title | Knowledge point or concept name |
| Source | URL | Original link (empty for text input) |
| Source Type | Select | `link` / `text` |
| Tags | Multi-select | Topic categories, populated by LLM |
| User Notes | Rich text | User's additional thoughts ("useful for project XXX") |
| Status | Select | `active` / `archived` |

**Page body:** Contains the summary + extracted key points in markdown. This is the content the user reads during review or browses on their phone.

### Setup

The Notion database must be created manually by the user and shared with the MAAS integration. The agent does not create databases — only pages within an existing database. The database ID is configured in `.env`.

---

## 4. NotionClient service

**File:** `src/project0/notion/client.py`

### Class signature

```python
class NotionClient:
    def __init__(
        self,
        token: str,
        database_id: str,
    ) -> None:
```

### Methods (all async)

| Method | Returns | Purpose |
|--------|---------|---------|
| `create_page(title, body_markdown, source_url, source_type, tags, user_notes)` | `KnowledgeEntry` | Create a new knowledge entry |
| `update_page(page_id, *, title, body_markdown, tags, user_notes, status)` | `KnowledgeEntry` | Update an existing entry |
| `get_page(page_id)` | `KnowledgeEntry` | Fetch properties + full page body |
| `archive_page(page_id)` | `None` | Set status to archived |
| `query_changed_since(since: datetime)` | `list[KnowledgeEntry]` | Reconciliation: properties only, no body |
| `query_all(limit)` | `list[KnowledgeEntry]` | Full index rebuild if needed |

### Domain model

```python
@dataclass(frozen=True)
class KnowledgeEntry:
    page_id: str
    title: str
    source_url: str | None
    source_type: str            # "link" | "text"
    tags: list[str]
    user_notes: str | None
    status: str                 # "active" | "archived" | "deleted"
    created_at: datetime
    last_edited: datetime
    body: str | None            # None when fetched via lightweight query
```

### Error handling

`NotionClientError` custom exception, same pattern as `GoogleCalendarError`. Wraps HTTP errors, rate limit errors, and malformed responses.

### SDK

Uses the official `notion-client` Python package, which is async-native. No `asyncio.to_thread` wrapping needed.

---

## 5. Local storage (SQLite)

Two new tables, managed through access classes in `store.py`.

### Table: `knowledge_index`

```sql
CREATE TABLE knowledge_index (
    notion_page_id  TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    source_url      TEXT,
    source_type     TEXT NOT NULL,
    tags            TEXT,                -- JSON array
    user_notes      TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    last_edited     TEXT NOT NULL,
    last_synced     TEXT NOT NULL
);
```

### Table: `review_schedule`

```sql
CREATE TABLE review_schedule (
    notion_page_id  TEXT PRIMARY KEY REFERENCES knowledge_index(notion_page_id),
    interval_step   INTEGER NOT NULL DEFAULT 0,
    next_review     TEXT NOT NULL,
    last_reviewed   TEXT,
    times_reviewed  INTEGER NOT NULL DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1
);
```

### Access classes

**`KnowledgeIndexStore`** — methods: `upsert(entry)`, `remove(page_id)`, `get(page_id)`, `list_active()`, `last_sync_timestamp()`, `mark_synced(page_id, ts)`

**`ReviewScheduleStore`** — methods: `create(page_id, first_review_date)`, `due_items(as_of_date)`, `mark_reviewed(page_id)` (advances interval_step through `[1, 3, 7, 14, 30]`, computes next_review), `remove(page_id)`, `set_active(page_id, is_active)`

### Soft-delete behavior

When reconciliation detects a Notion page was deleted or archived:
- `knowledge_index.status` → `'deleted'` or `'archived'`
- `review_schedule.is_active` → `0`

When a page reappears or is un-archived:
- `knowledge_index.status` → `'active'`
- `review_schedule.is_active` → `1`
- Review history (interval_step, times_reviewed) is preserved

---

## 6. Agent tools

### Input tools

**`process_link(url: str, user_notes: str | None)`**
1. Fetch URL content via `httpx` async GET + `readability-lxml` or `trafilatura` for article extraction (same approach Intelligence uses for web content). Falls back to raw HTML-to-text if extraction fails.
2. Call LLM with focused prompt: summarize + extract key points (output ~500-800 tokens). If user provided `user_notes` with a focus direction, the prompt includes it so the LLM emphasizes relevant parts.
3. LLM also generates a title and suggests tags
4. Create Notion page with properties + body
5. Insert into local index + create review schedule (first review = tomorrow)
6. Return confirmation with title, tags, Notion link

**`process_text(text: str, user_notes: str | None)`**
Same as `process_link` but takes raw text instead of fetching a URL. Source URL is empty. Source type is `text`.

### Review tools

**`list_upcoming_reviews(days_ahead: int = 7)`**
Query local `review_schedule` for items due within N days. Return list with titles, due dates, Notion links. Ordered by due date ascending.

**`mark_reviewed(page_id: str)`**
Advance `interval_step` by 1 (capped at index 4 = 30 days). Compute `next_review = today + intervals[new_step]`. Increment `times_reviewed`. Update `last_reviewed`.

### Browse tools

**`list_entries(tag: str | None = None, limit: int = 20)`**
Query local `knowledge_index` for active entries. Filter by tag if provided. Return titles, tags, Notion links.

**`get_entry(page_id: str)`**
Fetch full content from Notion (properties + page body). For when user wants details without leaving Telegram.

---

## 7. Pulse system

Two pulse entries in `prompts/learning.toml`:

### `notion_sync` — reconciliation

- **Interval:** 30 seconds (configurable via `sync_interval_seconds`)
- **Action:** Query Notion for `last_edited_time > last_sync_ts`, update local index
- **Output:** Always silent (returns `None`)
- **No LLM call** — pure API query + local DB update

### `review_reminder` — review notifications

- **Interval:** 30 minutes (configurable via `reminder_interval_seconds`)
- **Action:** Query `review_schedule` for due items (`next_review <= now AND is_active = 1`)
- **Output:** If due items exist, send Telegram message. Otherwise silent.

### Reminder message format

```
少爷～有 3 个知识点等你来复习呢：
1. Python GIL 与异步 I/O — 打开 (逾期 1 天)
2. 分布式共识算法 — 打开 (今天到期)
3. React Server Components — 打开 (今天到期)

回复「已复习 1」就好啦～
```

### Review reply handling

User replies "已复习 1" (or "已复习 1 2 3" for batch) → agent parses the numbers, calls `mark_reviewed` for each, confirms with next review date.

User ignores → items stay due, appear in next reminder cycle.

---

## 8. Persona and configuration

### Persona file: `prompts/learning.md`

Five sections matching routing reasons:

| Section header | Maps to |
|---------------|---------|
| `# 学习助手 — 角色设定` | Core identity |
| `# 模式：私聊` | `direct_dm` |
| `# 模式：群聊点名` | `mention` / `focus` |
| `# 模式：定时脉冲` | `pulse` |
| `# 模式：工具使用守则` | Tool usage guide |

**Personality traits:**
- Warm, considerate older sister (姐姐)
- Calls user 少爷
- Playfully encouraging about reviews — makes learning feel inviting
- Concise in knowledge processing output — respects the user's time
- Celebratory when user completes reviews, gently persuasive when items are overdue

### Config file: `prompts/learning.toml`

```toml
[learning]
model = "claude-sonnet-4-6"
max_tokens_reply = 2048
max_tool_iterations = 5
transcript_window = 10

[learning.notion]
sync_interval_seconds = 30

[learning.review]
reminder_interval_seconds = 1800
intervals_days = [1, 3, 7, 14, 30]

[learning.processing]
max_summary_tokens = 800
```

---

## 9. Module layout

```
src/project0/
    notion/                    # NEW — Notion service package
        __init__.py
        client.py              # NotionClient async wrapper
        model.py               # KnowledgeEntry dataclass + NotionClientError
    agents/
        learning.py            # NEW — LearningAgent class
        registry.py            # gains register_learning
    store.py                   # gains KnowledgeIndexStore, ReviewScheduleStore, new tables

prompts/
    learning.md                # NEW — Chinese persona, 5 sections
    learning.toml              # NEW — model, token, interval config

tests/
    test_notion_client.py      # NEW — unit tests for NotionClient
    test_learning_agent.py     # NEW — unit tests for LearningAgent
    test_knowledge_store.py    # NEW — unit tests for store classes
```

---

## 10. Composition and registration

In `main.py`:

1. Load `NotionClient` credentials from `.env` (`NOTION_TOKEN`, `NOTION_DATABASE_ID`)
2. Instantiate `NotionClient(token, database_id)`
3. Create `KnowledgeIndexStore` and `ReviewScheduleStore` from the shared SQLite connection
4. Load `LearningPersona` from `prompts/learning.md`
5. Load `LearningConfig` from `prompts/learning.toml`
6. Instantiate `LearningAgent` with all dependencies injected
7. Call `register_learning(agent.handle)` — registers in `AGENT_REGISTRY` + `PULSE_REGISTRY`
8. Add `TELEGRAM_BOT_TOKEN_LEARNING` to `AGENT_SPECS`
9. Spawn pulse loops for `notion_sync` and `review_reminder`

### Environment variables

```
NOTION_TOKEN=secret_...
NOTION_DATABASE_ID=...
TELEGRAM_BOT_TOKEN_LEARNING=...
```

---

## 11. Testing strategy

### Unit tests (no network, no Notion API)

- `NotionClient` with mocked HTTP responses — verify page creation, queries, error handling
- `KnowledgeIndexStore` and `ReviewScheduleStore` with in-memory SQLite — verify CRUD, soft-delete, interval advancement
- `LearningAgent` tool dispatch with fake `NotionClient` — verify each tool produces correct results
- Review interval logic — verify step advancement through `[1, 3, 7, 14, 30]` and capping at 30

### Integration tests

- Full agent handle cycle: input envelope → tool call → Notion write → local index update
- Reconciliation: simulate Notion changes → verify local index updates correctly
- Soft-delete round-trip: archive in Notion → verify local deactivation → restore → verify reactivation

### Optional live smoke test

- Gated on `NOTION_TOKEN` + `NOTION_DATABASE_ID` being set
- Creates a test page, verifies it exists, updates it, queries for changes, archives it
- Cleans up after itself
