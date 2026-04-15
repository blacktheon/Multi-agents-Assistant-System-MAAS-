from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from project0.llm.provider import AnthropicProvider, LLMProviderError, Msg
from project0.store import LLMUsageStore, Store


@pytest.fixture
def usage_store() -> LLMUsageStore:
    s = Store(":memory:")
    s.init_schema()
    return LLMUsageStore(s.conn)


def _fake_usage(input_tok: int, create: int, read: int, output: int) -> MagicMock:
    usage = MagicMock()
    usage.input_tokens = input_tok
    usage.cache_creation_input_tokens = create
    usage.cache_read_input_tokens = read
    usage.output_tokens = output
    return usage


def _fake_final_message(text: str, usage: MagicMock) -> MagicMock:
    final = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    final.content = [text_block]
    final.usage = usage
    return final


class _FakeStreamCtx:
    def __init__(self, final: MagicMock) -> None:
        self._final = final

    async def __aenter__(self) -> _FakeStreamCtx:
        return self

    async def __aexit__(self, *a) -> None:
        return None

    async def get_final_message(self) -> MagicMock:
        return self._final


@pytest.mark.asyncio
async def test_complete_records_usage_on_success(usage_store: LLMUsageStore) -> None:
    final = _fake_final_message("ok", _fake_usage(1234, 500, 700, 210))
    provider = AnthropicProvider(
        api_key="test", model="claude-sonnet-4-6",
        usage_store=usage_store, cache_ttl="ephemeral",
    )
    provider._client = MagicMock()
    provider._client.messages = MagicMock()
    provider._client.messages.stream = MagicMock(return_value=_FakeStreamCtx(final))

    text = await provider.complete(
        system="persona", messages=[Msg(role="user", content="hi")],
        agent="secretary", purpose="reply", envelope_id=42,
    )
    assert text == "ok"
    summary = usage_store.summary_since("1970-01-01T00:00:00Z")
    assert len(summary) == 1
    row = summary[0]
    assert row["agent"] == "secretary"
    assert row["input_tokens"] == 1234
    assert row["cache_creation_input_tokens"] == 500
    assert row["cache_read_input_tokens"] == 700
    assert row["output_tokens"] == 210
    assert row["calls"] == 1


@pytest.mark.asyncio
async def test_complete_does_not_record_on_failure(usage_store: LLMUsageStore) -> None:
    provider = AnthropicProvider(
        api_key="test", model="claude-sonnet-4-6",
        usage_store=usage_store, cache_ttl="ephemeral",
    )
    provider._client = MagicMock()
    provider._client.messages = MagicMock()

    def _raise(*a, **kw) -> None:
        raise RuntimeError("boom")

    provider._client.messages.stream = _raise

    with pytest.raises(LLMProviderError):
        await provider.complete(
            system="persona", messages=[Msg(role="user", content="hi")],
            agent="secretary", purpose="reply", envelope_id=42,
        )
    assert usage_store.summary_since("1970-01-01T00:00:00Z") == []


@pytest.mark.asyncio
async def test_complete_requires_agent_and_purpose_kwargs() -> None:
    s = Store(":memory:")
    s.init_schema()
    provider = AnthropicProvider(
        api_key="test", model="claude-sonnet-4-6",
        usage_store=LLMUsageStore(s.conn),
        cache_ttl="ephemeral",
    )
    with pytest.raises(TypeError):
        await provider.complete(  # type: ignore[call-arg]
            system="persona", messages=[Msg(role="user", content="hi")],
        )
