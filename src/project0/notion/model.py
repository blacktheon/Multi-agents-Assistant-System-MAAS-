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
