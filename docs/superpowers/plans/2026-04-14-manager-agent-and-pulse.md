# Sub-project 6c — Manager Agent + Pulse Primitive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `manager_stub` with a real LLM tool-use agent backed by `GoogleCalendar`, delegating to Secretary/Intelligence through the existing `AgentResult` contract, and introduce a generic pulse primitive (per-agent TOML config + orchestrator dispatcher + scheduler tasks) that wakes agents on an interval with an opaque payload.

**Architecture:** Bottom-up: envelope/tool types → provider extension → pulse primitive → orchestrator entry point → Manager agent → composition root wiring. The orchestrator stays domain-agnostic; calendar/reminder logic lives entirely in Manager. Per-turn state for the agentic loop is local to `_agentic_loop`, not on `self`, so concurrent turns are safe.

**Tech Stack:** Python 3.12, asyncio, anthropic SDK, google-api-python-client (already present), pytest, tomllib.

**Spec:** `docs/superpowers/specs/2026-04-14-manager-agent-and-pulse-design.md`

---

## File Structure

**New files:**
- `src/project0/llm/tools.py` — `ToolSpec`, `ToolCall`, `ToolUseResult`, `AssistantToolUseMsg`, `ToolResultMsg`
- `src/project0/pulse.py` — `PulseEntry`, `load_pulse_entries`, `build_pulse_envelope`, `run_pulse_loop`
- `src/project0/agents/manager.py` — full rewrite replacing `manager_stub`
- `prompts/manager.md`, `prompts/manager.toml`
- `tests/llm/test_tools_fake.py`
- `tests/llm/test_anthropic_tool_translation.py`
- `tests/pulse/test_pulse_loader.py`
- `tests/pulse/test_pulse_scheduler.py`
- `tests/orchestrator/test_pulse_dispatch.py`
- `tests/agents/test_manager_persona_load.py`
- `tests/agents/test_manager_tool_loop.py`

**Modified files:**
- `src/project0/envelope.py` — add `"pulse"` to `Source` and `RoutingReason`; add `delegation_payload` to `AgentResult`; relax invariant so a delegation may carry an accompanying reply (or we suppress the text when delegating — see Task 1)
- `src/project0/llm/provider.py` — extend `LLMProvider` Protocol, `FakeProvider`, `AnthropicProvider`
- `src/project0/orchestrator.py` — add `handle_pulse`; pass `delegation_payload` into the internal forward envelope
- `src/project0/agents/registry.py` — add `register_manager`; remove `manager_stub` import (Task 13 handles this, after Manager is fully functional so intermediate tasks still import cleanly)
- `src/project0/config.py` — add `user_tz`, `google_calendar_id`, `google_token_path`, `google_client_secrets_path` to `Settings`
- `src/project0/main.py` — construct `GoogleCalendar`, `Manager`, register it, start pulse loops
- `.env.example` — document `MANAGER_PULSE_CHAT_ID`

---

## Task 1: Envelope and AgentResult additions

**Files:**
- Modify: `src/project0/envelope.py`
- Test: `tests/test_envelope_pulse.py`

**Why this is first:** Every subsequent task depends on `"pulse"` being a valid source/routing_reason and on `AgentResult.delegation_payload` existing.

**Design note (invariant):** The current `AgentResult` forbids `reply_text` and `delegate_to` being set together. Manager's agentic loop, when the model emits final text *after* a delegation tool, should **suppress the trailing text** and return as a pure delegation. The persona instructs the model to stop after delegating; if it emits epilogue text we drop it (the visible `handoff_text` is the user-facing message anyway). This keeps the existing invariant untouched — only the new `delegation_payload` field is added.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_envelope_pulse.py
from project0.envelope import AgentResult, Envelope


def test_envelope_accepts_pulse_source_and_routing_reason():
    env = Envelope(
        id=None,
        ts="2026-04-14T00:00:00Z",
        parent_id=None,
        source="pulse",
        telegram_chat_id=None,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="system",
        from_agent=None,
        to_agent="manager",
        body="check_calendar",
        mentions=[],
        routing_reason="pulse",
        payload={"pulse_name": "check_calendar"},
    )
    assert env.routing_reason == "pulse"
    assert env.source == "pulse"
    # Round-trip JSON.
    assert Envelope.from_json(env.to_json()).routing_reason == "pulse"


def test_agent_result_delegation_carries_payload():
    r = AgentResult(
        reply_text=None,
        delegate_to="secretary",
        handoff_text="→ 已让秘书帮你记着",
        delegation_payload={"kind": "reminder_request", "appointment": "牙医"},
    )
    assert r.is_delegation()
    assert r.delegation_payload == {
        "kind": "reminder_request",
        "appointment": "牙医",
    }


def test_agent_result_reply_default_payload_is_none():
    r = AgentResult(
        reply_text="hi", delegate_to=None, handoff_text=None
    )
    assert r.delegation_payload is None
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_envelope_pulse.py -v
```

Expected: FAIL — `"pulse"` is not in the literal, `AgentResult` has no `delegation_payload`.

- [ ] **Step 3: Modify `envelope.py`**

Replace the `Source` and `RoutingReason` literals and the `AgentResult` dataclass:

```python
Source = Literal["telegram_group", "telegram_dm", "internal", "pulse"]
FromKind = Literal["user", "agent", "system"]
RoutingReason = Literal[
    "direct_dm",
    "mention",
    "focus",
    "default_manager",
    "manager_delegation",
    "outbound_reply",
    "listener_observation",
    "pulse",
]
```

```python
@dataclass
class AgentResult:
    reply_text: str | None
    delegate_to: str | None
    handoff_text: str | None
    delegation_payload: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        has_reply = self.reply_text is not None
        has_delegate = self.delegate_to is not None
        if has_reply == has_delegate:
            raise RoutingError(
                "AgentResult must set exactly one of reply_text or delegate_to; "
                f"got reply_text={self.reply_text!r}, delegate_to={self.delegate_to!r}"
            )
        if has_delegate and not self.handoff_text:
            raise RoutingError(
                "AgentResult with delegate_to must also set handoff_text"
            )
        if self.delegation_payload is not None and not has_delegate:
            raise RoutingError(
                "AgentResult.delegation_payload set without delegate_to"
            )

    def is_reply(self) -> bool:
        return self.reply_text is not None

    def is_delegation(self) -> bool:
        return self.delegate_to is not None
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_envelope_pulse.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Run the full existing suite to verify no regressions**

```
uv run pytest -q
```

Expected: all existing tests still pass (the new `delegation_payload` defaults to `None`, preserving every current call site).

- [ ] **Step 6: Commit**

```
git add src/project0/envelope.py tests/test_envelope_pulse.py
git commit -m "feat(envelope): add pulse source/routing_reason and AgentResult.delegation_payload"
```

---

## Task 2: Shared LLM tool types

**Files:**
- Create: `src/project0/llm/tools.py`
- Test: `tests/llm/test_tool_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_tool_types.py
from project0.llm.tools import (
    AssistantToolUseMsg,
    ToolCall,
    ToolResultMsg,
    ToolSpec,
    ToolUseResult,
)


def test_tool_spec_is_hashable_frozen_dataclass():
    spec = ToolSpec(
        name="calendar_list_events",
        description="List events",
        input_schema={"type": "object", "properties": {}},
    )
    assert spec.name == "calendar_list_events"


def test_tool_call_fields():
    call = ToolCall(id="toolu_1", name="foo", input={"x": 1})
    assert call.id == "toolu_1"
    assert call.input == {"x": 1}


def test_tool_use_result_text_variant():
    r = ToolUseResult(kind="text", text="done", tool_calls=[], stop_reason="end_turn")
    assert r.kind == "text"
    assert r.text == "done"
    assert r.tool_calls == []


def test_tool_use_result_tool_use_variant():
    r = ToolUseResult(
        kind="tool_use",
        text=None,
        tool_calls=[ToolCall(id="toolu_1", name="foo", input={})],
        stop_reason="tool_use",
    )
    assert r.kind == "tool_use"
    assert len(r.tool_calls) == 1


def test_assistant_tool_use_msg_and_tool_result_msg():
    assistant = AssistantToolUseMsg(
        tool_calls=[ToolCall(id="a", name="foo", input={})],
        text="thinking...",
    )
    result = ToolResultMsg(tool_use_id="a", content="42", is_error=False)
    assert assistant.text == "thinking..."
    assert result.is_error is False
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/llm/test_tool_types.py -v
```

Expected: FAIL — `ModuleNotFoundError: project0.llm.tools`.

- [ ] **Step 3: Create `src/project0/llm/tools.py`**

```python
"""Shared types for LLM tool-use conversations.

These types are imported by both the provider layer (which translates them
to/from the Anthropic SDK's wire format) and by agents that use tools
(Manager in 6c). Keeping them in a dedicated module avoids a circular
import between agents and the provider module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ToolSpec:
    """One tool advertised to the model. ``input_schema`` is a JSONSchema
    dict, passed straight through to Anthropic's ``tools`` parameter."""
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """One tool_use block emitted by the model. ``id`` is the Anthropic
    tool_use id — required for tool_result pairing on the next turn."""
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolUseResult:
    """One completion from ``complete_with_tools``. Either the model
    emitted final text (``kind='text'``) or it requested tool calls
    (``kind='tool_use'``). ``text`` may be set in the tool_use variant
    too, carrying optional assistant preamble text."""
    kind: Literal["text", "tool_use"]
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None


@dataclass
class AssistantToolUseMsg:
    """An assistant turn that called tools. Used when feeding the turn
    back into a follow-up ``complete_with_tools`` call."""
    tool_calls: list[ToolCall]
    text: str | None = None


@dataclass
class ToolResultMsg:
    """A tool_result turn fed back to the model after executing a tool."""
    tool_use_id: str
    content: str
    is_error: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/llm/test_tool_types.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: Commit**

```
git add src/project0/llm/tools.py tests/llm/test_tool_types.py
git commit -m "feat(llm): shared tool-use types (ToolSpec/ToolCall/ToolUseResult)"
```

---

## Task 3: Extend `LLMProvider` Protocol and `FakeProvider`

**Files:**
- Modify: `src/project0/llm/provider.py`
- Test: `tests/llm/test_tools_fake.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_tools_fake.py
import pytest

from project0.llm.provider import FakeProvider, LLMProviderError, Msg
from project0.llm.tools import (
    AssistantToolUseMsg,
    ToolCall,
    ToolResultMsg,
    ToolSpec,
    ToolUseResult,
)

_TOOLS = [
    ToolSpec(name="foo", description="foo", input_schema={"type": "object"}),
]


@pytest.mark.asyncio
async def test_fake_provider_replays_tool_use_script():
    fake = FakeProvider(
        tool_responses=[
            ToolUseResult(
                kind="tool_use",
                text=None,
                tool_calls=[ToolCall(id="toolu_1", name="foo", input={"x": 1})],
                stop_reason="tool_use",
            ),
            ToolUseResult(
                kind="text", text="all done", tool_calls=[], stop_reason="end_turn"
            ),
        ]
    )

    first = await fake.complete_with_tools(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        tools=_TOOLS,
    )
    assert first.kind == "tool_use"
    assert first.tool_calls[0].id == "toolu_1"

    second = await fake.complete_with_tools(
        system="sys",
        messages=[
            Msg(role="user", content="hi"),
            AssistantToolUseMsg(
                tool_calls=[ToolCall(id="toolu_1", name="foo", input={"x": 1})]
            ),
            ToolResultMsg(tool_use_id="toolu_1", content="42"),
        ],
        tools=_TOOLS,
    )
    assert second.kind == "text"
    assert second.text == "all done"

    assert len(fake.tool_calls_log) == 2


@pytest.mark.asyncio
async def test_fake_provider_exhausted_tool_script_raises():
    fake = FakeProvider(tool_responses=[])
    with pytest.raises(LLMProviderError, match="tool_responses"):
        await fake.complete_with_tools(
            system="sys",
            messages=[Msg(role="user", content="hi")],
            tools=_TOOLS,
        )
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/llm/test_tools_fake.py -v
```

Expected: FAIL — `FakeProvider` has no `complete_with_tools`.

- [ ] **Step 3: Extend `provider.py`**

Add imports near the top (alongside existing imports):

```python
from project0.llm.tools import (
    AssistantToolUseMsg,
    ToolCall,
    ToolResultMsg,
    ToolSpec,
    ToolUseResult,
)
```

Replace the `LLMProvider` Protocol:

```python
class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
    ) -> str:
        ...

    async def complete_with_tools(
        self,
        *,
        system: str,
        messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
    ) -> ToolUseResult:
        ...
```

Extend `FakeProvider` — add a new field and method:

```python
@dataclass
class FakeProvider:
    responses: list[str] | None = None
    callable_: Callable[[str, list[Msg]], str] | None = None
    calls: list[ProviderCall] = field(default_factory=list)
    tool_responses: list[ToolUseResult] | None = None
    tool_calls_log: list[dict] = field(default_factory=list)
    _idx: int = 0
    _tool_idx: int = 0

    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
    ) -> str:
        self.calls.append(
            ProviderCall(system=system, messages=list(messages), max_tokens=max_tokens)
        )
        if self.callable_ is not None:
            return self.callable_(system, messages)
        if self.responses is None:
            raise LLMProviderError("FakeProvider has neither responses nor callable_")
        if self._idx >= len(self.responses):
            raise LLMProviderError(
                f"FakeProvider exhausted: {len(self.responses)} canned responses, "
                f"{self._idx + 1} requested"
            )
        out = self.responses[self._idx]
        self._idx += 1
        return out

    async def complete_with_tools(
        self,
        *,
        system: str,
        messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
    ) -> ToolUseResult:
        self.tool_calls_log.append(
            {
                "system": system,
                "messages": list(messages),
                "tools": list(tools),
                "max_tokens": max_tokens,
            }
        )
        if self.tool_responses is None:
            raise LLMProviderError(
                "FakeProvider.complete_with_tools called but tool_responses is None"
            )
        if self._tool_idx >= len(self.tool_responses):
            raise LLMProviderError(
                f"FakeProvider.complete_with_tools exhausted: "
                f"{len(self.tool_responses)} canned tool_responses, "
                f"{self._tool_idx + 1} requested"
            )
        out = self.tool_responses[self._tool_idx]
        self._tool_idx += 1
        return out
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/llm/test_tools_fake.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Run existing provider tests**

```
uv run pytest tests/llm -v
```

Expected: all pass. Existing `complete()` callers are unaffected.

- [ ] **Step 6: Commit**

```
git add src/project0/llm/provider.py tests/llm/test_tools_fake.py
git commit -m "feat(llm): LLMProvider.complete_with_tools + FakeProvider tool scripting"
```

---

## Task 4: `AnthropicProvider.complete_with_tools`

**Files:**
- Modify: `src/project0/llm/provider.py`
- Test: `tests/llm/test_anthropic_tool_translation.py`

**Design note:** We unit-test the translation layer only, not a live Anthropic call. The test patches `self._client.messages.create` to a mock and asserts on the SDK payload we construct.

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_anthropic_tool_translation.py
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from project0.llm.provider import AnthropicProvider, LLMProviderError, Msg
from project0.llm.tools import (
    AssistantToolUseMsg,
    ToolCall,
    ToolResultMsg,
    ToolSpec,
)


def _fake_response_tool_use():
    # Mimics Anthropic SDK response object shape enough for the translator.
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="text", text="let me check"),
            SimpleNamespace(
                type="tool_use",
                id="toolu_1",
                name="calendar_list_events",
                input={"time_min": "2026-04-14T00:00:00Z"},
            ),
        ],
    )


def _fake_response_text():
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="all good")],
    )


@pytest.mark.asyncio
async def test_anthropic_translates_messages_and_tools():
    provider = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6")
    mock_create = AsyncMock(return_value=_fake_response_tool_use())
    provider._client.messages.create = mock_create  # type: ignore[method-assign]

    tools = [
        ToolSpec(
            name="calendar_list_events",
            description="List events",
            input_schema={"type": "object", "properties": {}},
        )
    ]
    messages = [
        Msg(role="user", content="check my day"),
        AssistantToolUseMsg(
            tool_calls=[ToolCall(id="toolu_old", name="noop", input={})],
            text="checking...",
        ),
        ToolResultMsg(tool_use_id="toolu_old", content="ok"),
    ]
    result = await provider.complete_with_tools(
        system="你是经理",
        messages=messages,
        tools=tools,
        max_tokens=512,
    )

    assert result.kind == "tool_use"
    assert result.text == "let me check"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "calendar_list_events"
    assert result.tool_calls[0].id == "toolu_1"

    # Inspect the SDK payload we constructed.
    kwargs = mock_create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["max_tokens"] == 512
    assert kwargs["tools"][0]["name"] == "calendar_list_events"
    # System block uses prompt caching.
    assert kwargs["system"][0]["cache_control"]["type"] == "ephemeral"
    sdk_messages = kwargs["messages"]
    assert sdk_messages[0] == {"role": "user", "content": "check my day"}
    # Assistant turn with tool_use was translated to a list-of-blocks form.
    assert sdk_messages[1]["role"] == "assistant"
    blocks = sdk_messages[1]["content"]
    assert any(b.get("type") == "text" and b.get("text") == "checking..." for b in blocks)
    assert any(b.get("type") == "tool_use" and b.get("id") == "toolu_old" for b in blocks)
    # tool_result turn → user role with tool_result block.
    assert sdk_messages[2]["role"] == "user"
    tr = sdk_messages[2]["content"][0]
    assert tr["type"] == "tool_result"
    assert tr["tool_use_id"] == "toolu_old"
    assert tr["content"] == "ok"
    assert tr.get("is_error", False) is False


@pytest.mark.asyncio
async def test_anthropic_returns_text_variant_on_end_turn():
    provider = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6")
    provider._client.messages.create = AsyncMock(return_value=_fake_response_text())  # type: ignore[method-assign]

    result = await provider.complete_with_tools(
        system="s", messages=[Msg(role="user", content="hi")], tools=[]
    )
    assert result.kind == "text"
    assert result.text == "all good"
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_anthropic_wraps_sdk_errors():
    provider = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6")
    provider._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("boom")
    )
    with pytest.raises(LLMProviderError):
        await provider.complete_with_tools(
            system="s", messages=[Msg(role="user", content="hi")], tools=[]
        )
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/llm/test_anthropic_tool_translation.py -v
```

Expected: FAIL — method not implemented.

- [ ] **Step 3: Implement `AnthropicProvider.complete_with_tools`**

Add to the `AnthropicProvider` class in `provider.py`:

```python
    async def complete_with_tools(
        self,
        *,
        system: str,
        messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
    ) -> ToolUseResult:
        sdk_messages: list[dict[str, Any]] = []
        for m in messages:
            if isinstance(m, Msg):
                sdk_messages.append({"role": m.role, "content": m.content})
            elif isinstance(m, AssistantToolUseMsg):
                blocks: list[dict[str, Any]] = []
                if m.text:
                    blocks.append({"type": "text", "text": m.text})
                for tc in m.tool_calls:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.input,
                    })
                sdk_messages.append({"role": "assistant", "content": blocks})
            elif isinstance(m, ToolResultMsg):
                tr_block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": m.tool_use_id,
                    "content": m.content,
                }
                if m.is_error:
                    tr_block["is_error"] = True
                sdk_messages.append({"role": "user", "content": [tr_block]})
            else:
                raise LLMProviderError(f"unknown message variant: {type(m).__name__}")

        sdk_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

        system_block = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system_block,
                messages=sdk_messages,
                tools=sdk_tools,
            )
        except Exception as e:
            log.exception("anthropic tool-use call failed")
            raise LLMProviderError(f"anthropic {type(e).__name__}") from e

        text_preamble: str | None = None
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                if text_preamble is None:
                    text_preamble = getattr(block, "text", None)
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=getattr(block, "id"),
                        name=getattr(block, "name"),
                        input=dict(getattr(block, "input", {}) or {}),
                    )
                )

        stop_reason = getattr(resp, "stop_reason", None)
        if tool_calls:
            return ToolUseResult(
                kind="tool_use",
                text=text_preamble,
                tool_calls=tool_calls,
                stop_reason=stop_reason,
            )
        if text_preamble is None:
            raise LLMProviderError("anthropic response contained no text or tool_use block")
        return ToolUseResult(
            kind="text",
            text=text_preamble,
            tool_calls=[],
            stop_reason=stop_reason,
        )
```

(You may need to add `Any` to the existing `typing` import if not already present.)

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/llm/test_anthropic_tool_translation.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```
git add src/project0/llm/provider.py tests/llm/test_anthropic_tool_translation.py
git commit -m "feat(llm): AnthropicProvider.complete_with_tools translator"
```

---

## Task 5: Pulse loader

**Files:**
- Create: `src/project0/pulse.py`
- Test: `tests/pulse/test_pulse_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pulse/test_pulse_loader.py
from pathlib import Path

import pytest

from project0.pulse import PulseEntry, load_pulse_entries


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agent.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_no_pulse_array_is_empty_list(tmp_path):
    p = _write(tmp_path, "[llm]\nmodel = 'x'\n")
    assert load_pulse_entries(p) == []


def test_valid_entries_parse(tmp_path, monkeypatch):
    monkeypatch.setenv("MANAGER_PULSE_CHAT_ID", "12345")
    p = _write(
        tmp_path,
        """
[[pulse]]
name = "check_calendar"
every_seconds = 300
chat_id_env = "MANAGER_PULSE_CHAT_ID"
payload = { window_minutes = 60 }

[[pulse]]
name = "nightly"
every_seconds = 3600
payload = { note = "sleep" }
""",
    )
    entries = load_pulse_entries(p)
    assert len(entries) == 2
    assert entries[0] == PulseEntry(
        name="check_calendar",
        every_seconds=300,
        chat_id=12345,
        payload={"window_minutes": 60},
    )
    assert entries[1].chat_id is None
    assert entries[1].payload == {"note": "sleep"}


def test_missing_env_var_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("MANAGER_PULSE_CHAT_ID", raising=False)
    p = _write(
        tmp_path,
        """
[[pulse]]
name = "check_calendar"
every_seconds = 300
chat_id_env = "MANAGER_PULSE_CHAT_ID"
""",
    )
    with pytest.raises(RuntimeError, match="MANAGER_PULSE_CHAT_ID"):
        load_pulse_entries(p)


def test_non_int_env_var_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("BAD_CHAT", "not-a-number")
    p = _write(
        tmp_path,
        """
[[pulse]]
name = "check_calendar"
every_seconds = 300
chat_id_env = "BAD_CHAT"
""",
    )
    with pytest.raises(RuntimeError, match="BAD_CHAT"):
        load_pulse_entries(p)


def test_every_seconds_too_small_raises(tmp_path):
    p = _write(
        tmp_path,
        """
[[pulse]]
name = "too_fast"
every_seconds = 5
""",
    )
    with pytest.raises(RuntimeError, match="every_seconds"):
        load_pulse_entries(p)


def test_duplicate_name_raises(tmp_path):
    p = _write(
        tmp_path,
        """
[[pulse]]
name = "check_calendar"
every_seconds = 300

[[pulse]]
name = "check_calendar"
every_seconds = 600
""",
    )
    with pytest.raises(RuntimeError, match="duplicate"):
        load_pulse_entries(p)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/pulse/test_pulse_loader.py -v
```

Expected: FAIL — `ModuleNotFoundError: project0.pulse`.

- [ ] **Step 3: Create `src/project0/pulse.py` (loader only for now)**

```python
"""Pulse primitive: scheduled wake-up envelopes for agents.

A pulse is a generic, domain-agnostic trigger. Each agent's TOML config
file may declare one or more ``[[pulse]]`` entries; the orchestrator
runs one scheduler task per entry, and each tick dispatches an Envelope
with ``source='pulse'`` and ``routing_reason='pulse'`` to the named
agent. The payload dict is pass-through — the orchestrator does not
interpret it. Domain logic (e.g. 'is there a calendar event soon')
lives entirely inside the target agent.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from project0.envelope import Envelope

if TYPE_CHECKING:
    from project0.orchestrator import Orchestrator

log = logging.getLogger(__name__)

_MIN_EVERY_SECONDS = 10


@dataclass(frozen=True)
class PulseEntry:
    name: str
    every_seconds: int
    chat_id: int | None
    payload: dict[str, Any] = field(default_factory=dict)


def load_pulse_entries(toml_path: Path) -> list[PulseEntry]:
    """Parse ``[[pulse]]`` entries from the given TOML file.

    Missing ``[[pulse]]`` array → empty list (valid).
    ``chat_id_env`` missing from os.environ → RuntimeError.
    ``every_seconds < 10`` → RuntimeError.
    Duplicate ``name`` → RuntimeError.
    """
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    raw_entries = data.get("pulse", [])
    if not isinstance(raw_entries, list):
        raise RuntimeError(f"{toml_path}: [[pulse]] must be an array of tables")

    entries: list[PulseEntry] = []
    seen: set[str] = set()
    for idx, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise RuntimeError(f"{toml_path}: pulse entry #{idx} is not a table")
        try:
            name = str(raw["name"])
            every = int(raw["every_seconds"])
        except KeyError as e:
            raise RuntimeError(
                f"{toml_path}: pulse entry #{idx} missing required key {e.args[0]!r}"
            ) from e

        if not name:
            raise RuntimeError(f"{toml_path}: pulse entry #{idx} has empty name")
        if name in seen:
            raise RuntimeError(f"{toml_path}: duplicate pulse name {name!r}")
        seen.add(name)

        if every < _MIN_EVERY_SECONDS:
            raise RuntimeError(
                f"{toml_path}: pulse {name!r} every_seconds={every} is below "
                f"floor {_MIN_EVERY_SECONDS}"
            )

        chat_id: int | None = None
        chat_id_env = raw.get("chat_id_env")
        if chat_id_env is not None:
            env_name = str(chat_id_env)
            raw_val = os.environ.get(env_name)
            if raw_val is None or not raw_val.strip():
                raise RuntimeError(
                    f"{toml_path}: pulse {name!r} references chat_id_env="
                    f"{env_name!r} but the env var is missing or empty"
                )
            try:
                chat_id = int(raw_val.strip())
            except ValueError as e:
                raise RuntimeError(
                    f"{toml_path}: pulse {name!r} env var {env_name}="
                    f"{raw_val!r} is not an integer"
                ) from e

        payload = raw.get("payload", {}) or {}
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"{toml_path}: pulse {name!r} payload must be a table"
            )

        entries.append(
            PulseEntry(
                name=name,
                every_seconds=every,
                chat_id=chat_id,
                payload=dict(payload),
            )
        )

    return entries
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/pulse/test_pulse_loader.py -v
```

Expected: 6 PASS.

- [ ] **Step 5: Commit**

```
git add src/project0/pulse.py tests/pulse/test_pulse_loader.py
git commit -m "feat(pulse): PulseEntry + TOML loader with env-var chat_id resolution"
```

---

## Task 6: Pulse envelope builder + scheduler loop

**Files:**
- Modify: `src/project0/pulse.py`
- Test: `tests/pulse/test_pulse_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pulse/test_pulse_scheduler.py
import asyncio

import pytest

from project0.envelope import Envelope
from project0.pulse import PulseEntry, build_pulse_envelope, run_pulse_loop


def test_build_pulse_envelope_shape():
    entry = PulseEntry(
        name="check_calendar",
        every_seconds=300,
        chat_id=42,
        payload={"window_minutes": 60},
    )
    env = build_pulse_envelope(entry, target_agent="manager")
    assert env.source == "pulse"
    assert env.routing_reason == "pulse"
    assert env.from_kind == "system"
    assert env.to_agent == "manager"
    assert env.telegram_chat_id == 42
    assert env.telegram_msg_id is None
    assert env.body == "check_calendar"
    assert env.payload == {
        "pulse_name": "check_calendar",
        "window_minutes": 60,
    }


def test_build_pulse_envelope_unbound_chat():
    entry = PulseEntry(name="p", every_seconds=60, chat_id=None, payload={})
    env = build_pulse_envelope(entry, target_agent="manager")
    assert env.telegram_chat_id is None


class _FakeOrch:
    def __init__(self):
        self.calls: list[Envelope] = []
        self.raise_next = False

    async def handle_pulse(self, env: Envelope) -> None:
        self.calls.append(env)
        if self.raise_next:
            self.raise_next = False
            raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_run_pulse_loop_fires_and_survives_errors(monkeypatch):
    entry = PulseEntry(name="p", every_seconds=60, chat_id=None, payload={})
    orch = _FakeOrch()

    # Patch asyncio.sleep to return immediately, but cap iterations.
    tick_count = {"n": 0}
    real_sleep = asyncio.sleep

    async def fast_sleep(_):
        tick_count["n"] += 1
        if tick_count["n"] >= 3:
            # Signal cancellation on the 3rd tick by raising CancelledError.
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr("project0.pulse.asyncio.sleep", fast_sleep)

    orch.raise_next = True  # first handle_pulse should raise; loop must survive.

    task = asyncio.create_task(
        run_pulse_loop(entry=entry, target_agent="manager", orchestrator=orch)
    )
    with pytest.raises(asyncio.CancelledError):
        await task

    # First tick fires only after the first sleep; then the second tick fires.
    # Tick 1 raises inside handle_pulse → swallowed → loop continues.
    # Tick 2 succeeds → handle_pulse called again.
    # Tick 3 → sleep raises CancelledError → loop exits.
    assert len(orch.calls) == 2
    assert all(e.source == "pulse" for e in orch.calls)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/pulse/test_pulse_scheduler.py -v
```

Expected: FAIL — `build_pulse_envelope` / `run_pulse_loop` not defined.

- [ ] **Step 3: Extend `src/project0/pulse.py`**

Append to the module:

```python
def build_pulse_envelope(entry: PulseEntry, *, target_agent: str) -> Envelope:
    """Construct the Envelope the scheduler enqueues for one pulse tick."""
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload: dict[str, Any] = {"pulse_name": entry.name, **entry.payload}
    return Envelope(
        id=None,
        ts=now,
        parent_id=None,
        source="pulse",
        telegram_chat_id=entry.chat_id,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="system",
        from_agent=None,
        to_agent=target_agent,
        body=entry.name,
        mentions=[],
        routing_reason="pulse",
        payload=payload,
    )


async def run_pulse_loop(
    *,
    entry: PulseEntry,
    target_agent: str,
    orchestrator: "Orchestrator",
) -> None:
    """Infinite scheduler loop for one pulse entry.

    Sleeps first, fires after. Exceptions from ``handle_pulse`` are logged
    and swallowed so a single bad tick cannot kill future ticks. Cancels
    propagate so the TaskGroup can shut us down cleanly.
    """
    log.info(
        "pulse loop starting: name=%s target=%s every=%ss",
        entry.name, target_agent, entry.every_seconds,
    )
    while True:
        try:
            await asyncio.sleep(entry.every_seconds)
        except asyncio.CancelledError:
            log.info("pulse loop cancelled: %s", entry.name)
            raise
        env = build_pulse_envelope(entry, target_agent=target_agent)
        try:
            await orchestrator.handle_pulse(env)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("pulse %s: handle_pulse raised; continuing loop", entry.name)
```

Note: `build_pulse_envelope` uses `target_agent` as a keyword argument for clarity at the call site. Add `tests/pulse/__init__.py` (empty) if the test layout requires it.

- [ ] **Step 4: Create test package init if needed**

```
mkdir -p tests/pulse && [ -f tests/pulse/__init__.py ] || : > tests/pulse/__init__.py
```

- [ ] **Step 5: Run test to verify it passes**

```
uv run pytest tests/pulse/test_pulse_scheduler.py -v
```

Expected: 3 PASS.

- [ ] **Step 6: Commit**

```
git add src/project0/pulse.py tests/pulse/test_pulse_scheduler.py tests/pulse/__init__.py
git commit -m "feat(pulse): envelope builder + run_pulse_loop scheduler"
```

---

## Task 7: `Settings` fields for Google Calendar

**Files:**
- Modify: `src/project0/config.py`
- Test: `tests/test_config.py` (or new file if that doesn't exist)

**Context:** 6b landed the `GoogleCalendar` client + `calendar_smoke.py` but did not wire any env vars into `Settings`. 6c is the first consumer inside the main process, so this is where the wiring lands. Paths default to the same locations the smoke script uses.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_calendar.py
import pytest

from project0.config import load_settings


@pytest.fixture
def base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_MANAGER", "t1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_INTELLIGENCE", "t2")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_SECRETARY", "t3")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "2")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-foo")
    monkeypatch.setenv("STORE_PATH", str(tmp_path / "store.db"))
    return monkeypatch


def test_user_tz_required(base_env):
    base_env.delenv("USER_TIMEZONE", raising=False)
    with pytest.raises(RuntimeError, match="USER_TIMEZONE"):
        load_settings()


def test_user_tz_invalid(base_env):
    base_env.setenv("USER_TIMEZONE", "Not/A/Zone")
    with pytest.raises(RuntimeError, match="USER_TIMEZONE"):
        load_settings()


def test_calendar_defaults(base_env):
    base_env.setenv("USER_TIMEZONE", "Asia/Shanghai")
    s = load_settings()
    assert s.user_tz.key == "Asia/Shanghai"
    assert s.google_calendar_id == "primary"
    assert s.google_token_path.name == "google_token.json"
    assert s.google_client_secrets_path.name == "google_client_secrets.json"


def test_calendar_overrides(base_env, tmp_path):
    base_env.setenv("USER_TIMEZONE", "UTC")
    base_env.setenv("GOOGLE_CALENDAR_ID", "work@example.com")
    base_env.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "t.json"))
    base_env.setenv("GOOGLE_CLIENT_SECRETS_PATH", str(tmp_path / "c.json"))
    s = load_settings()
    assert s.google_calendar_id == "work@example.com"
    assert s.google_token_path == tmp_path / "t.json"
    assert s.google_client_secrets_path == tmp_path / "c.json"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_config_calendar.py -v
```

Expected: FAIL — `Settings` has no such fields.

- [ ] **Step 3: Extend `config.py`**

Add imports:

```python
from pathlib import Path
from zoneinfo import ZoneInfo
```

Extend `Settings`:

```python
@dataclass(frozen=True)
class Settings:
    bot_tokens: dict[str, str]
    allowed_chat_ids: frozenset[int]
    allowed_user_ids: frozenset[int]
    anthropic_api_key: str
    store_path: str
    log_level: str
    user_tz: ZoneInfo
    google_calendar_id: str
    google_token_path: Path
    google_client_secrets_path: Path
```

At the end of `load_settings`, before `return Settings(...)`:

```python
    user_tz_name = os.environ.get("USER_TIMEZONE", "").strip()
    if not user_tz_name:
        raise RuntimeError("USER_TIMEZONE is required but was empty or unset")
    try:
        user_tz = ZoneInfo(user_tz_name)
    except Exception as e:
        raise RuntimeError(
            f"USER_TIMEZONE={user_tz_name!r} is not a valid zoneinfo name: {e}"
        ) from e

    google_calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "").strip() or "primary"
    google_token_path = Path(
        os.environ.get("GOOGLE_TOKEN_PATH", "").strip() or "data/google_token.json"
    )
    google_client_secrets_path = Path(
        os.environ.get("GOOGLE_CLIENT_SECRETS_PATH", "").strip()
        or "data/google_client_secrets.json"
    )
```

And pass them into the `Settings(...)` constructor.

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_config_calendar.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Run full suite**

```
uv run pytest -q
```

Expected: all green. Existing tests that construct `Settings` directly may break — if so, they need `user_tz=ZoneInfo("UTC")` and path fields added. Fix them as needed by searching:

```
grep -rn "Settings(" tests/
```

Add the new fields to any direct constructor call. Use `ZoneInfo("UTC")` and `Path("/tmp/x")` for paths that tests don't actually touch.

- [ ] **Step 6: Commit**

```
git add src/project0/config.py tests/test_config_calendar.py tests/
git commit -m "feat(config): add user_tz and google calendar settings"
```

---

## Task 8: `Orchestrator.handle_pulse` + delegation payload plumbing

**Files:**
- Modify: `src/project0/orchestrator.py`
- Test: `tests/orchestrator/test_pulse_dispatch.py`

**Design note:** `handle_pulse` is parallel to `handle(update)`. It persists the pulse envelope, dispatches the target agent, and reuses `_emit_reply` + the existing delegation block. The only change to the delegation block is that the internal forward envelope now carries `result.delegation_payload` so Secretary's `reminder_request` handler sees its expected payload.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestrator/test_pulse_dispatch.py
import asyncio
from pathlib import Path

import pytest

from project0.agents.registry import AGENT_REGISTRY
from project0.envelope import AgentResult, Envelope
from project0.orchestrator import Orchestrator
from project0.store import Store
from project0.telegram_io import FakeBotSender


@pytest.fixture
def orch(tmp_path):
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    o = Orchestrator(
        store=store,
        sender=FakeBotSender(),
        allowed_chat_ids=frozenset({999}),
        allowed_user_ids=frozenset({1}),
    )
    return o


def _pulse_env(chat_id=None):
    return Envelope(
        id=None,
        ts="2026-04-14T00:00:00Z",
        parent_id=None,
        source="pulse",
        telegram_chat_id=chat_id,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="system",
        from_agent=None,
        to_agent="manager",
        body="check_calendar",
        mentions=[],
        routing_reason="pulse",
        payload={"pulse_name": "check_calendar", "window_minutes": 60},
    )


@pytest.mark.asyncio
async def test_pulse_dispatch_text_reply(orch, monkeypatch):
    async def fake_manager(env: Envelope) -> AgentResult:
        assert env.source == "pulse"
        assert env.payload["pulse_name"] == "check_calendar"
        return AgentResult(reply_text="nothing urgent", delegate_to=None, handoff_text=None)

    monkeypatch.setitem(AGENT_REGISTRY, "manager", fake_manager)

    await orch.handle_pulse(_pulse_env(chat_id=None))

    # Pulse envelope and the internal reply envelope are both persisted.
    # No telegram send happens because telegram_chat_id is None.
    rows = orch.store.messages().recent_for_chat(chat_id=0, limit=10)
    # chat_id=None rows aren't queryable by chat — check via a direct SQL fetch:
    cur = orch.store._conn.execute("SELECT envelope_json FROM messages ORDER BY id")
    all_rows = [r[0] for r in cur.fetchall()]
    assert any('"source":"pulse"' in r for r in all_rows)
    assert any('"body":"nothing urgent"' in r for r in all_rows)


@pytest.mark.asyncio
async def test_pulse_dispatch_delegation_forwards_payload(orch, monkeypatch):
    captured: dict = {}

    async def fake_manager(env: Envelope) -> AgentResult:
        return AgentResult(
            reply_text=None,
            delegate_to="secretary",
            handoff_text="→ 已让秘书帮你记着",
            delegation_payload={
                "kind": "reminder_request",
                "appointment": "牙医",
                "when": "明天上午",
                "note": "",
            },
        )

    async def fake_secretary(env: Envelope) -> AgentResult:
        captured["env"] = env
        return AgentResult(reply_text="好的，明天上午提醒你", delegate_to=None, handoff_text=None)

    monkeypatch.setitem(AGENT_REGISTRY, "manager", fake_manager)
    monkeypatch.setitem(AGENT_REGISTRY, "secretary", fake_secretary)

    await orch.handle_pulse(_pulse_env(chat_id=999))

    assert "env" in captured, "secretary should have been dispatched"
    secretary_env = captured["env"]
    assert secretary_env.routing_reason == "manager_delegation"
    assert secretary_env.payload is not None
    assert secretary_env.payload["kind"] == "reminder_request"
    assert secretary_env.payload["appointment"] == "牙医"


@pytest.mark.asyncio
async def test_pulse_dispatch_non_manager_target_rejected(orch):
    env = _pulse_env()
    env.to_agent = "secretary"
    with pytest.raises(AssertionError):
        await orch.handle_pulse(env)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/orchestrator/test_pulse_dispatch.py -v
```

Expected: FAIL — `handle_pulse` not defined.

- [ ] **Step 3: Modify `orchestrator.py`**

**(3a)** Inside the existing delegation block in `handle()`, change the construction of the internal forward envelope to propagate `delegation_payload`. Find this code in `handle()`:

```python
            internal = Envelope(
                id=None,
                ts=_utc_now_iso(),
                parent_id=persisted.id,
                source="internal",
                telegram_chat_id=persisted.telegram_chat_id,
                telegram_msg_id=None,
                received_by_bot=None,
                from_kind="agent",
                from_agent="manager",
                to_agent=target,
                body=persisted.body,
                mentions=[],
                routing_reason="manager_delegation",
            )
```

Replace the last line with `routing_reason="manager_delegation",` followed by `payload=result.delegation_payload,`:

```python
            internal = Envelope(
                id=None,
                ts=_utc_now_iso(),
                parent_id=persisted.id,
                source="internal",
                telegram_chat_id=persisted.telegram_chat_id,
                telegram_msg_id=None,
                received_by_bot=None,
                from_kind="agent",
                from_agent="manager",
                to_agent=target,
                body=persisted.body,
                mentions=[],
                routing_reason="manager_delegation",
                payload=result.delegation_payload,
            )
```

**(3b)** Add a new `handle_pulse` method on `Orchestrator`. Paste this block after the existing `handle()` method, before `# --- helpers ---`:

```python
    async def handle_pulse(self, pulse_env: Envelope) -> None:
        """Entry point for scheduled pulse ticks.

        Parallel to ``handle(update)``: persists the pulse envelope, then
        dispatches the target agent, then reuses the same reply / delegation
        paths. Does not touch chat_focus and does not fan out to listeners.
        """
        assert pulse_env.source == "pulse"
        assert pulse_env.routing_reason == "pulse"
        assert pulse_env.to_agent == "manager", (
            f"pulse target must be 'manager' in 6c; got {pulse_env.to_agent!r}"
        )

        async with self.store.lock:
            persisted = self.store.messages().insert(pulse_env)
            assert persisted is not None  # pulse envelopes never collide (no msg_id)

        agent_fn = AGENT_REGISTRY[persisted.to_agent]
        result = await agent_fn(persisted)

        if result is None:
            log.debug("pulse %s: manager returned None", persisted.body)
            return

        if result.is_reply():
            async with self.store.lock:
                await self._emit_reply(
                    parent=persisted,
                    speaker=persisted.to_agent,
                    text=result.reply_text or "",
                )
            return

        # Delegation path — reuse the same structure as handle().
        async with self.store.lock:
            assert result.delegate_to is not None
            assert result.handoff_text is not None
            target = result.delegate_to
            if target not in AGENT_REGISTRY:
                raise RoutingError(f"unknown delegation target: {target!r}")

            await self._emit_reply(
                parent=persisted,
                speaker="manager",
                text=result.handoff_text,
            )

            internal = Envelope(
                id=None,
                ts=_utc_now_iso(),
                parent_id=persisted.id,
                source="internal",
                telegram_chat_id=persisted.telegram_chat_id,
                telegram_msg_id=None,
                received_by_bot=None,
                from_kind="agent",
                from_agent="manager",
                to_agent=target,
                body=persisted.body,
                mentions=[],
                routing_reason="manager_delegation",
                payload=result.delegation_payload,
            )
            persisted_internal = self.store.messages().insert(internal)
            assert persisted_internal is not None

        target_fn = AGENT_REGISTRY[target]
        target_result = await target_fn(persisted_internal)
        if target_result is None or not target_result.is_reply():
            raise RoutingError(
                f"pulse-delegated agent {target!r} must return a reply"
            )

        async with self.store.lock:
            await self._emit_reply(
                parent=persisted_internal,
                speaker=target,
                text=target_result.reply_text or "",
            )
```

- [ ] **Step 4: Create test package init if needed**

```
mkdir -p tests/orchestrator && [ -f tests/orchestrator/__init__.py ] || : > tests/orchestrator/__init__.py
```

- [ ] **Step 5: Run test to verify it passes**

```
uv run pytest tests/orchestrator/test_pulse_dispatch.py -v
```

Expected: 3 PASS. Run the full suite too to verify the delegation-payload plumbing didn't break existing routing:

```
uv run pytest -q
```

- [ ] **Step 6: Commit**

```
git add src/project0/orchestrator.py tests/orchestrator/test_pulse_dispatch.py tests/orchestrator/__init__.py
git commit -m "feat(orchestrator): handle_pulse entry point + delegation_payload plumbing"
```

---

## Task 9: Manager persona + config files

**Files:**
- Create: `prompts/manager.md`
- Create: `prompts/manager.toml`

**Design note:** Five-section Chinese persona, same parser pattern as Secretary. No tests in this task — the loader test comes in Task 10. This task just lands the content files.

- [ ] **Step 1: Create `prompts/manager.md`**

```markdown
# 经理 — 角色设定

你是这个多智能体系统的经理。你负责规划、调度、协调，但**不负责检查**其他 agent 的私有记忆或原始对话。你的语气沉稳、专注、偶尔温暖，但始终简洁。你不喜欢废话，也不喜欢用户被打扰。

你的权威是**协调权**，不是**检查权**。凡是涉及用户日程、重大计划改动、优先级重排的决定，你都必须先征得用户同意才执行。你可以提出建议、指出风险、给出选项，但**不要擅自替用户做决定**。

你拥有以下工具：
- 四个日历工具（列出、创建、修改、删除事件）
- 两个委派工具（委派给秘书、委派给情报员）

除此之外你什么都不能做。遇到超出你工具能力的请求，老实告诉用户「这个我帮不了」，不要编。

# 模式：私聊

用户正在私聊你。他在跟你直接对话，你是这次对话的主角。用你自己的语气回答，必要时调用日历工具查信息。除非用户明确要求，**不要主动调用委派工具**——私聊里用户想跟你说话，不是想被转介到别人那里。

回复要简洁，能一两句说完就别三四句。用户问「我明天有什么事吗」就直接报事项，不要先解释你要去查日历。

# 模式：群聊点名

你在群聊里被点名了（或者群聊的当前焦点是你）。场景跟私聊差不多，但语气可以再正式一点点——这里不是只有你们两个人。

遇到用户提到某个明确需要秘书温柔提醒的场合（比如「下周三牙医别忘了」），这种时候**可以**用 `delegate_to_secretary` 工具，让秘书用她自己的语气帮你传达。遇到需要查外部信息的场合（比如「最近有什么相关新闻」），用 `delegate_to_intelligence`。

# 模式：定时脉冲

你刚刚被一次**定时脉冲**唤醒了。用户没有在跟你说话——系统按计划叫醒了你，让你主动检查一下有没有什么需要用户注意的。

你拿到一个 `pulse_name` 和一个 `payload` dict。根据这两个信息决定做什么。典型场景：`pulse_name="check_calendar"`，`payload` 里有 `window_minutes` ——你的任务是调用 `calendar_list_events` 查一下这个时间窗口内有没有事件，然后决定：

- **没事** → 回一句短短的内部记录（比如「未来 60 分钟无事」），然后停。不要调用委派工具，不要打扰用户。
- **有事且足够近** → 调用 `delegate_to_secretary`，把事情的名字、时间、备注填进 `reminder_text` / `appointment` / `when` / `note` 字段。秘书会用她自己的语气去通知用户。委派之后立刻停手，不要再调任何工具。

脉冲触发时用户看不见你在想什么——你的 reply_text 只会被记到内部日志里，除非你委派了秘书，她的消息才会发到用户的 Telegram。所以在脉冲模式里话要**更少**。

# 模式：工具使用守则

1. **读在写之前**：改或删一个日历事件之前，先用 `calendar_list_events` 确认它存在，拿到它的 `event_id`。不要拍脑袋猜 `event_id`。
2. **不要编细节**：如果用户只说了「下周开会」没说具体时间，别自己填一个时间进 `calendar_create_event`。先问。
3. **委派后就停手**：调用了 `delegate_to_secretary` 或 `delegate_to_intelligence` 之后，不要再调用任何工具，直接结束这一轮。多余的工具调用会被忽略，但会浪费 token。
4. **错误恢复**：日历工具可能返回错误（`is_error=true`）。读完错误信息，能重试就重试（用更正后的参数），不能重试就用人话告诉用户「出了什么问题」。
5. **Handoff 要短**：你在委派时写的 `reminder_text` / `query` 是给下游 agent 看的，要具体、无冗余。不要写成给用户看的长句子。
```

- [ ] **Step 2: Create `prompts/manager.toml`**

```toml
# Manager agent configuration.
# Mirrors the Secretary config pattern. Missing keys raise at startup.

[llm]
model               = "claude-sonnet-4-6"
max_tokens_reply    = 1024
max_tool_iterations = 8

[context]
transcript_window = 20

# Scheduled pulses. Each [[pulse]] entry becomes one asyncio scheduler task.
# `chat_id_env` names an environment variable whose value is the integer
# Telegram chat id to bind this pulse to. Required for pulses that may
# delegate user-facing messages (otherwise the reminder has nowhere to go).
[[pulse]]
name         = "check_calendar"
every_seconds = 300
chat_id_env  = "MANAGER_PULSE_CHAT_ID"
payload      = { window_minutes = 60 }
```

- [ ] **Step 3: Commit**

```
git add prompts/manager.md prompts/manager.toml
git commit -m "prompts(manager): Chinese persona + TOML config with check_calendar pulse"
```

---

## Task 10: Manager persona and config loaders

**Files:**
- Modify: `src/project0/agents/manager.py` (adds loaders; full `Manager` class lands in Task 11)
- Test: `tests/agents/test_manager_persona_load.py`

**Design note:** This task replaces `manager_stub` in `agents/manager.py` with the loader functions but **keeps** a placeholder `manager_stub = ...` export so `registry.py` still imports cleanly until Task 14. We will also keep the old stub behavior for AGENT_REGISTRY until the full Manager class is wired in Task 14.

Wait — `registry.py` imports `manager_stub` at module load. If we remove it in this task the registry breaks. So: in this task we **add** `load_manager_persona` and `load_manager_config` alongside the existing `manager_stub`. The stub stays until Task 14.

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_manager_persona_load.py
from pathlib import Path

import pytest

from project0.agents.manager import (
    ManagerConfig,
    ManagerPersona,
    load_manager_config,
    load_manager_persona,
)


def test_loads_real_persona_file():
    persona = load_manager_persona(Path("prompts/manager.md"))
    assert isinstance(persona, ManagerPersona)
    assert "经理" in persona.core
    assert "私聊" in persona.dm_mode
    assert "群聊点名" in persona.group_addressed_mode
    assert "定时脉冲" in persona.pulse_mode
    assert "工具使用守则" in persona.tool_use_guide


def test_loads_real_config_file():
    cfg = load_manager_config(Path("prompts/manager.toml"))
    assert isinstance(cfg, ManagerConfig)
    assert cfg.model.startswith("claude-")
    assert cfg.max_tokens_reply > 0
    assert cfg.max_tool_iterations >= 1
    assert cfg.transcript_window >= 1


def test_missing_section_raises(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("# 经理 — 角色设定\nhello\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing section"):
        load_manager_persona(p)


def test_near_miss_header_detected(tmp_path):
    p = tmp_path / "typo.md"
    p.write_text(
        "# 经理 — 角色设定\ncore\n\n# 模式:私聊\nbody\n",  # half-width colon, no space
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed section header"):
        load_manager_persona(p)


def test_missing_config_key_raises(tmp_path):
    p = tmp_path / "cfg.toml"
    p.write_text("[llm]\nmodel='x'\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="max_tokens_reply"):
        load_manager_config(p)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/agents/test_manager_persona_load.py -v
```

Expected: FAIL — these symbols don't exist.

- [ ] **Step 3: Rewrite `src/project0/agents/manager.py`**

Replace the whole file with the loader content (plus a placeholder `manager_stub` that will be removed in Task 14):

```python
"""Manager agent — loader functions for persona and config.

The real Manager class lands in later tasks (11–13). This module already
exports ``load_manager_persona`` and ``load_manager_config`` so the
composition-root wiring in main.py can import them incrementally.

For now it still exports ``manager_stub`` so ``agents/registry.py`` keeps
importing cleanly; Task 13 removes the stub once Manager is fully wired.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project0.envelope import AgentResult, Envelope


# --- persona -----------------------------------------------------------------

@dataclass(frozen=True)
class ManagerPersona:
    core: str
    dm_mode: str
    group_addressed_mode: str
    pulse_mode: str
    tool_use_guide: str


_PERSONA_SECTIONS = {
    "core":                 "# 经理 — 角色设定",
    "dm_mode":               "# 模式：私聊",
    "group_addressed_mode":  "# 模式：群聊点名",
    "pulse_mode":            "# 模式：定时脉冲",
    "tool_use_guide":        "# 模式：工具使用守则",
}

_CANONICAL_HEADERS_NORMALIZED = {
    "".join(h.split()): h for h in _PERSONA_SECTIONS.values()
}


def load_manager_persona(path: Path) -> ManagerPersona:
    text = path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    lines = text.splitlines()
    current_key: str | None = None
    current_buf: list[str] = []
    header_to_key = {v: k for k, v in _PERSONA_SECTIONS.items()}
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped in header_to_key:
            if current_key is not None:
                sections[current_key] = "\n".join(current_buf).strip()
            current_key = header_to_key[stripped]
            current_buf = []
            continue
        if stripped.startswith("#"):
            normalized = "".join(stripped.split())
            if normalized in _CANONICAL_HEADERS_NORMALIZED:
                canonical = _CANONICAL_HEADERS_NORMALIZED[normalized]
                raise ValueError(
                    f"{path}:{lineno}: malformed section header "
                    f"{stripped!r}; expected exactly {canonical!r}"
                )
        if current_key is not None:
            current_buf.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(current_buf).strip()

    for key, header in _PERSONA_SECTIONS.items():
        if key not in sections or not sections[key]:
            raise ValueError(f"persona file {path} is missing section '{header}'")

    return ManagerPersona(
        core=sections["core"],
        dm_mode=sections["dm_mode"],
        group_addressed_mode=sections["group_addressed_mode"],
        pulse_mode=sections["pulse_mode"],
        tool_use_guide=sections["tool_use_guide"],
    )


# --- config ------------------------------------------------------------------

@dataclass(frozen=True)
class ManagerConfig:
    model: str
    max_tokens_reply: int
    max_tool_iterations: int
    transcript_window: int


def load_manager_config(path: Path) -> ManagerConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    def _require(section: str, key: str) -> Any:
        try:
            return data[section][key]
        except KeyError as e:
            raise RuntimeError(
                f"missing config key {section}.{key} in {path}"
            ) from e

    return ManagerConfig(
        model=str(_require("llm", "model")),
        max_tokens_reply=int(_require("llm", "max_tokens_reply")),
        max_tool_iterations=int(_require("llm", "max_tool_iterations")),
        transcript_window=int(_require("context", "transcript_window")),
    )


# --- placeholder stub (removed in Task 14) -----------------------------------

async def manager_stub(env: Envelope) -> AgentResult:
    """Legacy stub kept only so agents/registry.py imports cleanly until
    Task 14 swaps in the real Manager class. Behavior is the original
    one-rule hardcoded delegation."""
    if "news" in env.body.lower():
        return AgentResult(
            reply_text=None,
            delegate_to="intelligence",
            handoff_text="→ forwarding to @intelligence",
        )
    return AgentResult(
        reply_text=f"[manager-stub] acknowledged: {env.body}",
        delegate_to=None,
        handoff_text=None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/agents/test_manager_persona_load.py -v
```

Expected: 5 PASS. Also run the full suite to make sure the registry still imports:

```
uv run pytest -q
```

- [ ] **Step 5: Commit**

```
git add src/project0/agents/manager.py tests/agents/test_manager_persona_load.py
git commit -m "feat(manager): persona + config loaders (stub kept until full wiring)"
```

---

## Task 11: Manager class skeleton + tool specs + tool dispatch

**Files:**
- Modify: `src/project0/agents/manager.py`
- Test: `tests/agents/test_manager_tool_dispatch.py`

**Design note:** This task lands the `Manager` class with `_build_tool_specs` and `_dispatch_tool` but not yet `_agentic_loop` or the entry points. `TurnState` is defined as a local module dataclass so tests can construct it.

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_manager_tool_dispatch.py
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from project0.agents.manager import Manager, ManagerConfig, ManagerPersona, TurnState
from project0.calendar.errors import GoogleCalendarError
from project0.calendar.model import CalendarEvent
from project0.llm.tools import ToolCall


def _persona():
    return ManagerPersona(
        core="c", dm_mode="d", group_addressed_mode="g",
        pulse_mode="p", tool_use_guide="t",
    )


def _config():
    return ManagerConfig(
        model="m", max_tokens_reply=100, max_tool_iterations=8, transcript_window=10,
    )


def _mgr(calendar):
    return Manager(
        llm=None,  # not used by dispatch tests
        calendar=calendar,
        memory=None,
        messages_store=None,
        persona=_persona(),
        config=_config(),
    )


def test_tool_specs_include_all_six_tools():
    mgr = _mgr(calendar=None)
    names = {t.name for t in mgr._tool_specs}
    assert names == {
        "calendar_list_events",
        "calendar_create_event",
        "calendar_update_event",
        "calendar_delete_event",
        "delegate_to_secretary",
        "delegate_to_intelligence",
    }


@pytest.mark.asyncio
async def test_dispatch_list_events_success():
    fake_event = CalendarEvent(
        id="e1", summary="牙医", start=datetime(2026, 4, 15, 10, tzinfo=ZoneInfo("UTC")),
        end=datetime(2026, 4, 15, 11, tzinfo=ZoneInfo("UTC")),
        all_day=False, description=None, location=None, html_link="https://x",
    )
    cal = AsyncMock()
    cal.list_events = AsyncMock(return_value=[fake_event])
    mgr = _mgr(cal)
    ts = TurnState()

    content, is_err = await mgr._dispatch_tool(
        ToolCall(
            id="1", name="calendar_list_events",
            input={
                "time_min": "2026-04-14T00:00:00+00:00",
                "time_max": "2026-04-20T00:00:00+00:00",
                "max_results": 10,
            },
        ),
        ts,
    )
    assert is_err is False
    assert "牙医" in content
    cal.list_events.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_list_events_calendar_error():
    cal = AsyncMock()
    cal.list_events = AsyncMock(side_effect=GoogleCalendarError("boom"))
    mgr = _mgr(cal)
    ts = TurnState()

    content, is_err = await mgr._dispatch_tool(
        ToolCall(
            id="1", name="calendar_list_events",
            input={
                "time_min": "2026-04-14T00:00:00+00:00",
                "time_max": "2026-04-20T00:00:00+00:00",
                "max_results": 10,
            },
        ),
        ts,
    )
    assert is_err is True
    assert "boom" in content


@pytest.mark.asyncio
async def test_dispatch_delegate_to_secretary_sets_turn_state():
    mgr = _mgr(calendar=None)
    ts = TurnState()

    content, is_err = await mgr._dispatch_tool(
        ToolCall(
            id="1", name="delegate_to_secretary",
            input={
                "reminder_text": "下周三牙医",
                "appointment": "牙医",
                "when": "下周三 10:00",
                "note": "记得带病历",
            },
        ),
        ts,
    )
    assert is_err is False
    assert content == "delegated"
    assert ts.delegation_target == "secretary"
    assert "下周三牙医" in ts.delegation_handoff
    assert ts.delegation_payload == {
        "kind": "reminder_request",
        "appointment": "牙医",
        "when": "下周三 10:00",
        "note": "记得带病历",
    }


@pytest.mark.asyncio
async def test_dispatch_delegate_to_intelligence():
    mgr = _mgr(calendar=None)
    ts = TurnState()

    content, is_err = await mgr._dispatch_tool(
        ToolCall(id="1", name="delegate_to_intelligence", input={"query": "OpenAI news"}),
        ts,
    )
    assert content == "delegated"
    assert ts.delegation_target == "intelligence"
    assert ts.delegation_payload == {"kind": "query", "query": "OpenAI news"}


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_returns_error():
    mgr = _mgr(calendar=None)
    ts = TurnState()
    content, is_err = await mgr._dispatch_tool(
        ToolCall(id="1", name="bogus", input={}), ts
    )
    assert is_err is True
    assert "unknown tool" in content


@pytest.mark.asyncio
async def test_dispatch_invalid_input_returns_error():
    mgr = _mgr(calendar=None)
    ts = TurnState()
    content, is_err = await mgr._dispatch_tool(
        ToolCall(id="1", name="delegate_to_secretary", input={}),  # missing reminder_text
        ts,
    )
    assert is_err is True
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/agents/test_manager_tool_dispatch.py -v
```

Expected: FAIL — `Manager`, `TurnState` not defined.

- [ ] **Step 3: Extend `src/project0/agents/manager.py`**

Add new imports at the top of the file:

```python
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from project0.calendar.errors import GoogleCalendarError
from project0.envelope import AgentResult, Envelope
from project0.llm.tools import ToolCall, ToolSpec

if TYPE_CHECKING:
    from project0.calendar.client import GoogleCalendar
    from project0.llm.provider import LLMProvider
    from project0.store import AgentMemory, MessagesStore

log = logging.getLogger(__name__)
```

Append these definitions to the module (after the loaders, before `manager_stub`):

```python
# --- per-turn state ----------------------------------------------------------

@dataclass
class TurnState:
    """Mutable state for one agentic loop invocation. Lives in the local
    scope of ``_agentic_loop`` so concurrent turns never cross-contaminate."""
    delegation_target: str | None = None
    delegation_handoff: str | None = None
    delegation_payload: dict[str, Any] | None = None


# --- Manager class -----------------------------------------------------------

_LIST_EVENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "time_min":    {"type": "string", "description": "ISO8601 start time (inclusive)"},
        "time_max":    {"type": "string", "description": "ISO8601 end time (exclusive)"},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 250},
    },
    "required": ["time_min", "time_max"],
}

_CREATE_EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary":     {"type": "string"},
        "start":       {"type": "string", "description": "ISO8601 aware datetime"},
        "end":         {"type": "string", "description": "ISO8601 aware datetime"},
        "description": {"type": "string"},
        "location":    {"type": "string"},
    },
    "required": ["summary", "start", "end"],
}

_UPDATE_EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "event_id":    {"type": "string"},
        "summary":     {"type": "string"},
        "start":       {"type": "string"},
        "end":         {"type": "string"},
        "description": {"type": "string"},
        "location":    {"type": "string"},
    },
    "required": ["event_id"],
}

_DELETE_EVENT_SCHEMA = {
    "type": "object",
    "properties": {"event_id": {"type": "string"}},
    "required": ["event_id"],
}

_DELEGATE_SECRETARY_SCHEMA = {
    "type": "object",
    "properties": {
        "reminder_text": {"type": "string"},
        "appointment":   {"type": "string"},
        "when":          {"type": "string"},
        "note":          {"type": "string"},
    },
    "required": ["reminder_text"],
}

_DELEGATE_INTEL_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}


class Manager:
    def __init__(
        self,
        *,
        llm: "LLMProvider | None",
        calendar: "GoogleCalendar | None",
        memory: "AgentMemory | None",
        messages_store: "MessagesStore | None",
        persona: ManagerPersona,
        config: ManagerConfig,
    ) -> None:
        self._llm = llm
        self._calendar = calendar
        self._memory = memory
        self._messages = messages_store
        self._persona = persona
        self._config = config
        self._tool_specs = self._build_tool_specs()

    def _build_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="calendar_list_events",
                description="List calendar events in a time window.",
                input_schema=_LIST_EVENTS_SCHEMA,
            ),
            ToolSpec(
                name="calendar_create_event",
                description="Create a calendar event. Always confirm details with the user first.",
                input_schema=_CREATE_EVENT_SCHEMA,
            ),
            ToolSpec(
                name="calendar_update_event",
                description="Patch an existing calendar event. Read the event first via list_events to get its event_id.",
                input_schema=_UPDATE_EVENT_SCHEMA,
            ),
            ToolSpec(
                name="calendar_delete_event",
                description="Delete a calendar event by event_id.",
                input_schema=_DELETE_EVENT_SCHEMA,
            ),
            ToolSpec(
                name="delegate_to_secretary",
                description=(
                    "Delegate a user-facing reminder to the Secretary agent. "
                    "After calling this tool, stop calling any further tools."
                ),
                input_schema=_DELEGATE_SECRETARY_SCHEMA,
            ),
            ToolSpec(
                name="delegate_to_intelligence",
                description=(
                    "Delegate a research/briefing query to the Intelligence agent. "
                    "After calling this tool, stop calling any further tools."
                ),
                input_schema=_DELEGATE_INTEL_SCHEMA,
            ),
        ]

    async def _dispatch_tool(
        self, call: ToolCall, turn_state: TurnState
    ) -> tuple[str, bool]:
        try:
            if call.name == "calendar_list_events":
                assert self._calendar is not None
                tmin = datetime.fromisoformat(call.input["time_min"])
                tmax = datetime.fromisoformat(call.input["time_max"])
                max_results = int(call.input.get("max_results", 50))
                events = await self._calendar.list_events(tmin, tmax, max_results)
                payload = [
                    {
                        "id":       e.id,
                        "summary":  e.summary,
                        "start":    e.start.isoformat(),
                        "end":      e.end.isoformat(),
                        "all_day":  e.all_day,
                        "location": e.location,
                        "description": e.description,
                    }
                    for e in events
                ]
                return json.dumps(payload, ensure_ascii=False), False

            if call.name == "calendar_create_event":
                assert self._calendar is not None
                ev = await self._calendar.create_event(
                    summary=call.input["summary"],
                    start=datetime.fromisoformat(call.input["start"]),
                    end=datetime.fromisoformat(call.input["end"]),
                    description=call.input.get("description"),
                    location=call.input.get("location"),
                )
                return json.dumps({"event_id": ev.id, "summary": ev.summary}, ensure_ascii=False), False

            if call.name == "calendar_update_event":
                assert self._calendar is not None
                kwargs: dict = {}
                for k in ("summary", "description", "location"):
                    if k in call.input:
                        kwargs[k] = call.input[k]
                for k in ("start", "end"):
                    if k in call.input:
                        kwargs[k] = datetime.fromisoformat(call.input[k])
                await self._calendar.update_event(call.input["event_id"], **kwargs)
                return "ok", False

            if call.name == "calendar_delete_event":
                assert self._calendar is not None
                await self._calendar.delete_event(call.input["event_id"])
                return "ok", False

            if call.name == "delegate_to_secretary":
                reminder_text = str(call.input["reminder_text"])
                turn_state.delegation_target = "secretary"
                turn_state.delegation_handoff = f"→ 已让秘书帮你记着 {reminder_text}"
                turn_state.delegation_payload = {
                    "kind":        "reminder_request",
                    "appointment": str(call.input.get("appointment", "")),
                    "when":        str(call.input.get("when", "")),
                    "note":        str(call.input.get("note", "")),
                }
                return "delegated", False

            if call.name == "delegate_to_intelligence":
                query = str(call.input["query"])
                turn_state.delegation_target = "intelligence"
                turn_state.delegation_handoff = f"→ 去查一下「{query}」"
                turn_state.delegation_payload = {"kind": "query", "query": query}
                return "delegated", False

            return f"unknown tool: {call.name}", True

        except GoogleCalendarError as e:
            log.warning("manager tool %s: calendar error: %s", call.name, e)
            return f"calendar error: {e}", True
        except (KeyError, ValueError, TypeError) as e:
            log.warning("manager tool %s: invalid input: %s", call.name, e)
            return f"invalid tool input: {e}", True
```

- [ ] **Step 4: Create test package init if needed**

```
[ -f tests/agents/__init__.py ] || : > tests/agents/__init__.py
```

- [ ] **Step 5: Run test to verify it passes**

```
uv run pytest tests/agents/test_manager_tool_dispatch.py -v
```

Expected: 7 PASS.

- [ ] **Step 6: Commit**

```
git add src/project0/agents/manager.py tests/agents/test_manager_tool_dispatch.py tests/agents/__init__.py
git commit -m "feat(manager): Manager class skeleton + tool specs + _dispatch_tool"
```

---

## Task 12: `_agentic_loop` + entry points + handle

**Files:**
- Modify: `src/project0/agents/manager.py`
- Test: `tests/agents/test_manager_tool_loop.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_manager_tool_loop.py
from unittest.mock import AsyncMock

import pytest

from project0.agents.manager import (
    Manager,
    ManagerConfig,
    ManagerPersona,
)
from project0.envelope import Envelope
from project0.llm.provider import FakeProvider, LLMProviderError
from project0.llm.tools import ToolCall, ToolUseResult


def _persona():
    return ManagerPersona(
        core="c", dm_mode="d", group_addressed_mode="g",
        pulse_mode="p", tool_use_guide="t",
    )


def _config(max_iter=4):
    return ManagerConfig(
        model="m", max_tokens_reply=100,
        max_tool_iterations=max_iter, transcript_window=5,
    )


def _mgr(llm, calendar=None, messages_store=None):
    return Manager(
        llm=llm, calendar=calendar, memory=None,
        messages_store=messages_store, persona=_persona(), config=_config(),
    )


class _FakeMessagesStore:
    def recent_for_chat(self, *, chat_id, limit):
        return []


def _env_dm(body="hi"):
    return Envelope(
        id=1, ts="2026-04-14T00:00:00Z", parent_id=None,
        source="telegram_dm", telegram_chat_id=42, telegram_msg_id=1,
        received_by_bot="manager", from_kind="user", from_agent=None,
        to_agent="manager", body=body, mentions=[], routing_reason="direct_dm",
    )


def _env_pulse():
    return Envelope(
        id=1, ts="2026-04-14T00:00:00Z", parent_id=None,
        source="pulse", telegram_chat_id=None, telegram_msg_id=None,
        received_by_bot=None, from_kind="system", from_agent=None,
        to_agent="manager", body="check_calendar", mentions=[],
        routing_reason="pulse",
        payload={"pulse_name": "check_calendar", "window_minutes": 60},
    )


@pytest.mark.asyncio
async def test_plain_text_turn_returns_reply():
    fake = FakeProvider(tool_responses=[
        ToolUseResult(kind="text", text="hello back", tool_calls=[], stop_reason="end_turn"),
    ])
    mgr = _mgr(fake, messages_store=_FakeMessagesStore())

    result = await mgr.handle(_env_dm("hi"))
    assert result is not None
    assert result.reply_text == "hello back"
    assert result.delegate_to is None


@pytest.mark.asyncio
async def test_tool_then_text_flow():
    cal = AsyncMock()
    cal.list_events = AsyncMock(return_value=[])

    fake = FakeProvider(tool_responses=[
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(id="t1", name="calendar_list_events",
                                  input={"time_min": "2026-04-14T00:00:00+00:00",
                                         "time_max": "2026-04-15T00:00:00+00:00",
                                         "max_results": 10})],
            stop_reason="tool_use",
        ),
        ToolUseResult(kind="text", text="没有事", tool_calls=[], stop_reason="end_turn"),
    ])
    mgr = _mgr(fake, calendar=cal, messages_store=_FakeMessagesStore())

    result = await mgr.handle(_env_dm("我明天有什么事"))
    assert result is not None
    assert result.reply_text == "没有事"
    assert len(fake.tool_calls_log) == 2
    # Second call must include the tool_result turn.
    second_msgs = fake.tool_calls_log[1]["messages"]
    assert any(
        getattr(m, "tool_use_id", None) == "t1"
        for m in second_msgs
    )


@pytest.mark.asyncio
async def test_delegation_tool_returns_delegation_result():
    fake = FakeProvider(tool_responses=[
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(
                id="t1", name="delegate_to_secretary",
                input={
                    "reminder_text": "下周三牙医",
                    "appointment": "牙医",
                    "when": "下周三 10:00",
                    "note": "",
                },
            )],
            stop_reason="tool_use",
        ),
        ToolUseResult(
            kind="text", text="(epilogue that should be suppressed)",
            tool_calls=[], stop_reason="end_turn",
        ),
    ])
    mgr = _mgr(fake, messages_store=_FakeMessagesStore())

    result = await mgr.handle(_env_dm("下周三别让我忘了牙医"))
    assert result is not None
    assert result.delegate_to == "secretary"
    assert result.reply_text is None  # suppressed in favor of delegation
    assert "下周三牙医" in result.handoff_text
    assert result.delegation_payload is not None
    assert result.delegation_payload["appointment"] == "牙医"


@pytest.mark.asyncio
async def test_max_iterations_raises():
    # Every response is tool_use → loop never terminates → hits the cap.
    endless = [
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(id=f"t{i}", name="calendar_list_events",
                                  input={"time_min": "2026-04-14T00:00:00+00:00",
                                         "time_max": "2026-04-15T00:00:00+00:00"})],
            stop_reason="tool_use",
        )
        for i in range(10)
    ]
    fake = FakeProvider(tool_responses=endless)
    cal = AsyncMock()
    cal.list_events = AsyncMock(return_value=[])
    mgr = _mgr(fake, calendar=cal, messages_store=_FakeMessagesStore())

    with pytest.raises(LLMProviderError, match="max_tool_iterations"):
        await mgr.handle(_env_dm("loop forever"))


@pytest.mark.asyncio
async def test_pulse_path_returns_none_on_empty_text():
    fake = FakeProvider(tool_responses=[
        ToolUseResult(kind="text", text="", tool_calls=[], stop_reason="end_turn"),
    ])
    mgr = _mgr(fake, messages_store=_FakeMessagesStore())

    result = await mgr.handle(_env_pulse())
    assert result is None


@pytest.mark.asyncio
async def test_unknown_routing_reason_returns_none():
    fake = FakeProvider(tool_responses=[])
    mgr = _mgr(fake, messages_store=_FakeMessagesStore())
    env = _env_dm()
    env.routing_reason = "listener_observation"
    assert await mgr.handle(env) is None
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/agents/test_manager_tool_loop.py -v
```

Expected: FAIL — `handle`, `_agentic_loop` not implemented.

- [ ] **Step 3: Extend `src/project0/agents/manager.py`**

Add imports at the top:

```python
from project0.llm.provider import LLMProviderError, Msg
from project0.llm.tools import AssistantToolUseMsg, ToolResultMsg, ToolUseResult
```

Append these methods to the `Manager` class (after `_dispatch_tool`):

```python
    async def handle(self, env: Envelope) -> AgentResult | None:
        reason = env.routing_reason
        if reason == "direct_dm":
            return await self._run_chat_turn(env, self._persona.dm_mode)
        if reason in ("mention", "focus", "default_manager"):
            return await self._run_chat_turn(env, self._persona.group_addressed_mode)
        if reason == "pulse":
            return await self._run_pulse_turn(env)
        log.debug("manager: ignoring routing_reason=%s", reason)
        return None

    def _build_system_prompt(self, mode_section: str) -> str:
        return (
            self._persona.core
            + "\n\n" + mode_section
            + "\n\n" + self._persona.tool_use_guide
        )

    def _load_transcript(self, chat_id: int | None) -> str:
        if chat_id is None or self._messages is None:
            return ""
        envs = self._messages.recent_for_chat(
            chat_id=chat_id, limit=self._config.transcript_window
        )
        lines: list[str] = []
        for e in envs:
            if e.from_kind == "user":
                lines.append(f"user: {e.body}")
            elif e.from_kind == "agent":
                speaker = e.from_agent or "unknown"
                lines.append(f"{speaker}: {e.body}")
        return "\n".join(lines)

    async def _run_chat_turn(
        self, env: Envelope, mode_section: str
    ) -> AgentResult | None:
        system = self._build_system_prompt(mode_section)
        transcript = self._load_transcript(env.telegram_chat_id)
        initial_user_text = (
            f"对话记录:\n{transcript}\n\n最新用户消息: {env.body}"
            if transcript else f"最新用户消息: {env.body}"
        )
        return await self._agentic_loop(
            system=system,
            initial_user_text=initial_user_text,
            max_tokens=self._config.max_tokens_reply,
            is_pulse=False,
        )

    async def _run_pulse_turn(self, env: Envelope) -> AgentResult | None:
        system = self._build_system_prompt(self._persona.pulse_mode)
        payload = env.payload or {}
        pulse_name = payload.get("pulse_name", env.body)
        payload_json = json.dumps(payload, ensure_ascii=False)
        transcript = self._load_transcript(env.telegram_chat_id)
        initial_user_text = (
            f"定时脉冲被触发: {pulse_name}\n"
            f"payload: {payload_json}"
        )
        if transcript:
            initial_user_text += f"\n\n最近对话:\n{transcript}"
        return await self._agentic_loop(
            system=system,
            initial_user_text=initial_user_text,
            max_tokens=self._config.max_tokens_reply,
            is_pulse=True,
        )

    async def _agentic_loop(
        self,
        *,
        system: str,
        initial_user_text: str,
        max_tokens: int,
        is_pulse: bool,
    ) -> AgentResult | None:
        assert self._llm is not None
        turn_state = TurnState()
        messages: list = [Msg(role="user", content=initial_user_text)]

        for _iter in range(self._config.max_tool_iterations):
            try:
                result = await self._llm.complete_with_tools(
                    system=system,
                    messages=messages,
                    tools=self._tool_specs,
                    max_tokens=max_tokens,
                )
            except LLMProviderError as e:
                log.warning("manager LLM call failed: %s", e)
                return None

            if result.kind == "text":
                if turn_state.delegation_target is not None:
                    # Delegation queued: suppress the trailing text, return as delegation.
                    return AgentResult(
                        reply_text=None,
                        delegate_to=turn_state.delegation_target,
                        handoff_text=turn_state.delegation_handoff,
                        delegation_payload=turn_state.delegation_payload,
                    )
                final_text = result.text or ""
                if is_pulse and not final_text.strip():
                    return None
                return AgentResult(
                    reply_text=final_text,
                    delegate_to=None,
                    handoff_text=None,
                )

            # tool_use branch
            messages.append(
                AssistantToolUseMsg(
                    tool_calls=list(result.tool_calls),
                    text=result.text,
                )
            )
            for call in result.tool_calls:
                content_str, is_err = await self._dispatch_tool(call, turn_state)
                messages.append(
                    ToolResultMsg(
                        tool_use_id=call.id,
                        content=content_str,
                        is_error=is_err,
                    )
                )

        raise LLMProviderError(
            f"manager exceeded max_tool_iterations={self._config.max_tool_iterations}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/agents/test_manager_tool_loop.py -v
```

Expected: 6 PASS.

- [ ] **Step 5: Run full agents test suite**

```
uv run pytest tests/agents -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```
git add src/project0/agents/manager.py tests/agents/test_manager_tool_loop.py
git commit -m "feat(manager): agentic tool-use loop + DM/group/pulse entry paths"
```

---

## Task 13: Registry `register_manager` + remove stub

**Files:**
- Modify: `src/project0/agents/registry.py`
- Modify: `src/project0/agents/manager.py` (remove stub)

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_register_manager.py
import pytest

from project0.agents.manager import (
    Manager,
    ManagerConfig,
    ManagerPersona,
)
from project0.agents.registry import AGENT_REGISTRY, register_manager
from project0.envelope import Envelope
from project0.llm.provider import FakeProvider
from project0.llm.tools import ToolUseResult


@pytest.mark.asyncio
async def test_register_manager_installs_handle_in_registry():
    persona = ManagerPersona(core="c", dm_mode="d", group_addressed_mode="g",
                             pulse_mode="p", tool_use_guide="t")
    config = ManagerConfig(model="m", max_tokens_reply=100,
                           max_tool_iterations=4, transcript_window=5)

    fake = FakeProvider(tool_responses=[
        ToolUseResult(kind="text", text="ok", tool_calls=[], stop_reason="end_turn")
    ])

    class _Msgs:
        def recent_for_chat(self, *, chat_id, limit):
            return []

    mgr = Manager(llm=fake, calendar=None, memory=None,
                  messages_store=_Msgs(), persona=persona, config=config)

    # Save original to restore after.
    original = AGENT_REGISTRY.get("manager")
    try:
        register_manager(mgr.handle)
        handle = AGENT_REGISTRY["manager"]
        env = Envelope(
            id=1, ts="2026-04-14T00:00:00Z", parent_id=None,
            source="telegram_dm", telegram_chat_id=42, telegram_msg_id=1,
            received_by_bot="manager", from_kind="user", from_agent=None,
            to_agent="manager", body="hi", mentions=[], routing_reason="direct_dm",
        )
        result = await handle(env)
        assert result.reply_text == "ok"
    finally:
        if original is not None:
            AGENT_REGISTRY["manager"] = original
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/agents/test_register_manager.py -v
```

Expected: FAIL — `register_manager` not defined.

- [ ] **Step 3: Modify `registry.py`**

Replace the file (change two things: remove the `from project0.agents.manager import manager_stub` import, stop pre-populating `"manager"` in `AGENT_REGISTRY`, and add `register_manager`):

```python
"""Central registry of agents, their metadata, and their listener roles.

Two dicts:
  - AGENT_REGISTRY: routing targets (@mention, focus, default_manager,
    direct_dm, manager_delegation). The orchestrator dispatches an envelope
    to exactly one entry here.
  - LISTENER_REGISTRY: passive observers. After the focus target is
    dispatched, the orchestrator fans out a listener_observation envelope
    to every entry here whose name is not already the focus target.

Manager and Secretary are class instances with dependencies, installed
via ``register_manager`` / ``register_secretary`` from main.py at startup.
Intelligence is still a plain async stub (until 6d).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from project0.agents.intelligence import intelligence_stub
from project0.envelope import AgentResult, Envelope

AgentFn = Callable[[Envelope], Awaitable[AgentResult]]
AgentOptionalFn = Callable[[Envelope], Awaitable[AgentResult | None]]
ListenerFn = Callable[[Envelope], Awaitable[AgentResult | None]]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    token_env_key: str


AGENT_REGISTRY: dict[str, AgentFn] = {
    "intelligence": intelligence_stub,
    # "manager" installed by register_manager(...) in main.py.
    # "secretary" installed by register_secretary(...) in main.py.
}

LISTENER_REGISTRY: dict[str, ListenerFn] = {
    # "secretary" installed by register_secretary(...) in main.py.
}

AGENT_SPECS: dict[str, AgentSpec] = {
    "manager": AgentSpec(name="manager", token_env_key="TELEGRAM_BOT_TOKEN_MANAGER"),
    "intelligence": AgentSpec(
        name="intelligence", token_env_key="TELEGRAM_BOT_TOKEN_INTELLIGENCE"
    ),
    "secretary": AgentSpec(
        name="secretary", token_env_key="TELEGRAM_BOT_TOKEN_SECRETARY"
    ),
}


def register_manager(handle: AgentOptionalFn) -> None:
    """Install Manager's ``handle`` into AGENT_REGISTRY. Adapts the
    ``AgentResult | None`` return type to the ``AgentResult`` expected by
    AGENT_REGISTRY by surfacing a fail-visible placeholder if handle()
    returns None (which happens on unhandled routing reasons or on LLM
    errors during a chat turn)."""

    async def agent_adapter(env: Envelope) -> AgentResult:
        result = await handle(env)
        if result is None:
            return AgentResult(
                reply_text="(manager 暂时不便回答...)",
                delegate_to=None,
                handoff_text=None,
            )
        return result

    AGENT_REGISTRY["manager"] = agent_adapter


def register_secretary(handle: ListenerFn) -> None:
    """Install Secretary's ``handle`` into both registries (unchanged)."""

    async def agent_adapter(env: Envelope) -> AgentResult:
        result = await handle(env)
        if result is None:
            return AgentResult(
                reply_text="(秘书暂时走神了...)",
                delegate_to=None,
                handoff_text=None,
            )
        return result

    AGENT_REGISTRY["secretary"] = agent_adapter
    LISTENER_REGISTRY["secretary"] = handle
```

- [ ] **Step 4: Remove `manager_stub` from `manager.py`**

Open `src/project0/agents/manager.py` and delete the `manager_stub` function and its section header (the `# --- placeholder stub (removed in Task 14) ---` comment block).

- [ ] **Step 5: Run failing tests**

```
uv run pytest tests/agents/test_register_manager.py -v
uv run pytest -q
```

Expected: the new test passes; the full suite still passes. **Important:** if any existing test imports `manager_stub`, grep for it and either delete those imports or migrate those tests to the real `Manager` class.

```
grep -rn "manager_stub" tests/ src/
```

Expected: no references after cleanup.

- [ ] **Step 6: Commit**

```
git add src/project0/agents/registry.py src/project0/agents/manager.py tests/agents/test_register_manager.py
git commit -m "feat(registry): register_manager + drop manager_stub"
```

---

## Task 14: `main.py` wiring + pulse startup

**Files:**
- Modify: `src/project0/main.py`
- Manual test: `scripts/calendar_smoke.py` should still work (no changes)

**Design note:** This task is wiring-only — no new unit tests. We validate by starting the main process against a dev `.env` and watching the logs. The individual components are already covered by their own tests.

- [ ] **Step 1: Modify `src/project0/main.py`**

Add imports near the existing block:

```python
from project0.agents.manager import Manager, load_manager_config, load_manager_persona
from project0.agents.registry import AGENT_SPECS, register_manager, register_secretary
from project0.calendar.auth import load_or_acquire_credentials
from project0.calendar.client import GoogleCalendar
from project0.pulse import load_pulse_entries, run_pulse_loop
```

Inside `_run`, after the Secretary construction block and before the `AGENT_SPECS` sanity-check loop, add the Calendar + Manager block:

```python
    # Google Calendar client (shared; used by Manager and any future
    # calendar-using agent). Loads credentials via the installed-app flow
    # on first run, cached in settings.google_token_path thereafter.
    calendar_creds = load_or_acquire_credentials(
        token_path=settings.google_token_path,
        client_secrets_path=settings.google_client_secrets_path,
    )
    calendar = GoogleCalendar(
        credentials=calendar_creds,
        calendar_id=settings.google_calendar_id,
        user_tz=settings.user_tz,
    )
    log.info(
        "google calendar ready (calendar_id=%s tz=%s)",
        settings.google_calendar_id, settings.user_tz.key,
    )

    # Manager (replaces the legacy stub that used to live in
    # AGENT_REGISTRY at import time).
    manager_persona = load_manager_persona(Path("prompts/manager.md"))
    manager_cfg = load_manager_config(Path("prompts/manager.toml"))
    manager = Manager(
        llm=llm,
        calendar=calendar,
        memory=store.agent_memory("manager"),
        messages_store=store.messages(),
        persona=manager_persona,
        config=manager_cfg,
    )
    register_manager(manager.handle)
    log.info("manager registered (model=%s)", manager_cfg.model)

    # Pulse scheduler entries for Manager.
    pulse_entries = load_pulse_entries(Path("prompts/manager.toml"))
    log.info(
        "manager pulse entries: %s",
        [(e.name, e.every_seconds) for e in pulse_entries],
    )
```

Inside the existing `async with asyncio.TaskGroup() as tg:` block, alongside the bot-polling tasks, add:

```python
        for entry in pulse_entries:
            tg.create_task(
                run_pulse_loop(
                    entry=entry,
                    target_agent="manager",
                    orchestrator=orch,
                )
            )
            log.info("pulse task spawned: %s", entry.name)
```

- [ ] **Step 2: Update `.env.example`**

Append:

```
# Manager pulse bindings. MANAGER_PULSE_CHAT_ID is the Telegram chat id
# Manager's scheduled `check_calendar` pulse should deliver reminders to
# via the Secretary bot. Must match one of TELEGRAM_ALLOWED_CHAT_IDS.
MANAGER_PULSE_CHAT_ID=
```

- [ ] **Step 3: Syntax check by importing the module**

```
uv run python -c "from project0.main import main; print('ok')"
```

Expected: `ok`. No ImportError.

- [ ] **Step 4: Run full test suite**

```
uv run pytest -q
```

Expected: all green. No test touches `main.py` directly, so this mostly validates that the import graph is still consistent.

- [ ] **Step 5: Commit**

```
git add src/project0/main.py .env.example
git commit -m "feat(main): wire GoogleCalendar + Manager + pulse scheduler into composition root"
```

---

## Task 15: README update + smoke run instructions

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a 6c section to README.md**

Append under existing integration notes (pattern match the 6b section):

````markdown
## Sub-project 6c — Manager + pulse

Manager is now a real LLM agent with calendar tool use and scheduled pulses.

### Required env vars (in addition to 6a/6b)

- `MANAGER_PULSE_CHAT_ID` — integer Telegram chat id where the Manager's
  `check_calendar` pulse should deliver reminders (via Secretary). Must be
  one of `TELEGRAM_ALLOWED_CHAT_IDS`. Omit the variable to disable that
  pulse entry (the loader will raise at startup — which is what you want).

### Smoke run

```
uv run python -m project0.main
```

Expected log lines on a healthy start:

```
INFO project0 :: google calendar ready (calendar_id=primary tz=Asia/Shanghai)
INFO project0 :: manager registered (model=claude-sonnet-4-6)
INFO project0 :: manager pulse entries: [('check_calendar', 300)]
INFO project0 :: pulse task spawned: check_calendar
```

The first scheduled `check_calendar` tick fires ~5 minutes after startup
(not immediately). To iterate faster, temporarily lower `every_seconds`
in `prompts/manager.toml` to its floor of `10`.

### Manual test checklist

- [ ] DM to the Manager bot → model replies in-character (no calendar call
      required)
- [ ] Group mention "@manager 我明天有什么事" → model calls
      `calendar_list_events` and reports the results
- [ ] Ask Manager to remind you of something → Secretary receives the
      delegation envelope and sends a warm reminder message
- [ ] Let the `check_calendar` pulse fire at least once; with an upcoming
      event on the calendar, confirm Secretary posts a proactive reminder
      in `MANAGER_PULSE_CHAT_ID`
````

- [ ] **Step 2: Commit**

```
git add README.md
git commit -m "docs(6c): smoke-run instructions for Manager + pulse"
```

---

## Self-Review Checklist (read before declaring done)

- [ ] All 15 tasks committed in order
- [ ] `uv run pytest -q` green
- [ ] `uv run python -c "from project0.main import main"` green
- [ ] `grep -rn manager_stub src/ tests/` returns nothing
- [ ] `prompts/manager.md` and `prompts/manager.toml` exist and load cleanly
- [ ] `MANAGER_PULSE_CHAT_ID` documented in `.env.example` and README
- [ ] Spec coverage: every section of `2026-04-14-manager-agent-and-pulse-design.md` maps to at least one task
