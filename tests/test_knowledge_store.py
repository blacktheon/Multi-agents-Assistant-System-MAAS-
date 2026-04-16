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
        notion_page_id="page-1", title="Original", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    index_store.upsert(
        notion_page_id="page-1", title="Updated", source_url=None,
        source_type="text", tags=["new-tag"], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T11:00:00Z",
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


def test_create_review(
    index_store: KnowledgeIndexStore, review_store: ReviewScheduleStore,
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
    index_store: KnowledgeIndexStore, review_store: ReviewScheduleStore,
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
    index_store: KnowledgeIndexStore, review_store: ReviewScheduleStore,
) -> None:
    index_store.upsert(
        notion_page_id="page-1", title="A", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    review_store.create("page-1", first_review_date="2026-04-17")
    review_store.mark_reviewed("page-1", reviewed_date="2026-04-17")
    due = review_store.due_items("2026-04-17")
    assert len(due) == 0
    due_later = review_store.due_items("2026-04-20")
    assert len(due_later) == 1
    assert due_later[0]["interval_step"] == 1
    assert due_later[0]["times_reviewed"] == 1


def test_mark_reviewed_caps_at_max_interval(
    index_store: KnowledgeIndexStore, review_store: ReviewScheduleStore,
) -> None:
    index_store.upsert(
        notion_page_id="page-1", title="A", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    review_store.create("page-1", first_review_date="2026-04-17")
    dates = ["2026-04-17", "2026-04-20", "2026-04-27", "2026-05-11", "2026-06-10"]
    for d in dates:
        review_store.mark_reviewed("page-1", reviewed_date=d)
    due = review_store.due_items("2026-07-10")
    assert len(due) == 1
    assert due[0]["interval_step"] == 4


def test_set_active_deactivates_and_reactivates(
    index_store: KnowledgeIndexStore, review_store: ReviewScheduleStore,
) -> None:
    index_store.upsert(
        notion_page_id="page-1", title="A", source_url=None,
        source_type="text", tags=[], user_notes=None, status="active",
        created_at="2026-04-16T10:00:00Z", last_edited="2026-04-16T10:00:00Z",
    )
    review_store.create("page-1", first_review_date="2026-04-17")
    review_store.set_active("page-1", False)
    due = review_store.due_items("2026-04-17")
    assert len(due) == 0
    review_store.set_active("page-1", True)
    due = review_store.due_items("2026-04-17")
    assert len(due) == 1


def test_remove_review(
    index_store: KnowledgeIndexStore, review_store: ReviewScheduleStore,
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
