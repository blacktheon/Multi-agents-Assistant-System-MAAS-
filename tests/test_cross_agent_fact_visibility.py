"""Cross-agent fact visibility (Task 18).

End-to-end: Secretary writes a fact via the writer, then Manager and
Intelligence readers see it via as_prompt_block(). This is the
learning-across-agents proof for the sub-project.
"""
from __future__ import annotations

import pytest

from project0.store import Store, UserFactsReader, UserFactsWriter


@pytest.mark.asyncio
async def test_fact_written_by_secretary_visible_to_manager_and_intelligence() -> None:
    store = Store(":memory:")
    store.init_schema()

    writer = UserFactsWriter("secretary", store.conn)
    writer.add("最喜欢吃寿司", topic="food")

    manager_reader = UserFactsReader("manager", store.conn)
    intelligence_reader = UserFactsReader("intelligence", store.conn)

    mgr_block = manager_reader.as_prompt_block()
    intel_block = intelligence_reader.as_prompt_block()
    assert "寿司" in mgr_block
    assert "寿司" in intel_block
