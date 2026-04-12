"""Pytest fixtures for the skeleton test suite."""

from __future__ import annotations

import pytest

from project0.store import Store


@pytest.fixture()
def store() -> Store:
    """Fresh in-memory SQLite store per test."""
    s = Store(":memory:")
    s.init_schema()
    return s
