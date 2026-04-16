"""Async wrapper around the Notion API for knowledge base operations."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
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
        since_iso = since.astimezone(UTC).isoformat()
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
        return datetime.now(UTC)
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
