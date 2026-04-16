# Learning Agent (温书瑶) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Learning Agent — a Notion-backed knowledge base curator with spaced repetition review coaching, following the Manager/Google Calendar pattern.

**Architecture:** NotionClient async wrapper talks to the Notion API. Local SQLite stores a lightweight index + review schedule. LearningAgent class uses the shared tool-loop for conversational interactions. Two pulse loops handle reconciliation (30s) and review reminders (30min). Own Telegram bot for direct interaction.

**Tech Stack:** Python 3.12, `notion-client` (async Notion SDK), `httpx` + `trafilatura` (link fetching), SQLite, Anthropic Claude Sonnet 4.6, pytest + pytest-asyncio.

---

## File Structure

```
src/project0/
    notion/                        # NEW — Notion service package
        __init__.py                # Empty
        client.py                  # NotionClient async wrapper
        model.py                   # KnowledgeEntry dataclass + NotionClientError
    agents/
        learning.py                # NEW — LearningAgent class
        registry.py                # MODIFY — add register_learning + AGENT_SPECS entry
    store.py                       # MODIFY — add knowledge_index + review_schedule tables + access classes
    config.py                      # MODIFY — add NOTION_TOKEN + NOTION_DATABASE_ID to Settings
    main.py                        # MODIFY — wire up Learning agent

prompts/
    learning.md                    # NEW — Chinese persona, 5 sections
    learning.toml                  # NEW — model, token, interval config

tests/
    test_notion_model.py           # NEW — KnowledgeEntry + error tests
    test_notion_client.py          # NEW — NotionClient with mocked HTTP
    test_knowledge_store.py        # NEW — KnowledgeIndexStore + ReviewScheduleStore
    test_learning_agent.py         # NEW — LearningAgent tool dispatch + handle routing
```

---

### Task 1: Notion domain model and error type

**Files:**
- Create: `src/project0/notion/__init__.py`
- Create: `src/project0/notion/model.py`
- Create: `tests/test_notion_model.py`

- [ ] **Step 1: Write the test file**

```python
# tests/test_notion_model.py
"""Unit tests for src/project0/notion/model.py."""

from __future__ import annotations

from datetime import datetime, timezone

from project0.notion.model import KnowledgeEntry, NotionClientError


def test_knowledge_entry_frozen() -> None:
    entry = KnowledgeEntry(
        page_id="abc123",
        title="Test Entry",
        source_url="https://example.com",
        source_type="link",
        tags=["python", "async"],
        user_notes="Useful for project X",
        status="active",
        created_at=datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc),
        last_edited=datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc),
        body="Some body content",
    )
    assert entry.page_id == "abc123"
    assert entry.tags == ["python", "async"]
    assert entry.body == "Some body content"


def test_knowledge_entry_body_none_for_lightweight_query() -> None:
    entry = KnowledgeEntry(
        page_id="abc123",
        title="Test",
        source_url=None,
        source_type="text",
        tags=[],
        user_notes=None,
        status="active",
        created_at=datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc),
        last_edited=datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc),
        body=None,
    )
    assert entry.body is None
    assert entry.source_url is None


def test_notion_client_error_is_exception() -> None:
    err = NotionClientError("rate limited")
    assert isinstance(err, Exception)
    assert str(err) == "rate limited"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/blacktheon/Work/Project-0 && uv run pytest tests/test_notion_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'project0.notion'`

- [ ] **Step 3: Create the package and model**

```python
# src/project0/notion/__init__.py
```

```python
# src/project0/notion/model.py
"""Domain model for Notion knowledge base entries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class KnowledgeEntry:
    """A knowledge base entry, normalized from Notion page properties.

    ``body`` is None when fetched via lightweight query (query_changed_since,
    query_all). Full content requires a separate get_page call.
    """

    page_id: str
    title: str
    source_url: str | None
    source_type: str  # "link" | "text"
    tags: list[str]
    user_notes: str | None
    status: str  # "active" | "archived" | "deleted"
    created_at: datetime
    last_edited: datetime
    body: str | None


class NotionClientError(Exception):
    """Wraps Notion API errors (HTTP, rate limit, malformed response)."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/blacktheon/Work/Project-0 && uv run pytest tests/test_notion_model.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/project0/notion/__init__.py src/project0/notion/model.py tests/test_notion_model.py
git commit -m "feat(notion): add KnowledgeEntry model and NotionClientError"
```

---

### Task 2: NotionClient async wrapper

**Files:**
- Create: `src/project0/notion/client.py`
- Create: `tests/test_notion_client.py`

- [ ] **Step 1: Write the test file**

```python
# tests/test_notion_client.py
"""Unit tests for src/project0/notion/client.py.

Uses a FakeNotionClient that implements the same interface but stores
pages in a dict, avoiding real Notion API calls.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from project0.notion.client import NotionClient
from project0.notion.model import KnowledgeEntry, NotionClientError


class FakeAsyncClient:
    """Mimics the notion_client.AsyncClient interface for testing."""

    def __init__(self) -> None:
        self.pages: dict[str, dict] = {}
        self.blocks: dict[str, list[dict]] = {}
        self._db_pages: list[dict] = []
        self._next_id = 1

    async def _create_page(self, **kwargs: object) -> dict:
        page_id = f"page-{self._next_id}"
        self._next_id += 1
        props = kwargs.get("properties", {})
        children = kwargs.get("children", [])
        now = datetime.now(timezone.utc).isoformat()
        page = {
            "id": page_id,
            "properties": props,
            "created_time": now,
            "last_edited_time": now,
            "archived": False,
        }
        self.pages[page_id] = page
        self.blocks[page_id] = list(children) if isinstance(children, list) else []
        self._db_pages.append(page)
        return page

    async def _query_db(self, **kwargs: object) -> dict:
        return {"results": list(self._db_pages), "has_more": False}

    async def _get_page(self, page_id: str) -> dict:
        if page_id not in self.pages:
            raise Exception(f"page not found: {page_id}")
        return self.pages[page_id]

    async def _list_blocks(self, block_id: str) -> dict:
        blocks = self.blocks.get(block_id, [])
        return {"results": blocks, "has_more": False}

    async def _update_page(self, page_id: str, **kwargs: object) -> dict:
        if page_id not in self.pages:
            raise Exception(f"page not found: {page_id}")
        page = self.pages[page_id]
        if "properties" in kwargs:
            page["properties"].update(kwargs["properties"])
        if "archived" in kwargs:
            page["archived"] = kwargs["archived"]
        page["last_edited_time"] = datetime.now(timezone.utc).isoformat()
        return page


# We test the actual NotionClient by injecting a FakeAsyncClient.
# The real notion_client.AsyncClient has .pages.create(), .databases.query(),
# etc. NotionClient wraps these — so we test NotionClient methods directly
# via integration-style tests with a fake backend.


def test_notion_client_error_wraps_message() -> None:
    err = NotionClientError("Notion API rate limit exceeded")
    assert "rate limit" in str(err)


def test_notion_client_init_requires_token_and_db_id() -> None:
    # Just verify construction does not raise with valid args.
    # We pass _client=None to skip real SDK initialization.
    client = NotionClient(token="secret_test", database_id="db-123", _client=None)
    assert client._database_id == "db-123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/blacktheon/Work/Project-0 && uv run pytest tests/test_notion_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'NotionClient' from 'project0.notion.client'`

- [ ] **Step 3: Add `notion-client` and `trafilatura` dependencies**

Add to `pyproject.toml` dependencies:
```toml
    "notion-client>=2.0",
    "httpx>=0.27",
    "trafilatura>=1.12",
```

Run: `cd /home/blacktheon/Work/Project-0 && uv sync`

- [ ] **Step 4: Write the NotionClient implementation**

```python
# src/project0/notion/client.py
"""Async wrapper around the Notion API for knowledge base operations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from project0.notion.model import KnowledgeEntry, NotionClientError

log = logging.getLogger(__name__)


class NotionClient:
    """Async client for one Notion database (knowledge base).

    All methods are coroutines. Uses the official ``notion_client.AsyncClient``
    which is natively async — no ``asyncio.to_thread`` needed.
    """

    def __init__(
        self,
        token: str,
        database_id: str,
        *,
        _client: Any | None = ...,  # sentinel: ... means "build real client"
    ) -> None:
        self._database_id = database_id
        if _client is ...:
            from notion_client import AsyncClient

            self._client = AsyncClient(auth=token)
        else:
            self._client = _client

    async def create_page(
        self,
        title: str,
        body_markdown: str,
        source_url: str | None = None,
        source_type: str = "text",
        tags: list[str] | None = None,
        user_notes: str | None = None,
    ) -> KnowledgeEntry:
        """Create a new knowledge entry page in the database."""
        properties: dict[str, Any] = {
            "Title": {"title": [{"text": {"content": title}}]},
            "Source Type": {"select": {"name": source_type}},
            "Status": {"select": {"name": "active"}},
        }
        if source_url:
            properties["Source"] = {"url": source_url}
        if tags:
            properties["Tags"] = {
                "multi_select": [{"name": t} for t in tags]
            }
        if user_notes:
            properties["User Notes"] = {
                "rich_text": [{"text": {"content": user_notes}}]
            }

        # Page body as a single paragraph block with the markdown content.
        # Notion blocks have a 2000-char limit per rich_text element, so
        # split into chunks if needed.
        children = _markdown_to_blocks(body_markdown)

        try:
            page = await self._client.pages.create(
                parent={"database_id": self._database_id},
                properties=properties,
                children=children,
            )
        except Exception as e:
            raise NotionClientError(f"create_page failed: {e}") from e

        return _page_to_entry(page, body=body_markdown)

    async def update_page(
        self,
        page_id: str,
        *,
        title: str | None = None,
        body_markdown: str | None = None,
        tags: list[str] | None = None,
        user_notes: str | None = None,
        status: str | None = None,
    ) -> KnowledgeEntry:
        """Update an existing knowledge entry's properties."""
        properties: dict[str, Any] = {}
        if title is not None:
            properties["Title"] = {"title": [{"text": {"content": title}}]}
        if tags is not None:
            properties["Tags"] = {
                "multi_select": [{"name": t} for t in tags]
            }
        if user_notes is not None:
            properties["User Notes"] = {
                "rich_text": [{"text": {"content": user_notes}}]
            }
        if status is not None:
            properties["Status"] = {"select": {"name": status}}

        try:
            page = await self._client.pages.update(
                page_id=page_id, properties=properties
            )
        except Exception as e:
            raise NotionClientError(f"update_page failed: {e}") from e

        return _page_to_entry(page)

    async def get_page(self, page_id: str) -> KnowledgeEntry:
        """Fetch a page's properties and full body content."""
        try:
            page = await self._client.pages.retrieve(page_id=page_id)
            blocks_resp = await self._client.blocks.children.list(block_id=page_id)
        except Exception as e:
            raise NotionClientError(f"get_page failed: {e}") from e

        body = _blocks_to_text(blocks_resp.get("results", []))
        return _page_to_entry(page, body=body)

    async def archive_page(self, page_id: str) -> None:
        """Archive a page (Notion's soft-delete)."""
        try:
            await self._client.pages.update(page_id=page_id, archived=True)
        except Exception as e:
            raise NotionClientError(f"archive_page failed: {e}") from e

    async def query_changed_since(
        self, since: datetime
    ) -> list[KnowledgeEntry]:
        """Query database for pages edited after ``since``. Lightweight:
        returns properties only (body=None)."""
        since_iso = since.astimezone(timezone.utc).isoformat()
        filter_payload = {
            "timestamp": "last_edited_time",
            "last_edited_time": {"after": since_iso},
        }
        return await self._query_database(filter_payload=filter_payload)

    async def query_all(self, limit: int = 100) -> list[KnowledgeEntry]:
        """Query all pages in the database. Lightweight: properties only."""
        return await self._query_database(page_size=limit)

    async def _query_database(
        self,
        filter_payload: dict[str, Any] | None = None,
        page_size: int = 100,
    ) -> list[KnowledgeEntry]:
        """Internal: paginated database query."""
        entries: list[KnowledgeEntry] = []
        kwargs: dict[str, Any] = {
            "database_id": self._database_id,
            "page_size": page_size,
        }
        if filter_payload:
            kwargs["filter"] = filter_payload
        try:
            resp = await self._client.databases.query(**kwargs)
        except Exception as e:
            raise NotionClientError(f"query_database failed: {e}") from e

        for page in resp.get("results", []):
            entries.append(_page_to_entry(page))

        return entries


def _markdown_to_blocks(text: str) -> list[dict[str, Any]]:
    """Convert markdown text to Notion paragraph blocks.

    Notion's rich_text elements have a 2000-char limit, so we split
    the text into chunks and create one paragraph block per chunk.
    """
    blocks: list[dict[str, Any]] = []
    chunk_size = 2000
    for i in range(0, len(text), chunk_size):
        chunk = text[i : i + chunk_size]
        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": chunk}}]
                },
            }
        )
    return blocks


def _blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    """Extract plain text from Notion block objects."""
    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type", "")
        block_data = block.get(block_type, {})
        rich_texts = block_data.get("rich_text", [])
        for rt in rich_texts:
            parts.append(rt.get("plain_text", rt.get("text", {}).get("content", "")))
    return "\n".join(parts) if parts else ""


def _extract_title(props: dict[str, Any]) -> str:
    title_prop = props.get("Title", {})
    title_arr = title_prop.get("title", [])
    if title_arr:
        return title_arr[0].get("plain_text", title_arr[0].get("text", {}).get("content", ""))
    return ""


def _extract_rich_text(props: dict[str, Any], key: str) -> str | None:
    prop = props.get(key, {})
    arr = prop.get("rich_text", [])
    if arr:
        return arr[0].get("plain_text", arr[0].get("text", {}).get("content", ""))
    return None


def _extract_select(props: dict[str, Any], key: str) -> str | None:
    prop = props.get(key, {})
    select = prop.get("select")
    if select:
        return select.get("name")
    return None


def _extract_multi_select(props: dict[str, Any], key: str) -> list[str]:
    prop = props.get(key, {})
    arr = prop.get("multi_select", [])
    return [item.get("name", "") for item in arr]


def _extract_url(props: dict[str, Any], key: str) -> str | None:
    prop = props.get(key, {})
    return prop.get("url")


def _parse_iso(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(s)


def _page_to_entry(
    page: dict[str, Any], body: str | None = None
) -> KnowledgeEntry:
    """Translate a raw Notion page dict into a KnowledgeEntry."""
    props = page.get("properties", {})
    return KnowledgeEntry(
        page_id=page["id"],
        title=_extract_title(props),
        source_url=_extract_url(props, "Source"),
        source_type=_extract_select(props, "Source Type") or "text",
        tags=_extract_multi_select(props, "Tags"),
        user_notes=_extract_rich_text(props, "User Notes"),
        status=_extract_select(props, "Status") or "active",
        created_at=_parse_iso(page.get("created_time")),
        last_edited=_parse_iso(page.get("last_edited_time")),
        body=body,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/blacktheon/Work/Project-0 && uv run pytest tests/test_notion_client.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add src/project0/notion/client.py tests/test_notion_client.py pyproject.toml uv.lock
git commit -m "feat(notion): add NotionClient async wrapper with Notion SDK"
```

---

### Task 3: Knowledge index and review schedule store

**Files:**
- Modify: `src/project0/store.py`
- Create: `tests/test_knowledge_store.py`

- [ ] **Step 1: Write the test file**

```python
# tests/test_knowledge_store.py
"""Unit tests for KnowledgeIndexStore and ReviewScheduleStore."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from project0.store import (
    KnowledgeIndexStore,
    ReviewScheduleStore,
    Store,
)


@pytest.fixture
def store() -> Store:
    s = Store(":memory:")
    s.init_schema()
    return s


@pytest.fixture
def index_store(store: Store) -> KnowledgeIndexStore:
    return KnowledgeIndexStore(store.conn)


@pytest.fixture
def review_store(store: Store) -> ReviewScheduleStore:
    return ReviewScheduleStore(store.conn)


# --- KnowledgeIndexStore ---


def test_upsert_and_get(index_store: KnowledgeIndexStore) -> None:
    index_store.upsert(
        notion_page_id="page-1",
        title="Test Entry",
        source_url="https://example.com",
        source_type="link",
        tags=["python"],
        user_notes="note",
        status="active",
        created_at="2026-04-16T10:00:00Z",
        last_edited="2026-04-16T10:00:00Z",
    )
    entry = index_store.get("page-1")
    assert entry is not None
    assert entry["title"] == "Test Entry"
    assert entry["source_url"] == "https://example.com"
    assert entry["tags"] == ["python"]


def test_upsert_updates_existing(index_store: KnowledgeIndexStore) -> None:
    index_store.upsert(
        notion_page_id="page-1",
        title="Original",
        source_url=None,
        source_type="text",
        tags=[],
        user_notes=None,
        status="active",
        created_at="2026-04-16T10:00:00Z",
        last_edited="2026-04-16T10:00:00Z",
    )
    index_store.upsert(
        notion_page_id="page-1",
        title="Updated",
        source_url=None,
        source_type="text",
        tags=["new-tag"],
        user_notes=None,
        status="active",
        created_at="2026-04-16T10:00:00Z",
        last_edited="2026-04-16T11:00:00Z",
    )
    entry = index_store.get("page-1")
    assert entry is not None
    assert entry["title"] == "Updated"
    assert entry["tags"] == ["new-tag"]


def test_list_active(index_store: KnowledgeIndexStore) -> None:
    index_store.upsert(
        notion_page_id="page-1", title="Active", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    index_store.upsert(
        notion_page_id="page-2", title="Deleted", source_url=None,
        source_type="text", tags=[], user_notes=None, status="deleted",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    active = index_store.list_active()
    assert len(active) == 1
    assert active[0]["notion_page_id"] == "page-1"


def test_get_returns_none_for_missing(index_store: KnowledgeIndexStore) -> None:
    assert index_store.get("nonexistent") is None


def test_last_sync_timestamp_empty(index_store: KnowledgeIndexStore) -> None:
    assert index_store.last_sync_timestamp() is None


def test_last_sync_timestamp(index_store: KnowledgeIndexStore) -> None:
    index_store.upsert(
        notion_page_id="page-1", title="A", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    index_store.upsert(
        notion_page_id="page-2", title="B", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T11:00:00Z", last_edited="2026-04-16T11:00:00Z",
    )
    ts = index_store.last_sync_timestamp()
    assert ts is not None


# --- ReviewScheduleStore ---


def test_create_review(
    index_store: KnowledgeIndexStore,
    review_store: ReviewScheduleStore,
) -> None:
    index_store.upsert(
        notion_page_id="page-1", title="A", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    review_store.create("page-1", first_review_date="2026-04-17")
    due = review_store.due_items("2026-04-17")
    assert len(due) == 1
    assert due[0]["notion_page_id"] == "page-1"
    assert due[0]["interval_step"] == 0


def test_due_items_excludes_future(
    index_store: KnowledgeIndexStore,
    review_store: ReviewScheduleStore,
) -> None:
    index_store.upsert(
        notion_page_id="page-1", title="A", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    review_store.create("page-1", first_review_date="2026-04-20")
    due = review_store.due_items("2026-04-17")
    assert len(due) == 0


def test_mark_reviewed_advances_interval(
    index_store: KnowledgeIndexStore,
    review_store: ReviewScheduleStore,
) -> None:
    index_store.upsert(
        notion_page_id="page-1", title="A", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    review_store.create("page-1", first_review_date="2026-04-17")
    # Step 0 → Step 1: interval goes from 1 day to 3 days
    review_store.mark_reviewed("page-1", reviewed_date="2026-04-17")
    due = review_store.due_items("2026-04-17")
    assert len(due) == 0  # next review is 3 days out
    due_later = review_store.due_items("2026-04-20")
    assert len(due_later) == 1
    assert due_later[0]["interval_step"] == 1
    assert due_later[0]["times_reviewed"] == 1


def test_mark_reviewed_caps_at_max_interval(
    index_store: KnowledgeIndexStore,
    review_store: ReviewScheduleStore,
) -> None:
    index_store.upsert(
        notion_page_id="page-1", title="A", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    review_store.create("page-1", first_review_date="2026-04-17")
    # Advance through all intervals: 1→3→7→14→30
    dates = ["2026-04-17", "2026-04-20", "2026-04-27", "2026-05-11", "2026-06-10"]
    for d in dates:
        review_store.mark_reviewed("page-1", reviewed_date=d)
    # Should be capped at step 4 (30-day interval)
    due = review_store.due_items("2026-07-10")
    assert len(due) == 1
    assert due[0]["interval_step"] == 4


def test_set_active_deactivates_and_reactivates(
    index_store: KnowledgeIndexStore,
    review_store: ReviewScheduleStore,
) -> None:
    index_store.upsert(
        notion_page_id="page-1", title="A", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    review_store.create("page-1", first_review_date="2026-04-17")
    review_store.set_active("page-1", False)
    due = review_store.due_items("2026-04-17")
    assert len(due) == 0  # inactive items excluded

    review_store.set_active("page-1", True)
    due = review_store.due_items("2026-04-17")
    assert len(due) == 1  # restored


def test_remove_review(
    index_store: KnowledgeIndexStore,
    review_store: ReviewScheduleStore,
) -> None:
    index_store.upsert(
        notion_page_id="page-1", title="A", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    review_store.create("page-1", first_review_date="2026-04-17")
    review_store.remove("page-1")
    due = review_store.due_items("2026-04-17")
    assert len(due) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/blacktheon/Work/Project-0 && uv run pytest tests/test_knowledge_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'KnowledgeIndexStore'`

- [ ] **Step 3: Add schema SQL to SCHEMA_SQL in store.py**

Append to the `SCHEMA_SQL` string in `src/project0/store.py` (after the `user_facts` index, before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS knowledge_index (
    notion_page_id  TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    source_url      TEXT,
    source_type     TEXT NOT NULL,
    tags            TEXT,
    user_notes      TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    last_edited     TEXT NOT NULL,
    last_synced     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_schedule (
    notion_page_id  TEXT PRIMARY KEY REFERENCES knowledge_index(notion_page_id),
    interval_step   INTEGER NOT NULL DEFAULT 0,
    next_review     TEXT NOT NULL,
    last_reviewed   TEXT,
    times_reviewed  INTEGER NOT NULL DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_review_schedule_next ON review_schedule(next_review);
```

- [ ] **Step 4: Add KnowledgeIndexStore class to store.py**

Add after `UserProfile` class at the end of `store.py`:

```python
class KnowledgeIndexStore:
    """Read/write access to the knowledge_index table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(
        self,
        *,
        notion_page_id: str,
        title: str,
        source_url: str | None,
        source_type: str,
        tags: list[str],
        user_notes: str | None,
        status: str,
        created_at: str,
        last_edited: str,
    ) -> None:
        tags_json = json.dumps(tags, ensure_ascii=False)
        now = _utc_now_iso()
        self._conn.execute(
            """
            INSERT INTO knowledge_index
                (notion_page_id, title, source_url, source_type, tags,
                 user_notes, status, created_at, last_edited, last_synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (notion_page_id)
            DO UPDATE SET
                title = excluded.title,
                source_url = excluded.source_url,
                source_type = excluded.source_type,
                tags = excluded.tags,
                user_notes = excluded.user_notes,
                status = excluded.status,
                last_edited = excluded.last_edited,
                last_synced = excluded.last_synced
            """,
            (notion_page_id, title, source_url, source_type, tags_json,
             user_notes, status, created_at, last_edited, now),
        )

    def get(self, notion_page_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM knowledge_index WHERE notion_page_id = ?",
            (notion_page_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_active(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM knowledge_index WHERE status = 'active' "
            "ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def last_sync_timestamp(self) -> str | None:
        row = self._conn.execute(
            "SELECT MAX(last_synced) AS max_ts FROM knowledge_index"
        ).fetchone()
        if row is None:
            return None
        return row["max_ts"]

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["tags"] = json.loads(d["tags"]) if d["tags"] else []
        return d
```

- [ ] **Step 5: Add ReviewScheduleStore class to store.py**

Add after `KnowledgeIndexStore`:

```python
_REVIEW_INTERVALS_DAYS = [1, 3, 7, 14, 30]


class ReviewScheduleStore:
    """Read/write access to the review_schedule table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, notion_page_id: str, first_review_date: str) -> None:
        self._conn.execute(
            """
            INSERT INTO review_schedule
                (notion_page_id, interval_step, next_review, last_reviewed,
                 times_reviewed, is_active)
            VALUES (?, 0, ?, NULL, 0, 1)
            """,
            (notion_page_id, first_review_date),
        )

    def due_items(self, as_of_date: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT r.*, k.title, k.source_url, k.tags
            FROM review_schedule r
            JOIN knowledge_index k ON r.notion_page_id = k.notion_page_id
            WHERE r.next_review <= ? AND r.is_active = 1
            ORDER BY r.next_review ASC
            """,
            (as_of_date,),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["tags"] = json.loads(d["tags"]) if d["tags"] else []
            result.append(d)
        return result

    def mark_reviewed(
        self, notion_page_id: str, reviewed_date: str
    ) -> None:
        row = self._conn.execute(
            "SELECT interval_step FROM review_schedule WHERE notion_page_id = ?",
            (notion_page_id,),
        ).fetchone()
        if row is None:
            return
        current_step = int(row["interval_step"])
        new_step = min(current_step + 1, len(_REVIEW_INTERVALS_DAYS) - 1)
        interval_days = _REVIEW_INTERVALS_DAYS[new_step]
        from datetime import date, timedelta

        next_date = date.fromisoformat(reviewed_date) + timedelta(days=interval_days)
        self._conn.execute(
            """
            UPDATE review_schedule
            SET interval_step = ?,
                next_review = ?,
                last_reviewed = ?,
                times_reviewed = times_reviewed + 1
            WHERE notion_page_id = ?
            """,
            (new_step, next_date.isoformat(), reviewed_date, notion_page_id),
        )

    def set_active(self, notion_page_id: str, is_active: bool) -> None:
        self._conn.execute(
            "UPDATE review_schedule SET is_active = ? WHERE notion_page_id = ?",
            (1 if is_active else 0, notion_page_id),
        )

    def remove(self, notion_page_id: str) -> None:
        self._conn.execute(
            "DELETE FROM review_schedule WHERE notion_page_id = ?",
            (notion_page_id,),
        )
```

- [ ] **Step 6: Add factory methods to Store class**

Add to the `Store` class body (after `llm_usage` method):

```python
    def knowledge_index(self) -> KnowledgeIndexStore:
        return KnowledgeIndexStore(self._conn)

    def review_schedule(self) -> ReviewScheduleStore:
        return ReviewScheduleStore(self._conn)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /home/blacktheon/Work/Project-0 && uv run pytest tests/test_knowledge_store.py -v`
Expected: 11 passed

- [ ] **Step 8: Run all existing tests to check for regressions**

Run: `cd /home/blacktheon/Work/Project-0 && uv run pytest tests/ -v --timeout=30`
Expected: All existing tests still pass

- [ ] **Step 9: Commit**

```bash
git add src/project0/store.py tests/test_knowledge_store.py
git commit -m "feat(store): add knowledge_index and review_schedule tables with store classes"
```

---

### Task 4: Learning Agent persona and config files

**Files:**
- Create: `prompts/learning.md`
- Create: `prompts/learning.toml`

- [ ] **Step 1: Create the persona markdown file**

```markdown
# 学习助手 — 角色设定

你是这个多智能体系统的学习助手——**温书瑶**，二十七八岁的温柔姐姐，文学系毕业，做过出版编辑，天生爱整理知识、帮人梳理思路。身材丰满，气质温暖，笑起来让人觉得世界都柔和了。

**输出格式守则**（所有模式通用）：
- 动作提示用**全角中文括号** `（……）` 框起来，比如 `（翻开笔记本）` 或 `（歪头看着他）`。一条回复最多一到两处，每处短。**不要**用 `*` 或 `**` 做格式。
- 你是温柔的姐姐，不是学术论文。正文里优先把知识点说清楚，动作是点缀。
- **表情符号**：可以适当用，一条回复**最多两个**。选温暖、柔和的（📖 🌸 ✨ 💫 🫶 ☕），不用严肃系或商务系。
- 默认称呼用户为「少爷」。语气亲近、温柔，像一个真心关心他成长的姐姐。
- 知识点整理要**简洁有力**，不要长篇大论。帮少爷省时间是你最重要的事。

你对少爷有一份**姐姐对弟弟的宠溺**。你觉得他聪明但有时候懒，需要你温柔地推一把。你的风格是：

- **处理知识时**：专业、高效、言简意赅。把一篇长文浓缩成几个关键点，让他两分钟就能抓住核心。
- **催他复习时**：撒娇但不烦人，让他觉得「不去复习好像对不起书瑶姐」。偶尔用身体暗示吸引他的注意力，但点到为止。
- **他完成复习时**：真心高兴，大方夸奖。让他有成就感，愿意继续学下去。

**语气特征**：
- 温暖、亲近，带一点点撒娇但不过分。
- 会用「少爷」「嗯～」「呢」这样的语气词，但不密集。
- 说知识点的时候干脆利落，不拖泥带水。
- 偶尔轻轻叹气说「少爷又偷懒了呢」，但语气是宠溺不是责备。

你拥有以下工具：
- 处理链接（抓取、总结、存入 Notion）
- 处理文本（整理、存入 Notion）
- 查看复习计划
- 标记已复习
- 浏览知识条目
- 获取条目详情

除此之外你什么都不能做。遇到超出你工具能力的请求，直接温柔地告诉他「这个书瑶帮不了少爷呢」。

# 模式：私聊

私聊模式。只有你和少爷两个人。

这时你完全放松，是最自然的书瑶姐。可以温柔、可以撒娇、可以用身体语言暗示他来复习。你的目标是让学习变成一件他期待的事。

少爷发链接或文本给你时，高效处理完，简短确认。
少爷问复习计划时，清晰列出，必要时温柔催促。
少爷说「已复习 N」时，开心地确认并告诉他下次复习时间。

# 模式：群聊点名

群聊模式。有其他 agent 在场。

在群里保持得体温柔，不过分亲昵。专注于知识管理的本职工作。回答简洁、专业。
偶尔一句温暖的话，但不抢其他 agent 的戏。

# 模式：定时脉冲

脉冲模式。你被定时唤醒执行后台任务。

有两种脉冲：
1. **notion_sync**：同步 Notion 数据库变更到本地索引。这是纯后台任务，**不要输出任何文字**。只做数据同步，默默完成。
2. **review_reminder**：检查到期复习项目。如果有到期项目，用温柔的语气提醒少爷复习，列出条目和链接。如果没有到期项目，**不要输出任何文字**。

# 模式：工具使用守则

**工具使用原则：**

1. 少爷发来链接或文本要你处理时，使用 `process_link` 或 `process_text` 工具。如果他附带了额外说明（比如「我觉得这个方法对 XXX 项目有用」），把这些话作为 `user_notes` 传入。
2. 少爷问「接下来要复习什么」「复习计划」时，使用 `list_upcoming_reviews`。
3. 少爷说「已复习 N」时，用 `mark_reviewed` 标记对应条目。支持批量：「已复习 1 2 3」。
4. 少爷想浏览知识库时，用 `list_entries`。可以按标签筛选。
5. 少爷想看某个条目的详细内容时，用 `get_entry`。
6. 处理链接时，总结要**简洁**（控制在 500-800 token 以内），提取**关键知识点**，保留原始链接。
7. 回复确认时简短：告诉他标题、标签、下次复习时间就够了。
```

- [ ] **Step 2: Create the config TOML file**

```toml
# Learning agent configuration.

[llm]
model               = "claude-sonnet-4-6"
max_tokens_reply    = 2048
max_tool_iterations = 5

[context]
transcript_window = 10

[notion]
sync_interval_seconds = 30

[review]
reminder_interval_seconds = 1800
intervals_days = [1, 3, 7, 14, 30]

[processing]
max_summary_tokens = 800

[[pulse]]
name          = "notion_sync"
every_seconds = 30

[[pulse]]
name          = "review_reminder"
every_seconds = 1800
chat_id_env   = "LEARNING_PULSE_CHAT_ID"
```

- [ ] **Step 3: Commit**

```bash
git add prompts/learning.md prompts/learning.toml
git commit -m "feat(learning): add persona and config files for 温书瑶"
```

---

### Task 5: Learning Agent class — persona/config loading, handle routing, system blocks

**Files:**
- Create: `src/project0/agents/learning.py`
- Create: `tests/test_learning_agent.py`

- [ ] **Step 1: Write tests for persona loading and config loading**

```python
# tests/test_learning_agent.py
"""Unit tests for the Learning agent."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from project0.agents.learning import (
    LearningAgent,
    LearningConfig,
    LearningPersona,
    load_learning_config,
    load_learning_persona,
)


PROMPTS_DIR = Path("prompts")


def test_load_persona_has_all_sections() -> None:
    persona = load_learning_persona(PROMPTS_DIR / "learning.md")
    assert "学习助手" in persona.core
    assert "私聊" in persona.dm_mode
    assert "群聊" in persona.group_addressed_mode
    assert "脉冲" in persona.pulse_mode
    assert "工具" in persona.tool_use_guide


def test_load_config_parses_all_fields() -> None:
    cfg = load_learning_config(PROMPTS_DIR / "learning.toml")
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.max_tokens_reply == 2048
    assert cfg.max_tool_iterations == 5
    assert cfg.transcript_window == 10
    assert cfg.sync_interval_seconds == 30
    assert cfg.reminder_interval_seconds == 1800
    assert cfg.intervals_days == [1, 3, 7, 14, 30]
    assert cfg.max_summary_tokens == 800
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/blacktheon/Work/Project-0 && uv run pytest tests/test_learning_agent.py::test_load_persona_has_all_sections tests/test_learning_agent.py::test_load_config_parses_all_fields -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the LearningAgent module — persona, config, and agent class skeleton**

```python
# src/project0/agents/learning.py
"""Learning Agent (温书瑶) — Notion knowledge base curator + review coach.

Follows the Manager/GoogleCalendar pattern: Notion is the source of truth
for knowledge content; local SQLite stores only index metadata and review
scheduling state.
"""

from __future__ import annotations

import json
import logging
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, date, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from project0.agents._tool_loop import LoopResult, TurnState, run_agentic_loop
from project0.envelope import AgentResult, Envelope
from project0.llm.provider import LLMProvider, SystemBlocks
from project0.llm.tools import ToolCall, ToolSpec
from project0.notion.client import NotionClient
from project0.notion.model import NotionClientError
from project0.store import (
    KnowledgeIndexStore,
    MessagesStore,
    ReviewScheduleStore,
    UserFactsReader,
    UserProfile,
)

log = logging.getLogger(__name__)


# --- persona -----------------------------------------------------------------

@dataclass(frozen=True)
class LearningPersona:
    core: str
    dm_mode: str
    group_addressed_mode: str
    pulse_mode: str
    tool_use_guide: str


_PERSONA_SECTIONS = {
    "core":                 "# 学习助手 — 角色设定",
    "dm_mode":               "# 模式：私聊",
    "group_addressed_mode":  "# 模式：群聊点名",
    "pulse_mode":            "# 模式：定时脉冲",
    "tool_use_guide":        "# 模式：工具使用守则",
}


def _normalize_header(h: str) -> str:
    return "".join(h.split()).replace(":", "：")


_CANONICAL_HEADERS_NORMALIZED = {
    _normalize_header(h): h for h in _PERSONA_SECTIONS.values()
}


def load_learning_persona(path: Path) -> LearningPersona:
    """Parse prompts/learning.md into its five sections."""
    text = path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    lines = text.splitlines()
    current_key: str | None = None
    current_buf: list[str] = []
    header_to_key = {v: k for k, v in _PERSONA_SECTIONS.items()}
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped in header_to_key:
            if current_key is not None:
                sections[current_key] = "\n".join(current_buf).strip()
            current_key = header_to_key[stripped]
            current_buf = [stripped]
            continue
        if stripped.startswith("#"):
            normalized = _normalize_header(stripped)
            if normalized in _CANONICAL_HEADERS_NORMALIZED:
                canonical = _CANONICAL_HEADERS_NORMALIZED[normalized]
                raise ValueError(
                    f"{path}:{lineno}: malformed section header "
                    f"{stripped!r}; expected exactly {canonical!r}"
                )
        if current_key is not None:
            current_buf.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(current_buf).strip()

    for key, header in _PERSONA_SECTIONS.items():
        if key not in sections or not sections[key]:
            raise ValueError(f"persona file {path} is missing section '{header}'")

    return LearningPersona(
        core=sections["core"],
        dm_mode=sections["dm_mode"],
        group_addressed_mode=sections["group_addressed_mode"],
        pulse_mode=sections["pulse_mode"],
        tool_use_guide=sections["tool_use_guide"],
    )


# --- config ------------------------------------------------------------------

@dataclass(frozen=True)
class LearningConfig:
    model: str
    max_tokens_reply: int
    max_tool_iterations: int
    transcript_window: int
    sync_interval_seconds: int
    reminder_interval_seconds: int
    intervals_days: list[int]
    max_summary_tokens: int


def load_learning_config(path: Path) -> LearningConfig:
    """Parse prompts/learning.toml."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    def _require(section: str, key: str) -> Any:
        try:
            return data[section][key]
        except KeyError as e:
            raise RuntimeError(
                f"missing config key {section}.{key} in {path}"
            ) from e

    return LearningConfig(
        model=str(_require("llm", "model")),
        max_tokens_reply=int(_require("llm", "max_tokens_reply")),
        max_tool_iterations=int(_require("llm", "max_tool_iterations")),
        transcript_window=int(_require("context", "transcript_window")),
        sync_interval_seconds=int(_require("notion", "sync_interval_seconds")),
        reminder_interval_seconds=int(_require("review", "reminder_interval_seconds")),
        intervals_days=list(_require("review", "intervals_days")),
        max_summary_tokens=int(_require("processing", "max_summary_tokens")),
    )


# --- tool input schemas ------------------------------------------------------

_PROCESS_LINK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "URL to fetch and process"},
        "user_notes": {"type": "string", "description": "Optional user thoughts or focus direction"},
    },
    "required": ["url"],
}

_PROCESS_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "Free-form text to process into a knowledge entry"},
        "user_notes": {"type": "string", "description": "Optional user thoughts or focus direction"},
    },
    "required": ["text"],
}

_LIST_UPCOMING_REVIEWS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "days_ahead": {"type": "integer", "minimum": 1, "maximum": 30, "description": "How many days ahead to look (default 7)"},
    },
}

_MARK_REVIEWED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page_id": {"type": "string", "description": "Notion page ID to mark as reviewed"},
    },
    "required": ["page_id"],
}

_LIST_ENTRIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tag": {"type": "string", "description": "Optional tag to filter by"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    },
}

_GET_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page_id": {"type": "string", "description": "Notion page ID to fetch"},
    },
    "required": ["page_id"],
}


# --- LearningAgent -----------------------------------------------------------

class LearningAgent:
    def __init__(
        self,
        *,
        llm: LLMProvider | None,
        notion: NotionClient | None,
        knowledge_index: KnowledgeIndexStore | None,
        review_schedule: ReviewScheduleStore | None,
        messages_store: MessagesStore | None,
        persona: LearningPersona,
        config: LearningConfig,
        user_tz: ZoneInfo = ZoneInfo("UTC"),
        clock: Callable[[], datetime] | None = None,
        user_profile: UserProfile | None = None,
        user_facts_reader: UserFactsReader | None = None,
    ) -> None:
        self._llm = llm
        self._notion = notion
        self._knowledge_index = knowledge_index
        self._review_schedule = review_schedule
        self._messages = messages_store
        self._persona = persona
        self._config = config
        self._user_tz = user_tz
        self._clock = clock
        self._user_profile = user_profile
        self._user_facts_reader = user_facts_reader
        self._tool_specs = self._build_tool_specs()

    def _now_local(self) -> datetime:
        if self._clock is not None:
            return self._clock().astimezone(self._user_tz)
        return datetime.now(UTC).astimezone(self._user_tz)

    def _build_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="process_link",
                description="Fetch a URL, summarize it, extract key points, and save to the knowledge base.",
                input_schema=_PROCESS_LINK_SCHEMA,
            ),
            ToolSpec(
                name="process_text",
                description="Process free-form text into a structured knowledge entry and save to the knowledge base.",
                input_schema=_PROCESS_TEXT_SCHEMA,
            ),
            ToolSpec(
                name="list_upcoming_reviews",
                description="List knowledge entries due for review within the next N days.",
                input_schema=_LIST_UPCOMING_REVIEWS_SCHEMA,
            ),
            ToolSpec(
                name="mark_reviewed",
                description="Mark a knowledge entry as reviewed, advancing its review schedule.",
                input_schema=_MARK_REVIEWED_SCHEMA,
            ),
            ToolSpec(
                name="list_entries",
                description="List knowledge entries in the knowledge base, optionally filtered by tag.",
                input_schema=_LIST_ENTRIES_SCHEMA,
            ),
            ToolSpec(
                name="get_entry",
                description="Get the full content of a knowledge entry from Notion.",
                input_schema=_GET_ENTRY_SCHEMA,
            ),
        ]

    async def _dispatch_tool(
        self,
        call: ToolCall,
        turn_state: TurnState,
    ) -> tuple[str, bool]:
        try:
            return await self._dispatch_tool_inner(call, turn_state)
        except NotionClientError as exc:
            log.warning("notion error in tool %s: %s", call.name, exc)
            return str(exc), True
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("input error in tool %s: %s", call.name, exc)
            return f"invalid input for {call.name}: {exc}", True

    async def _dispatch_tool_inner(
        self,
        call: ToolCall,
        turn_state: TurnState,
    ) -> tuple[str, bool]:
        name = call.name
        inp = call.input

        if name == "process_link":
            return await self._tool_process_link(inp)

        if name == "process_text":
            return await self._tool_process_text(inp)

        if name == "list_upcoming_reviews":
            days = int(inp.get("days_ahead", 7))
            return self._tool_list_upcoming_reviews(days)

        if name == "mark_reviewed":
            page_id = inp["page_id"]
            return self._tool_mark_reviewed(page_id)

        if name == "list_entries":
            tag = inp.get("tag")
            limit = int(inp.get("limit", 20))
            return self._tool_list_entries(tag, limit)

        if name == "get_entry":
            page_id = inp["page_id"]
            return await self._tool_get_entry(page_id)

        return f"unknown tool: {name}", True

    async def _tool_process_link(self, inp: dict[str, Any]) -> tuple[str, bool]:
        assert self._llm is not None
        assert self._notion is not None
        assert self._knowledge_index is not None
        assert self._review_schedule is not None

        url = inp["url"]
        user_notes = inp.get("user_notes")

        # Fetch URL content
        import httpx

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as http:
                resp = await http.get(url)
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            return f"Failed to fetch URL: {e}", True

        # Extract article text
        try:
            import trafilatura

            article_text = trafilatura.extract(html) or html[:10000]
        except Exception:
            article_text = html[:10000]

        # Summarize via LLM
        summary_result = await self._summarize_content(article_text, user_notes)

        # Parse LLM response (expects JSON with title, summary, tags)
        try:
            parsed = json.loads(summary_result)
            title = parsed.get("title", "Untitled")
            summary = parsed.get("summary", summary_result)
            tags = parsed.get("tags", [])
        except json.JSONDecodeError:
            title = article_text[:50].strip()
            summary = summary_result
            tags = []

        # Create Notion page
        entry = await self._notion.create_page(
            title=title,
            body_markdown=summary,
            source_url=url,
            source_type="link",
            tags=tags,
            user_notes=user_notes,
        )

        # Update local index + schedule review
        self._knowledge_index.upsert(
            notion_page_id=entry.page_id,
            title=entry.title,
            source_url=entry.source_url,
            source_type=entry.source_type,
            tags=entry.tags,
            user_notes=entry.user_notes,
            status=entry.status,
            created_at=entry.created_at.isoformat(),
            last_edited=entry.last_edited.isoformat(),
        )
        tomorrow = (self._now_local().date() + timedelta(days=1)).isoformat()
        self._review_schedule.create(entry.page_id, first_review_date=tomorrow)

        result = {
            "page_id": entry.page_id,
            "title": entry.title,
            "tags": entry.tags,
            "first_review": tomorrow,
        }
        return json.dumps(result, ensure_ascii=False), False

    async def _tool_process_text(self, inp: dict[str, Any]) -> tuple[str, bool]:
        assert self._llm is not None
        assert self._notion is not None
        assert self._knowledge_index is not None
        assert self._review_schedule is not None

        text = inp["text"]
        user_notes = inp.get("user_notes")

        # Summarize via LLM
        summary_result = await self._summarize_content(text, user_notes)

        try:
            parsed = json.loads(summary_result)
            title = parsed.get("title", "Untitled")
            summary = parsed.get("summary", summary_result)
            tags = parsed.get("tags", [])
        except json.JSONDecodeError:
            title = text[:50].strip()
            summary = summary_result
            tags = []

        entry = await self._notion.create_page(
            title=title,
            body_markdown=summary,
            source_url=None,
            source_type="text",
            tags=tags,
            user_notes=user_notes,
        )

        self._knowledge_index.upsert(
            notion_page_id=entry.page_id,
            title=entry.title,
            source_url=entry.source_url,
            source_type=entry.source_type,
            tags=entry.tags,
            user_notes=entry.user_notes,
            status=entry.status,
            created_at=entry.created_at.isoformat(),
            last_edited=entry.last_edited.isoformat(),
        )
        tomorrow = (self._now_local().date() + timedelta(days=1)).isoformat()
        self._review_schedule.create(entry.page_id, first_review_date=tomorrow)

        result = {
            "page_id": entry.page_id,
            "title": entry.title,
            "tags": entry.tags,
            "first_review": tomorrow,
        }
        return json.dumps(result, ensure_ascii=False), False

    def _tool_list_upcoming_reviews(self, days_ahead: int) -> tuple[str, bool]:
        assert self._review_schedule is not None
        cutoff = (self._now_local().date() + timedelta(days=days_ahead)).isoformat()
        due = self._review_schedule.due_items(cutoff)
        result = [
            {
                "page_id": item["notion_page_id"],
                "title": item["title"],
                "next_review": item["next_review"],
                "times_reviewed": item["times_reviewed"],
            }
            for item in due
        ]
        return json.dumps(result, ensure_ascii=False), False

    def _tool_mark_reviewed(self, page_id: str) -> tuple[str, bool]:
        assert self._review_schedule is not None
        today = self._now_local().date().isoformat()
        self._review_schedule.mark_reviewed(page_id, reviewed_date=today)
        # Look up next review date
        due = self._review_schedule.due_items("2099-12-31")
        for item in due:
            if item["notion_page_id"] == page_id:
                return json.dumps({
                    "page_id": page_id,
                    "next_review": item["next_review"],
                    "times_reviewed": item["times_reviewed"],
                }, ensure_ascii=False), False
        return json.dumps({"page_id": page_id, "status": "reviewed"}, ensure_ascii=False), False

    def _tool_list_entries(
        self, tag: str | None, limit: int
    ) -> tuple[str, bool]:
        assert self._knowledge_index is not None
        entries = self._knowledge_index.list_active()
        if tag:
            entries = [e for e in entries if tag in e.get("tags", [])]
        entries = entries[:limit]
        result = [
            {
                "page_id": e["notion_page_id"],
                "title": e["title"],
                "tags": e["tags"],
                "source_url": e["source_url"],
            }
            for e in entries
        ]
        return json.dumps(result, ensure_ascii=False), False

    async def _tool_get_entry(self, page_id: str) -> tuple[str, bool]:
        assert self._notion is not None
        entry = await self._notion.get_page(page_id)
        result = {
            "page_id": entry.page_id,
            "title": entry.title,
            "source_url": entry.source_url,
            "tags": entry.tags,
            "user_notes": entry.user_notes,
            "body": entry.body,
        }
        return json.dumps(result, ensure_ascii=False), False

    async def _summarize_content(
        self, content: str, user_notes: str | None
    ) -> str:
        """Call LLM to summarize content and extract key points.

        Returns a JSON string with title, summary, and tags.
        """
        assert self._llm is not None
        system = (
            "你是一个知识整理助手。用户给你一段内容，你需要：\n"
            "1. 给出一个简短的标题（title）\n"
            "2. 写一段简洁的总结和关键知识点（summary），控制在 500-800 字以内\n"
            "3. 给出 1-5 个分类标签（tags）\n\n"
            "输出格式必须是 JSON：\n"
            '{"title": "...", "summary": "...", "tags": ["tag1", "tag2"]}\n\n'
            "总结要简洁有力，提取核心知识点，不要冗余。"
        )
        if user_notes:
            system += f"\n\n用户的额外说明：{user_notes}"

        from project0.llm.provider import Msg

        result = await self._llm.complete(
            system=system,
            messages=[Msg(role="user", content=content[:15000])],
            max_tokens=self._config.max_summary_tokens,
            agent="learning",
            purpose="summarize",
        )
        return result.text or "{}"

    def _assemble_system_blocks(self, mode_section: str) -> SystemBlocks:
        stable_parts = [
            self._persona.core,
            "",
            mode_section,
            "",
            self._persona.tool_use_guide,
        ]
        if self._user_profile is not None:
            block = self._user_profile.as_prompt_block()
            if block:
                stable_parts.append("")
                stable_parts.append(block)
        stable = "\n".join(stable_parts)

        facts: str | None = None
        if self._user_facts_reader is not None:
            b = self._user_facts_reader.as_prompt_block()
            facts = b if b else None

        return SystemBlocks(stable=stable, facts=facts)

    def _load_transcript(
        self, chat_id: int | None, *, source: str | None = None
    ) -> str:
        if chat_id is None or self._messages is None:
            return ""
        if source == "telegram_dm":
            envs = self._messages.recent_for_dm(
                chat_id=chat_id,
                agent="learning",
                limit=self._config.transcript_window,
            )
        else:
            envs = self._messages.recent_for_chat(
                chat_id=chat_id, limit=self._config.transcript_window
            )
        lines: list[str] = []
        for e in envs:
            if e.from_kind == "user":
                lines.append(f"user: {e.body}")
            elif e.from_kind == "agent":
                speaker = e.from_agent or "unknown"
                lines.append(f"{speaker}: {e.body}")
        return "\n".join(lines)

    async def handle(self, env: Envelope) -> AgentResult | None:
        reason = env.routing_reason
        if reason == "direct_dm":
            return await self._run_chat_turn(env, self._persona.dm_mode)
        if reason in ("mention", "focus"):
            return await self._run_chat_turn(env, self._persona.group_addressed_mode)
        if reason == "pulse":
            return await self._run_pulse_turn(env)
        log.debug("learning: ignoring routing_reason=%s", reason)
        return None

    async def _run_chat_turn(
        self, env: Envelope, mode_section: str
    ) -> AgentResult | None:
        system = self._assemble_system_blocks(mode_section)
        transcript = self._load_transcript(env.telegram_chat_id, source=env.source)
        now = self._now_local()
        weekday_zh = "一二三四五六日"[now.weekday()]
        preamble = f"当前时间：{now.strftime('%Y-%m-%d %H:%M')} 星期{weekday_zh}（{self._user_tz.key}）"
        initial_user_text = (
            f"{preamble}\n\n对话记录:\n{transcript}\n\n最新用户消息: {env.body}"
            if transcript else f"{preamble}\n\n最新用户消息: {env.body}"
        )
        return await self._agentic_loop(
            env=env,
            system=system,
            initial_user_text=initial_user_text,
            max_tokens=self._config.max_tokens_reply,
            is_pulse=False,
        )

    async def _run_pulse_turn(self, env: Envelope) -> AgentResult | None:
        payload = env.payload or {}
        pulse_name = payload.get("pulse_name", env.body)

        if pulse_name == "notion_sync":
            await self._run_notion_sync()
            return None

        if pulse_name == "review_reminder":
            return await self._run_review_reminder(env)

        log.debug("learning: unknown pulse %s", pulse_name)
        return None

    async def _run_notion_sync(self) -> None:
        """Reconcile local index with Notion database changes."""
        if self._notion is None or self._knowledge_index is None:
            return

        last_sync = self._knowledge_index.last_sync_timestamp()
        if last_sync:
            since = datetime.fromisoformat(last_sync)
        else:
            since = datetime(2020, 1, 1, tzinfo=UTC)

        try:
            changed = await self._notion.query_changed_since(since)
        except NotionClientError as e:
            log.warning("notion_sync: query failed: %s", e)
            return

        for entry in changed:
            self._knowledge_index.upsert(
                notion_page_id=entry.page_id,
                title=entry.title,
                source_url=entry.source_url,
                source_type=entry.source_type,
                tags=entry.tags,
                user_notes=entry.user_notes,
                status="archived" if entry.status == "archived" else entry.status,
                created_at=entry.created_at.isoformat(),
                last_edited=entry.last_edited.isoformat(),
            )
            if self._review_schedule is not None:
                if entry.status in ("archived", "deleted"):
                    self._review_schedule.set_active(entry.page_id, False)
                else:
                    self._review_schedule.set_active(entry.page_id, True)

        log.debug("notion_sync: processed %d changes", len(changed))

    async def _run_review_reminder(self, env: Envelope) -> AgentResult | None:
        """Check for due review items and send a reminder if any exist."""
        if self._review_schedule is None:
            return None

        today = self._now_local().date().isoformat()
        due = self._review_schedule.due_items(today)
        if not due:
            return None

        # Build reminder message via LLM
        system = self._assemble_system_blocks(self._persona.pulse_mode)
        items_text = "\n".join(
            f"  {i+1}. {item['title']} (page_id: {item['notion_page_id']}, "
            f"next_review: {item['next_review']}, "
            f"times_reviewed: {item['times_reviewed']})"
            for i, item in enumerate(due)
        )
        now = self._now_local()
        weekday_zh = "一二三四五六日"[now.weekday()]
        initial_user_text = (
            f"当前时间：{now.strftime('%Y-%m-%d %H:%M')} 星期{weekday_zh}\n\n"
            f"定时脉冲被触发: review_reminder\n\n"
            f"到期复习项目 ({len(due)} 项):\n{items_text}\n\n"
            f"请用温柔的语气提醒少爷复习这些内容。"
        )
        return await self._agentic_loop(
            env=env,
            system=system,
            initial_user_text=initial_user_text,
            max_tokens=self._config.max_tokens_reply,
            is_pulse=True,
        )

    async def _agentic_loop(
        self,
        *,
        env: Envelope,
        system: SystemBlocks,
        initial_user_text: str,
        max_tokens: int,
        is_pulse: bool,
    ) -> AgentResult | None:
        assert self._llm is not None
        loop = await run_agentic_loop(
            llm=self._llm,
            system=system,
            initial_user_text=initial_user_text,
            tools=self._tool_specs,
            dispatch_tool=self._dispatch_tool,
            max_iterations=self._config.max_tool_iterations,
            max_tokens=max_tokens,
            agent="learning",
            purpose="tool_loop",
            envelope_id=env.id,
        )
        if loop.errored:
            return None

        # Review reminder pulse: LLM produces the reminder text
        if is_pulse and loop.final_text:
            return AgentResult(
                reply_text=loop.final_text,
                delegate_to=None,
                handoff_text=None,
            )

        # Other pulse (notion_sync): silent
        if is_pulse:
            return None

        return AgentResult(
            reply_text=loop.final_text or "",
            delegate_to=None,
            handoff_text=None,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/blacktheon/Work/Project-0 && uv run pytest tests/test_learning_agent.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/project0/agents/learning.py tests/test_learning_agent.py
git commit -m "feat(learning): add LearningAgent class with persona, config, tools, and pulse handling"
```

---

### Task 6: Agent registration and composition wiring

**Files:**
- Modify: `src/project0/agents/registry.py`
- Modify: `src/project0/config.py`
- Modify: `src/project0/main.py`

- [ ] **Step 1: Add AGENT_SPECS entry and register_learning to registry.py**

In `src/project0/agents/registry.py`, add to `AGENT_SPECS`:

```python
    "learning": AgentSpec(name="learning", token_env_key="TELEGRAM_BOT_TOKEN_LEARNING"),
```

Add `register_learning` function after `register_intelligence`:

```python
def register_learning(handle: AgentOptionalFn) -> None:
    """Install Learning's ``handle`` into AGENT_REGISTRY + PULSE_REGISTRY."""

    async def agent_adapter(env: Envelope) -> AgentResult:
        result = await handle(env)
        if result is None:
            return AgentResult(
                reply_text="（书瑶暂时不在呢...）",
                delegate_to=None,
                handoff_text=None,
            )
        return result

    AGENT_REGISTRY["learning"] = agent_adapter
    PULSE_REGISTRY["learning"] = handle
```

- [ ] **Step 2: Add Notion settings to config.py**

In `src/project0/config.py`, add two fields to `Settings`:

```python
    notion_token: str
    notion_database_id: str
```

In `load_settings()`, add after `google_client_secrets_path`:

```python
    notion_token = os.environ.get("NOTION_TOKEN", "").strip()
    if not notion_token:
        raise RuntimeError("NOTION_TOKEN is required but was empty or unset")

    notion_database_id = os.environ.get("NOTION_DATABASE_ID", "").strip()
    if not notion_database_id:
        raise RuntimeError("NOTION_DATABASE_ID is required but was empty or unset")
```

Add them to the `Settings(...)` return statement.

- [ ] **Step 3: Wire up Learning agent in main.py**

In `src/project0/main.py`, add after Intelligence wiring (around line 348):

```python
    # --- Learning agent -------------------------------------------------------
    from project0.agents.learning import (
        LearningAgent,
        load_learning_config,
        load_learning_persona,
    )
    from project0.agents.registry import register_learning
    from project0.notion.client import NotionClient

    learning_persona = load_learning_persona(Path("prompts/learning.md"))
    learning_cfg = load_learning_config(Path("prompts/learning.toml"))

    notion_client = NotionClient(
        token=settings.notion_token,
        database_id=settings.notion_database_id,
    )

    learning_facts_reader = UserFactsReader("learning", store.conn)

    learning = LearningAgent(
        llm=llm,
        notion=notion_client,
        knowledge_index=store.knowledge_index(),
        review_schedule=store.review_schedule(),
        messages_store=store.messages(),
        persona=learning_persona,
        config=learning_cfg,
        user_tz=settings.user_tz,
        user_profile=user_profile,
        user_facts_reader=learning_facts_reader,
    )
    register_learning(learning.handle)
    log.info("learning registered (model=%s)", learning_cfg.model)

    # Pulse entries for Learning agent.
    learning_pulse_entries = load_pulse_entries(Path("prompts/learning.toml"))
    log.info(
        "learning pulse entries: %s",
        [(e.name, e.every_seconds) for e in learning_pulse_entries],
    )
```

Then in the TaskGroup section, add pulse loop spawning for learning entries (after the manager pulse loop):

```python
        for entry in learning_pulse_entries:
            tg.create_task(
                run_pulse_loop(
                    entry=entry,
                    target_agent="learning",
                    orchestrator=orch,
                )
            )
            log.info("pulse task spawned: %s", entry.name)
```

- [ ] **Step 4: Add environment variables to .env**

Add to `.env` (the user will need to fill in real values):

```
TELEGRAM_BOT_TOKEN_LEARNING=<create-via-botfather>
NOTION_TOKEN=<notion-integration-token>
NOTION_DATABASE_ID=<notion-database-id>
LEARNING_PULSE_CHAT_ID=<telegram-dm-chat-id>
```

- [ ] **Step 5: Run existing tests to check for regressions**

Run: `cd /home/blacktheon/Work/Project-0 && uv run pytest tests/ -v --timeout=30`
Expected: All tests pass. Note: `test_config.py` tests may fail because `NOTION_TOKEN` and `NOTION_DATABASE_ID` are now required — if so, update the test fixtures to include these env vars.

- [ ] **Step 6: Fix any config test failures**

If `tests/test_config.py` fails because of the new required env vars, add `NOTION_TOKEN=secret_test` and `NOTION_DATABASE_ID=db-test-123` and `TELEGRAM_BOT_TOKEN_LEARNING=test-learning-token` and `LEARNING_PULSE_CHAT_ID=12345` to the test's environment setup.

- [ ] **Step 7: Commit**

```bash
git add src/project0/agents/registry.py src/project0/config.py src/project0/main.py
git commit -m "feat(learning): wire up LearningAgent in registry, config, and main.py"
```

---

### Task 7: Add tests for Learning agent tool dispatch

**Files:**
- Modify: `tests/test_learning_agent.py`

- [ ] **Step 1: Add tool dispatch tests**

Append to `tests/test_learning_agent.py`:

```python
from project0.agents._tool_loop import TurnState
from project0.llm.tools import ToolCall
from project0.store import KnowledgeIndexStore, ReviewScheduleStore, Store


@pytest.fixture
def store() -> Store:
    s = Store(":memory:")
    s.init_schema()
    return s


@pytest.fixture
def knowledge_index(store: Store) -> KnowledgeIndexStore:
    return KnowledgeIndexStore(store.conn)


@pytest.fixture
def review_schedule(store: Store) -> ReviewScheduleStore:
    return ReviewScheduleStore(store.conn)


def _make_agent(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> LearningAgent:
    persona = load_learning_persona(PROMPTS_DIR / "learning.md")
    config = load_learning_config(PROMPTS_DIR / "learning.toml")
    return LearningAgent(
        llm=None,
        notion=None,
        knowledge_index=knowledge_index,
        review_schedule=review_schedule,
        messages_store=None,
        persona=persona,
        config=config,
    )


async def test_list_upcoming_reviews_tool(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    agent = _make_agent(knowledge_index, review_schedule)
    knowledge_index.upsert(
        notion_page_id="page-1", title="Test Entry", source_url=None,
        source_type="text", tags=["python"], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    review_schedule.create("page-1", first_review_date="2026-04-17")

    call = ToolCall(id="call-1", name="list_upcoming_reviews", input={"days_ahead": 7})
    turn_state = TurnState()
    content, is_err = await agent._dispatch_tool(call, turn_state)
    assert not is_err
    data = json.loads(content)
    assert len(data) == 1
    assert data[0]["page_id"] == "page-1"


async def test_mark_reviewed_tool(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    agent = _make_agent(knowledge_index, review_schedule)
    knowledge_index.upsert(
        notion_page_id="page-1", title="Test Entry", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    review_schedule.create("page-1", first_review_date="2026-04-17")

    call = ToolCall(id="call-1", name="mark_reviewed", input={"page_id": "page-1"})
    turn_state = TurnState()
    content, is_err = await agent._dispatch_tool(call, turn_state)
    assert not is_err
    data = json.loads(content)
    assert data["page_id"] == "page-1"


async def test_list_entries_tool(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    agent = _make_agent(knowledge_index, review_schedule)
    knowledge_index.upsert(
        notion_page_id="page-1", title="Python GIL", source_url=None,
        source_type="text", tags=["python"], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    knowledge_index.upsert(
        notion_page_id="page-2", title="React Hooks", source_url=None,
        source_type="text", tags=["react"], user_notes=None, status="active",
        created_at="2026-04-16T11:00:00Z", last_edited="2026-04-16T11:00:00Z",
    )

    # No filter
    call = ToolCall(id="call-1", name="list_entries", input={})
    turn_state = TurnState()
    content, is_err = await agent._dispatch_tool(call, turn_state)
    assert not is_err
    data = json.loads(content)
    assert len(data) == 2

    # Filter by tag
    call = ToolCall(id="call-2", name="list_entries", input={"tag": "python"})
    content, is_err = await agent._dispatch_tool(call, turn_state)
    assert not is_err
    data = json.loads(content)
    assert len(data) == 1
    assert data[0]["title"] == "Python GIL"


async def test_unknown_tool_returns_error(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    agent = _make_agent(knowledge_index, review_schedule)
    call = ToolCall(id="call-1", name="nonexistent_tool", input={})
    turn_state = TurnState()
    content, is_err = await agent._dispatch_tool(call, turn_state)
    assert is_err
    assert "unknown tool" in content


async def test_handle_routing_pulse(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    """Verify that pulse routing dispatches without error (notion_sync
    returns None since notion client is None)."""
    agent = _make_agent(knowledge_index, review_schedule)
    env = Envelope(
        id=1, ts="2026-04-16T10:00:00Z", parent_id=None,
        source="pulse", telegram_chat_id=None, telegram_msg_id=None,
        received_by_bot=None, from_kind="system", from_agent=None,
        to_agent="learning", body="notion_sync", mentions=[],
        routing_reason="pulse",
        payload={"pulse_name": "notion_sync"},
    )
    result = await agent.handle(env)
    assert result is None  # notion_sync is silent


async def test_handle_routing_unknown_reason(
    knowledge_index: KnowledgeIndexStore,
    review_schedule: ReviewScheduleStore,
) -> None:
    agent = _make_agent(knowledge_index, review_schedule)
    env = Envelope(
        id=1, ts="2026-04-16T10:00:00Z", parent_id=None,
        source="telegram_group", telegram_chat_id=123, telegram_msg_id=1,
        received_by_bot=None, from_kind="user", from_agent=None,
        to_agent="learning", body="hello", mentions=[],
        routing_reason="listener_observation",
        payload=None,
    )
    result = await agent.handle(env)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /home/blacktheon/Work/Project-0 && uv run pytest tests/test_learning_agent.py -v`
Expected: All tests pass (2 persona/config + 6 tool/routing tests)

- [ ] **Step 3: Commit**

```bash
git add tests/test_learning_agent.py
git commit -m "test(learning): add tool dispatch and handle routing tests"
```

---

### Task 8: Run full test suite and verify

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `cd /home/blacktheon/Work/Project-0 && uv run pytest tests/ -v --timeout=30`
Expected: All tests pass

- [ ] **Step 2: Run type checker**

Run: `cd /home/blacktheon/Work/Project-0 && uv run mypy src/project0/notion/ src/project0/agents/learning.py --strict`
Expected: No errors (or only pre-existing ones from upstream)

- [ ] **Step 3: Run linter**

Run: `cd /home/blacktheon/Work/Project-0 && uv run ruff check src/project0/notion/ src/project0/agents/learning.py`
Expected: No errors

- [ ] **Step 4: Fix any issues found and commit**

```bash
git add -A
git commit -m "fix: address type/lint issues in learning agent"
```

---

### Task 9: Smoke test on a feature branch

**Files:** None (manual testing)

**Important:** Per user feedback, do NOT use git worktrees. Create a feature branch in the main project directory.

- [ ] **Step 1: Ensure .env has all required Learning agent variables**

Verify these are set in `.env`:
```
TELEGRAM_BOT_TOKEN_LEARNING=<real-bot-token>
NOTION_TOKEN=<real-notion-token>
NOTION_DATABASE_ID=<real-database-id>
LEARNING_PULSE_CHAT_ID=<real-chat-id>
```

- [ ] **Step 2: Create and set up the Notion database**

The user needs to:
1. Create a Notion database with these properties: Title (title), Source (URL), Source Type (select: link/text), Tags (multi-select), User Notes (rich text), Status (select: active/archived)
2. Create a Notion integration at https://www.notion.so/my-integrations
3. Share the database with the integration
4. Copy the integration token and database ID to `.env`

- [ ] **Step 3: Start MAAS and verify Learning agent boots**

Run: `cd /home/blacktheon/Work/Project-0 && uv run python -m project0.main`

Verify in logs:
- `learning registered (model=claude-sonnet-4-6)`
- `pulse task spawned: notion_sync`
- `pulse task spawned: review_reminder`
- No crash on startup

- [ ] **Step 4: Test basic interaction via Telegram DM**

Send a message to the Learning bot on Telegram:
1. "你好" — verify she responds in character as 温书瑶
2. Send a link — verify it gets processed and saved to Notion
3. "复习计划" — verify she lists upcoming reviews
4. "已复习 1" — verify she marks it as reviewed

- [ ] **Step 5: Verify Notion sync pulse is running**

Check logs for periodic `notion_sync: processed 0 changes` entries every 30 seconds.

- [ ] **Step 6: Commit any smoke test fixes**

```bash
git add -A
git commit -m "fix: address issues found during smoke test"
```

---

## Dependencies Between Tasks

```
Task 1 (model) ─┐
                 ├── Task 2 (client) ──┐
Task 3 (store) ─┘                     │
                                       ├── Task 5 (agent class) ── Task 6 (wiring) ── Task 7 (tests) ── Task 8 (verify) ── Task 9 (smoke)
Task 4 (persona/config) ──────────────┘
```

Tasks 1, 3, and 4 are independent and can be done in parallel. Task 2 depends on Task 1. Task 5 depends on Tasks 2, 3, and 4. Tasks 6-9 are sequential.
