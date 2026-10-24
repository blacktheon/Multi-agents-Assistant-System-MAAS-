"""Unit tests for LocalProvider. The endpoint is mocked with respx.

Covers the happy path in this task; error paths land in Task 7, the
NotImplementedError guard in Task 8.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from project0.llm.local_provider import LocalProvider
from project0.llm.provider import LLMProviderError, Msg, SystemBlocks
from project0.llm.tools import AssistantToolUseMsg, ToolCall, ToolResultMsg, ToolSpec
from project0.store import LLMUsageStore, Store

BASE_URL = "http://127.0.0.1:8000/v1"
MODEL = "qwen2.5-72b-awq-8k"


def _make_usage_store(tmp_path: Path) -> LLMUsageStore:
    store = Store(str(tmp_path / "llm_usage.db"))
    store.init_schema()
    return LLMUsageStore(store.conn)


@pytest.mark.asyncio
@respx.mock
async def test_local_provider_complete_happy_path(tmp_path: Path) -> None:
    usage = _make_usage_store(tmp_path)
    provider = LocalProvider(
        base_url=BASE_URL,
        model=MODEL,
        api_key="unused",
        usage_store=usage,
    )

    respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "cmpl-1",
                "object": "chat.completion",
                "created": 0,
                "model": MODEL,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "你好，老公。"},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 123,
                    "completion_tokens": 45,
                    "total_tokens": 168,
                },
            },
        )
    )

    out = await provider.complete(
        system="You are Secretary.",
        messages=[Msg(role="user", content="hi")],
        max_tokens=200,
        agent="secretary",
        purpose="reply",
        envelope_id=42,
    )
    assert out == "你好，老公。"

    cur = usage._conn.execute(
        "SELECT agent, model, input_tokens, output_tokens, "
        "cache_creation_input_tokens, cache_read_input_tokens, "
        "envelope_id, purpose FROM llm_usage"
    )
    rows = cur.fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "secretary"
    assert row[1] == MODEL
    assert row[2] == 123
    assert row[3] == 45
    assert row[4] == 0
    assert row[5] == 0
    assert row[6] == 42
    assert row[7] == "reply"


@pytest.mark.asyncio
@respx.mock
async def test_local_provider_joins_system_blocks(tmp_path: Path) -> None:
    usage = _make_usage_store(tmp_path)
    provider = LocalProvider(
        base_url=BASE_URL, model=MODEL, api_key="unused", usage_store=usage,
    )
    captured: list[dict] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    respx.post(f"{BASE_URL}/chat/completions").mock(side_effect=_capture)

    await provider.complete(
        system=SystemBlocks(stable="PERSONA", facts="FACTS"),
        messages=[Msg(role="user", content="hi")],
        max_tokens=50,
        agent="secretary",
        purpose="reply",
    )

    payload = captured[0]
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][0]["content"] == "PERSONA\n\nFACTS"
    assert payload["messages"][1] == {"role": "user", "content": "hi"}
    assert payload["model"] == MODEL
    assert payload["max_tokens"] == 50
    assert payload["stream"] is False


@pytest.mark.asyncio
@respx.mock
async def test_local_provider_connection_refused(tmp_path: Path) -> None:
    from project0.llm.local_provider import LocalProviderUnavailableError

    usage = _make_usage_store(tmp_path)
    provider = LocalProvider(base_url=BASE_URL, model=MODEL, api_key="k", usage_store=usage)
    respx.post(f"{BASE_URL}/chat/completions").mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(LocalProviderUnavailableError):
        await provider.complete(
            system="s", messages=[Msg(role="user", content="hi")],
            max_tokens=50, agent="secretary", purpose="reply",
        )


@pytest.mark.asyncio
@respx.mock
async def test_local_provider_timeout(tmp_path: Path) -> None:
    from project0.llm.local_provider import LocalProviderUnavailableError

    usage = _make_usage_store(tmp_path)
    provider = LocalProvider(
        base_url=BASE_URL, model=MODEL, api_key="k", usage_store=usage,
        request_timeout_seconds=0.1,
    )
    respx.post(f"{BASE_URL}/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(LocalProviderUnavailableError):
        await provider.complete(
            system="s", messages=[Msg(role="user", content="hi")],
            max_tokens=50, agent="secretary", purpose="reply",
        )


@pytest.mark.asyncio
@respx.mock
async def test_local_provider_400_context_length(tmp_path: Path) -> None:
    from project0.llm.local_provider import LocalProviderContextError

    usage = _make_usage_store(tmp_path)
    provider = LocalProvider(base_url=BASE_URL, model=MODEL, api_key="k", usage_store=usage)
    respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "This model's maximum context length is 8192 tokens."}},
        )
    )

    with pytest.raises(LocalProviderContextError):
        await provider.complete(
            system="s", messages=[Msg(role="user", content="hi")],
            max_tokens=50, agent="secretary", purpose="reply",
        )


@pytest.mark.asyncio
@respx.mock
async def test_local_provider_400_non_context_raises_unavailable(tmp_path: Path) -> None:
    from project0.llm.local_provider import LocalProviderUnavailableError

    usage = _make_usage_store(tmp_path)
    provider = LocalProvider(base_url=BASE_URL, model=MODEL, api_key="k", usage_store=usage)
    respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad request"}})
    )

    with pytest.raises(LocalProviderUnavailableError):
        await provider.complete(
            system="s", messages=[Msg(role="user", content="hi")],
            max_tokens=50, agent="secretary", purpose="reply",
        )


@pytest.mark.asyncio
@respx.mock
async def test_local_provider_500_then_200_retries_once(tmp_path: Path) -> None:
    usage = _make_usage_store(tmp_path)
    provider = LocalProvider(
        base_url=BASE_URL, model=MODEL, api_key="k",
        usage_store=usage, retry_sleep_seconds=0.0,
    )
    route = respx.post(f"{BASE_URL}/chat/completions").mock(
        side_effect=[
            httpx.Response(500, json={"error": {"message": "internal"}}),
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            ),
        ]
    )

    out = await provider.complete(
        system="s", messages=[Msg(role="user", content="hi")],
        max_tokens=50, agent="secretary", purpose="reply",
    )
    assert out == "ok"
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_local_provider_500_twice_raises_unavailable(tmp_path: Path) -> None:
    from project0.llm.local_provider import LocalProviderUnavailableError

    usage = _make_usage_store(tmp_path)
    provider = LocalProvider(
        base_url=BASE_URL, model=MODEL, api_key="k",
        usage_store=usage, retry_sleep_seconds=0.0,
    )
    respx.post(f"{BASE_URL}/chat/completions").mock(
        side_effect=[
            httpx.Response(500, json={"error": {"message": "x"}}),
            httpx.Response(500, json={"error": {"message": "x"}}),
        ]
    )

    with pytest.raises(LocalProviderUnavailableError):
        await provider.complete(
            system="s", messages=[Msg(role="user", content="hi")],
            max_tokens=50, agent="secretary", purpose="reply",
        )


@pytest.mark.asyncio
@respx.mock
async def test_local_provider_empty_content_returns_empty_string(tmp_path: Path) -> None:
    usage = _make_usage_store(tmp_path)
    provider = LocalProvider(base_url=BASE_URL, model=MODEL, api_key="k", usage_store=usage)
    respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": None}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            },
        )
    )

    out = await provider.complete(
        system="s", messages=[Msg(role="user", content="hi")],
        max_tokens=50, agent="secretary", purpose="reply",
    )
    assert out == ""


# --- tool use via Qwen3.5's native text protocol -----------------------------

TOOL = ToolSpec(
    name="calendar_list_events",
    description="List events.",
    input_schema={
        "type": "object",
        "properties": {"day": {"type": "string"}, "limit": {"type": "integer"}},
    },
)


def _completion(content: str) -> dict:
    return {
        "id": "cmpl-t",
        "object": "chat.completion",
        "created": 0,
        "model": MODEL,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.mark.asyncio
@respx.mock
async def test_complete_with_tools_parses_call_and_sends_no_tools_field(tmp_path: Path) -> None:
    usage = _make_usage_store(tmp_path)
    provider = LocalProvider(base_url=BASE_URL, model=MODEL, api_key="unused", usage_store=usage)
    route = respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion(
            "<tool_call>\n<function=calendar_list_events>\n<parameter=day>\n明天\n</parameter>\n"
            "<parameter=limit>\n5\n</parameter>\n</function>\n</tool_call>"
        ))
    )

    result = await provider.complete_with_tools(
        system=SystemBlocks(stable="You are 林夕.", facts="喜欢茶"),
        messages=[Msg(role="user", content="明天有什么安排？")],
        tools=[TOOL],
        max_tokens=512,
        agent="manager",
        purpose="tool_loop",
        envelope_id=7,
    )

    body = json.loads(route.calls.last.request.content)
    assert "tools" not in body
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"].startswith("# Tools")
    assert body["messages"][0]["content"].endswith("You are 林夕.\n\n喜欢茶")
    assert body["messages"][1] == {"role": "user", "content": "明天有什么安排？"}
    assert result.kind == "tool_use"
    assert result.text is None
    assert result.tool_calls == [
        ToolCall(id="call_1", name="calendar_list_events", input={"day": "明天", "limit": 5})
    ]
    assert result.stop_reason == "stop"
    row = usage._conn.execute(
        "SELECT agent, purpose, envelope_id, input_tokens FROM llm_usage"
    ).fetchone()
    assert tuple(row) == ("manager", "tool_loop", 7, 10)


@pytest.mark.asyncio
@respx.mock
async def test_complete_with_tools_translates_history(tmp_path: Path) -> None:
    usage = _make_usage_store(tmp_path)
    provider = LocalProvider(base_url=BASE_URL, model=MODEL, api_key="unused", usage_store=usage)
    route = respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("明天下午三点有会议。"))
    )
    call = ToolCall(id="call_1", name="calendar_list_events", input={"day": "明天"})

    result = await provider.complete_with_tools(
        system="You are 林夕.",
        messages=[
            Msg(role="user", content="明天有什么安排？"),
            AssistantToolUseMsg(tool_calls=[call], text="我查一下"),
            ToolResultMsg(tool_use_id="call_1", content='[{"title": "会议"}]', is_error=False),
            ToolResultMsg(tool_use_id="call_1", content="not found", is_error=True),
        ],
        tools=[TOOL],
        max_tokens=512,
        agent="manager",
        purpose="tool_loop",
    )

    body = json.loads(route.calls.last.request.content)
    assert len(body["messages"]) == 4
    assert body["messages"][2]["role"] == "assistant"
    assert body["messages"][2]["content"].startswith("我查一下\n\n<tool_call>")
    assert body["messages"][3] == {
        "role": "user",
        "content": (
            '<tool_response>\n[{"title": "会议"}]\n</tool_response>\n'
            "<tool_response>\n[tool error] not found\n</tool_response>"
        ),
    }
    assert result.kind == "text"
    assert result.text == "明天下午三点有会议。"
    assert result.tool_calls == []


@pytest.mark.asyncio
@respx.mock
async def test_complete_with_tools_connection_error_is_provider_error(tmp_path: Path) -> None:
    usage = _make_usage_store(tmp_path)
    provider = LocalProvider(base_url=BASE_URL, model=MODEL, api_key="unused", usage_store=usage)
    respx.post(f"{BASE_URL}/chat/completions").mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(LLMProviderError):
        await provider.complete_with_tools(
            system="s",
            messages=[Msg(role="user", content="hi")],
            tools=[TOOL],
            max_tokens=50,
            agent="manager",
            purpose="tool_loop",
        )
