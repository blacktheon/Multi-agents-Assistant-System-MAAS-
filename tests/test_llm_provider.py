"""Tests for the LLM provider abstraction. No test hits the real Anthropic
API — AnthropicProvider is exercised with a mocked SDK in Task 5."""

from __future__ import annotations

import pytest

from project0.llm.provider import (
    FakeProvider,
    LLMProviderError,
    Msg,
)


@pytest.mark.asyncio
async def test_fake_provider_returns_canned_responses_in_order() -> None:
    p = FakeProvider(responses=["first", "second", "third"])
    assert await p.complete(system="sys", messages=[Msg(role="user", content="a")]) == "first"
    assert await p.complete(system="sys", messages=[Msg(role="user", content="b")]) == "second"
    assert await p.complete(system="sys", messages=[Msg(role="user", content="c")]) == "third"


@pytest.mark.asyncio
async def test_fake_provider_raises_when_out_of_canned_responses() -> None:
    p = FakeProvider(responses=["only"])
    await p.complete(system="sys", messages=[])
    with pytest.raises(LLMProviderError):
        await p.complete(system="sys", messages=[])


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
    )
    assert out == "saw 2 msgs"
    assert captured[0][0] == "PERSONA"
    assert [m.content for m in captured[0][1]] == ["hi", "hey"]


@pytest.mark.asyncio
async def test_fake_provider_records_all_calls() -> None:
    p = FakeProvider(responses=["a", "b"])
    await p.complete(system="S1", messages=[Msg(role="user", content="x")])
    await p.complete(system="S2", messages=[Msg(role="user", content="y")], max_tokens=100)
    assert len(p.calls) == 2
    assert p.calls[0].system == "S1"
    assert p.calls[0].max_tokens == 800  # default
    assert p.calls[1].system == "S2"
    assert p.calls[1].max_tokens == 100
