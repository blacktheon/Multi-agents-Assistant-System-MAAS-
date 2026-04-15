"""Tests for the `thinking_budget_tokens` kwarg added to LLMProvider.complete (6e)."""

from unittest.mock import AsyncMock, MagicMock

from project0.llm.provider import AnthropicProvider, FakeProvider, Msg


async def test_fake_provider_records_thinking_budget_in_call() -> None:
    fake = FakeProvider(responses=["ok"])
    await fake.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=100,
        thinking_budget_tokens=4096,
    )
    assert fake.calls[0].thinking_budget_tokens == 4096


async def test_fake_provider_defaults_thinking_budget_to_none() -> None:
    fake = FakeProvider(responses=["ok"])
    await fake.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=100,
    )
    assert fake.calls[0].thinking_budget_tokens is None


async def test_anthropic_provider_passes_thinking_to_sdk() -> None:
    provider = AnthropicProvider(api_key="k", model="claude-opus-4-6")
    mock_resp = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "hello"
    mock_resp.content = [text_block]
    mock_create = AsyncMock(return_value=mock_resp)
    provider._client = MagicMock()  # type: ignore[attr-defined]
    provider._client.messages = MagicMock()
    provider._client.messages.create = mock_create

    await provider.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=32768,
        thinking_budget_tokens=16384,
    )

    kwargs = mock_create.call_args.kwargs
    assert kwargs.get("thinking") == {"type": "enabled", "budget_tokens": 16384}


async def test_anthropic_provider_omits_thinking_when_none() -> None:
    provider = AnthropicProvider(api_key="k", model="claude-opus-4-6")
    mock_resp = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "hello"
    mock_resp.content = [text_block]
    mock_create = AsyncMock(return_value=mock_resp)
    provider._client = MagicMock()  # type: ignore[attr-defined]
    provider._client.messages = MagicMock()
    provider._client.messages.create = mock_create

    await provider.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=100,
    )

    kwargs = mock_create.call_args.kwargs
    assert "thinking" not in kwargs


async def test_anthropic_provider_skips_thinking_blocks_in_response() -> None:
    provider = AnthropicProvider(api_key="k", model="claude-opus-4-6")
    mock_resp = MagicMock()
    thinking_block = MagicMock()
    thinking_block.type = "thinking"
    thinking_block.text = "internal reasoning"
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "final answer"
    mock_resp.content = [thinking_block, text_block]
    mock_create = AsyncMock(return_value=mock_resp)
    provider._client = MagicMock()  # type: ignore[attr-defined]
    provider._client.messages = MagicMock()
    provider._client.messages.create = mock_create

    result = await provider.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=32768,
        thinking_budget_tokens=16384,
    )
    assert result == "final answer"
