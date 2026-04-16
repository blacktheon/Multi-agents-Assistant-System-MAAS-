"""Unit tests for src/project0/notion/client.py."""

from __future__ import annotations

from project0.notion.client import NotionClient
from project0.notion.model import NotionClientError


def test_notion_client_error_wraps_message() -> None:
    err = NotionClientError("Notion API rate limit exceeded")
    assert "rate limit" in str(err)


def test_notion_client_init_requires_token_and_db_id() -> None:
    # _client=None skips real SDK initialization for testing.
    client = NotionClient(token="secret_test", database_id="db-123", _client=None)
    assert client._database_id == "db-123"
