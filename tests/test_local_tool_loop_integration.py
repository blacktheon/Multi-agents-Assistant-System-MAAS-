"""run_agentic_loop end-to-end over LocalProvider against a mocked Ollama
endpoint: tool call → dispatch → tool response → final text, and a server
outage surfacing as errored=True rather than an exception."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from project0.agents._tool_loop import TurnState, run_agentic_loop
from project0.llm.local_provider import LocalProvider
from project0.llm.tools import ToolCall, ToolSpec
from project0.store import LLMUsageStore, Store

BASE_URL = "http://127.0.0.1:11434/v1"
MODEL = "qwen3.5-122b-abliterated-q4km-32k"
TOOL_ECHO = ToolSpec(
    name="echo",
    description="Echo the input back.",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
)


async def _dispatch_echo(call: ToolCall, turn_state: TurnState) -> tuple[str, bool]:
    if call.name == "echo":
        return call.input.get("text", ""), False
    return f"unknown tool: {call.name}", True


def _provider(tmp_path: Path) -> LocalProvider:
    store = Store(str(tmp_path / "u.db"))
    store.init_schema()
    return LocalProvider(
        base_url=BASE_URL,
        model=MODEL,
        api_key="unused",
        usage_store=LLMUsageStore(store.conn),
        retry_sleep_seconds=0.0,
    )


def _completion(content: str) -> dict:
    return {
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


async def _run(tmp_path: Path):
    return await run_agentic_loop(
        llm=_provider(tmp_path),
        system="sys",
        initial_user_text="hi",
        tools=[TOOL_ECHO],
        dispatch_tool=_dispatch_echo,
        max_iterations=4,
        max_tokens=256,
        agent="manager",
        purpose="tool_loop",
    )


@pytest.mark.asyncio
@respx.mock
async def test_tool_call_then_text_over_local_provider(tmp_path: Path) -> None:
    replies = iter([
        _completion(
            "<tool_call>\n<function=echo>\n<parameter=text>\nping\n</parameter>\n"
            "</function>\n</tool_call>"
        ),
        _completion("ping done"),
    ])
    route = respx.post(f"{BASE_URL}/chat/completions").mock(
        side_effect=lambda request: httpx.Response(200, json=next(replies))
    )

    result = await _run(tmp_path)

    assert result.final_text == "ping done"
    assert result.errored is False
    assert route.call_count == 2
    second = json.loads(route.calls[1].request.content)
    assert second["messages"][-1] == {
        "role": "user",
        "content": "<tool_response>\nping\n</tool_response>",
    }
    assert second["messages"][-2]["role"] == "assistant"
    assert "<function=echo>" in second["messages"][-2]["content"]


@pytest.mark.asyncio
@respx.mock
async def test_persistent_server_error_is_flagged_not_raised(tmp_path: Path) -> None:
    respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": "overloaded"})
    )

    result = await _run(tmp_path)

    assert result.errored is True
    assert result.final_text is None
