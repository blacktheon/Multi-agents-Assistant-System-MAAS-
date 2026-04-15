"""Verify run_agentic_loop plumbs agent/purpose/envelope_id into llm_usage."""
from __future__ import annotations

import pytest

from project0.agents._tool_loop import run_agentic_loop
from project0.llm.provider import FakeProvider
from project0.llm.tools import ToolUseResult
from project0.store import LLMUsageStore, Store


@pytest.mark.asyncio
async def test_run_agentic_loop_records_agent_and_purpose_labels() -> None:
    store = Store(":memory:")
    store.init_schema()
    usage = LLMUsageStore(store.conn)
    fake = FakeProvider(
        tool_responses=[
            ToolUseResult(
                kind="text",
                text="final reply",
                tool_calls=[],
                stop_reason="end_turn",
            ),
        ],
        usage_store=usage,
    )

    async def _dispatch(call, state):
        return ("ok", False)

    result = await run_agentic_loop(
        llm=fake,
        system="persona",
        initial_user_text="hi",
        tools=[],
        dispatch_tool=_dispatch,
        max_iterations=3,
        max_tokens=800,
        agent="manager",
        purpose="tool_loop",
        envelope_id=7,
    )
    assert result.final_text == "final reply"
    rows = usage.summary_since("1970-01-01T00:00:00Z")
    assert rows == [{
        "agent": "manager",
        "input_tokens": 100,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 80,
        "output_tokens": 20,
        "calls": 1,
    }]
    # envelope_id and purpose landed in the raw row as well
    raw = [
        (r["purpose"], r["envelope_id"])
        for r in store.conn.execute(
            "SELECT purpose, envelope_id FROM llm_usage"
        ).fetchall()
    ]
    assert raw == [("tool_loop", 7)]
