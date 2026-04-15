"""Tests for the `thinking_budget_tokens` kwarg added to LLMProvider.complete (6e).

AnthropicProvider.complete uses `client.messages.stream(...)` (required by the
SDK for long-running requests). Tests mock the streaming context manager.
"""

from unittest.mock import AsyncMock, MagicMock

from project0.llm.provider import AnthropicProvider, FakeProvider, Msg
from project0.store import LLMUsageStore, Store


def _usage_store() -> LLMUsageStore:
    s = Store(":memory:")
    s.init_schema()
    return LLMUsageStore(s.conn)


def _make_mock_stream(*content_blocks: MagicMock) -> MagicMock:
    """Build a mock for `_client.messages.stream(...)` that returns an async
    context manager whose `get_final_message()` returns a message with the
    given content blocks."""
    final_message = MagicMock()
    final_message.content = list(content_blocks)

    stream_ctx = MagicMock()
    stream_ctx.__aenter__ = AsyncMock(return_value=stream_ctx)
    stream_ctx.__aexit__ = AsyncMock(return_value=None)
    stream_ctx.get_final_message = AsyncMock(return_value=final_message)

    stream_fn = MagicMock(return_value=stream_ctx)
    return stream_fn


def _text_block(text: str) -> MagicMock:
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _thinking_block(text: str) -> MagicMock:
    b = MagicMock()
    b.type = "thinking"
    b.text = text
    return b


async def test_fake_provider_records_thinking_budget_in_call() -> None:
    fake = FakeProvider(responses=["ok"])
    await fake.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=100,
        thinking_budget_tokens=4096,
        agent="intelligence", purpose="summarizer", envelope_id=None,
    )
    assert fake.calls[0].thinking_budget_tokens == 4096


async def test_fake_provider_defaults_thinking_budget_to_none() -> None:
    fake = FakeProvider(responses=["ok"])
    await fake.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=100,
        agent="intelligence", purpose="summarizer", envelope_id=None,
    )
    assert fake.calls[0].thinking_budget_tokens is None


async def test_anthropic_provider_passes_thinking_to_sdk() -> None:
    provider = AnthropicProvider(api_key="k", model="claude-opus-4-6", usage_store=_usage_store())
    stream_fn = _make_mock_stream(_text_block("hello"))
    provider._client = MagicMock()  # type: ignore[attr-defined]
    provider._client.messages = MagicMock()
    provider._client.messages.stream = stream_fn

    await provider.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=32768,
        thinking_budget_tokens=16384,
        agent="intelligence", purpose="summarizer", envelope_id=None,
    )

    kwargs = stream_fn.call_args.kwargs
    assert kwargs.get("thinking") == {"type": "adaptive", "budget_tokens": 16384}


async def test_anthropic_provider_omits_thinking_when_none() -> None:
    provider = AnthropicProvider(api_key="k", model="claude-opus-4-6", usage_store=_usage_store())
    stream_fn = _make_mock_stream(_text_block("hello"))
    provider._client = MagicMock()  # type: ignore[attr-defined]
    provider._client.messages = MagicMock()
    provider._client.messages.stream = stream_fn

    await provider.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=100,
        agent="intelligence", purpose="summarizer", envelope_id=None,
    )

    kwargs = stream_fn.call_args.kwargs
    assert "thinking" not in kwargs


async def test_anthropic_provider_skips_thinking_blocks_in_response() -> None:
    provider = AnthropicProvider(api_key="k", model="claude-opus-4-6", usage_store=_usage_store())
    stream_fn = _make_mock_stream(
        _thinking_block("internal reasoning"),
        _text_block("final answer"),
    )
    provider._client = MagicMock()  # type: ignore[attr-defined]
    provider._client.messages = MagicMock()
    provider._client.messages.stream = stream_fn

    result = await provider.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=32768,
        thinking_budget_tokens=16384,
        agent="intelligence", purpose="summarizer", envelope_id=None,
    )
    assert result == "final answer"
