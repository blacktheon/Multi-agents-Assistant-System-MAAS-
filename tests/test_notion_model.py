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
