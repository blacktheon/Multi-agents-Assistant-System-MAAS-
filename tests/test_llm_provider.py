"""Tests for the LLM provider abstraction. No test hits the real Anthropic
API — AnthropicProvider is exercised with a mocked SDK in Task 5."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from project0.llm.provider import (
    FakeProvider,
    LLMProviderError,
    Msg,
)
from project0.store import LLMUsageStore, Store


def _usage_store() -> LLMUsageStore:
    s = Store(":memory:")
    s.init_schema()
    return LLMUsageStore(s.conn)


@pytest.mark.asyncio
async def test_fake_provider_returns_canned_responses_in_order() -> None:
    p = FakeProvider(responses=["first", "second", "third"])
    assert await p.complete(system="sys", messages=[Msg(role="user", content="a")], agent="secretary", purpose="reply", envelope_id=None) == "first"
    assert await p.complete(system="sys", messages=[Msg(role="user", content="b")], agent="secretary", purpose="reply", envelope_id=None) == "second"
    assert await p.complete(system="sys", messages=[Msg(role="user", content="c")], agent="secretary", purpose="reply", envelope_id=None) == "third"


@pytest.mark.asyncio
async def test_fake_provider_raises_when_out_of_canned_responses() -> None:
    p = FakeProvider(responses=["only"])
    await p.complete(system="sys", messages=[], agent="secretary", purpose="reply", envelope_id=None)
    with pytest.raises(LLMProviderError):
        await p.complete(system="sys", messages=[], agent="secretary", purpose="reply", envelope_id=None)


@pytest.mark.asyncio
async def test_fake_provider_callable_mode_receives_inputs() -> None:
    captured: list[tuple[str, list[Msg]]] = []

    def fn(system: str, messages: list[Msg]) -> str:
        captured.append((system, list(messages)))
        return f"saw {len(messages)} msgs"

    p = FakeProvider(callable_=fn)
    out = await p.complete(
        system="PERSONA",
        messages=[Msg(role="user", content="hi"), Msg(role="assistant", content="hey")],
        agent="secretary", purpose="reply", envelope_id=None,
    )
    assert out == "saw 2 msgs"
    assert captured[0][0] == "PERSONA"
    assert [m.content for m in captured[0][1]] == ["hi", "hey"]


@pytest.mark.asyncio
async def test_fake_provider_records_all_calls() -> None:
    p = FakeProvider(responses=["a", "b"])
    await p.complete(system="S1", messages=[Msg(role="user", content="x")], agent="secretary", purpose="reply", envelope_id=None)
    await p.complete(system="S2", messages=[Msg(role="user", content="y")], max_tokens=100, agent="secretary", purpose="reply", envelope_id=None)
    assert len(p.calls) == 2
    assert p.calls[0].system == "S1"
    assert p.calls[0].max_tokens == 800  # default
    assert p.calls[1].system == "S2"
    assert p.calls[1].max_tokens == 100


def _mock_stream_ctx(*, final_content: list) -> MagicMock:
    """Build a mock for `messages.stream(...)` returning an async context
    manager whose `get_final_message()` returns a message with the given
    content blocks. (6e: AnthropicProvider.complete uses streaming.)"""
    final_message = SimpleNamespace(content=final_content)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=ctx)
    ctx.__aexit__ = AsyncMock(return_value=None)
    ctx.get_final_message = AsyncMock(return_value=final_message)
    return ctx


@pytest.mark.asyncio
async def test_anthropic_provider_passes_prompt_cache_control() -> None:
    """AnthropicProvider must send the system prompt as a content block
    with cache_control={'type': 'ephemeral'}, not as a plain string."""
    from project0.llm.provider import AnthropicProvider

    ctx = _mock_stream_ctx(
        final_content=[SimpleNamespace(text="hi from fake claude", type="text")]
    )

    with patch("project0.llm.provider.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=ctx)
        mock_cls.return_value = mock_client

        p = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6", usage_store=_usage_store())
        out = await p.complete(
            system="PERSONA",
            messages=[Msg(role="user", content="hello")],
            max_tokens=500,
            agent="secretary", purpose="reply", envelope_id=None,
        )

    assert out == "hi from fake claude"
    mock_client.messages.stream.assert_called_once()
    kwargs = mock_client.messages.stream.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["max_tokens"] == 500
    assert kwargs["system"] == [
        {"type": "text", "text": "PERSONA", "cache_control": {"type": "ephemeral"}}
    ]
    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_anthropic_provider_raises_on_sdk_error() -> None:
    import anthropic

    from project0.llm.provider import AnthropicProvider

    with patch("project0.llm.provider.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        # The stream() call itself raises — simulates network-level failure.
        mock_client.messages.stream = MagicMock(
            side_effect=anthropic.APIConnectionError(request=MagicMock())
        )
        mock_cls.return_value = mock_client

        p = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6", usage_store=_usage_store())
        with pytest.raises(LLMProviderError, match="APIConnectionError"):
            await p.complete(system="S", messages=[Msg(role="user", content="x")], agent="secretary", purpose="reply", envelope_id=None)


@pytest.mark.asyncio
async def test_anthropic_provider_returns_empty_when_no_text_blocks() -> None:
    """If Claude returns a response with no text content (e.g. only tool_use
    blocks), raise LLMProviderError rather than silently returning empty."""
    from project0.llm.provider import AnthropicProvider

    ctx = _mock_stream_ctx(final_content=[])

    with patch("project0.llm.provider.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=ctx)
        mock_cls.return_value = mock_client
        p = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6", usage_store=_usage_store())
        with pytest.raises(LLMProviderError):
            await p.complete(system="S", messages=[Msg(role="user", content="x")], agent="secretary", purpose="reply", envelope_id=None)
