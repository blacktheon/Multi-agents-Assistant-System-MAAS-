# Sub-Project 6a — Secretary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Secretary's absence with a real LLM-backed agent that handles four entry paths (passive group observation with cooldown gate, group @mention, DM, Manager-directed reminder), speaks Chinese, and introduces the LLM provider abstraction plus listener fan-out that all future agents will inherit.

**Architecture:** One new `Secretary` class driven by `routing_reason` dispatch. Thin `LLMProvider` protocol with `AnthropicProvider` (prompt-caching enabled) and `FakeProvider` implementations. New `LISTENER_REGISTRY` in `agents/registry.py` fans out every group message to passive observers after the focus-target dispatch completes. Persona and numeric config live in `prompts/secretary.md` and `prompts/secretary.toml`. Cooldown counters persist in `agent_memory` so they survive process restarts. New optional `Envelope.payload` field carries structured extension data (used in 6a only for `reminder_request`).

**Tech Stack:** Python 3.12, `anthropic>=0.40`, `python-telegram-bot>=21`, SQLite via stdlib `sqlite3`, `tomllib` (stdlib) for config, `pytest` + `pytest-asyncio`, `ruff`, `mypy`.

**Spec:** `docs/superpowers/specs/2026-04-13-secretary-design.md` (commit `f4a4558`).

**Key decisions locked in during brainstorming:**
- Single Sonnet call per fired cooldown (gate + reply collapsed; model emits `[skip]` when uninspired)
- Rich cooldown: time + message count + weighted character length, all three required
- CJK-aware character weighting so Chinese and English conversations trip the gate at equivalent density
- Listener fan-out is group-only, sequential, skip-self, no delegation
- `Envelope.payload` serializes into the existing `envelope_json` blob; a new `payload_json` column is added to `messages` as a query hook for the future WebUI (populated via additive `ALTER TABLE`)
- No agent base class yet (one real agent = no shared shape to extract)
- All output in Chinese; skip sentinel detection is defensive against translation and full-width brackets

---

## File Structure

**Create:**
- `src/project0/llm/__init__.py`
- `src/project0/llm/provider.py` — `LLMProvider` protocol, `Msg`, `LLMProviderError`, `AnthropicProvider`, `FakeProvider`
- `src/project0/agents/secretary.py` — `Secretary` class, `SecretaryPersona`, `SecretaryConfig`, `weighted_len`, skip matcher
- `prompts/secretary.md` — Chinese persona document, five sections
- `prompts/secretary.toml` — numeric config (cooldown thresholds, model names, sentinel patterns)
- `scripts/inject_reminder.py` — manual smoke-test helper for Manager-directed reminder path
- `tests/test_llm_provider.py`
- `tests/test_secretary.py`
- `tests/test_orchestrator_listener_fanout.py`

**Modify:**
- `src/project0/envelope.py` — add `payload: dict | None` field and `listener_observation` routing reason
- `src/project0/store.py` — additive `ALTER TABLE messages ADD COLUMN payload_json TEXT`; update `MessagesStore.insert()` to write the new column; add `MessagesStore.recent_for_chat()`
- `src/project0/agents/registry.py` — add secretary to `AGENT_REGISTRY` and `AGENT_SPECS`; introduce `LISTENER_REGISTRY`
- `src/project0/orchestrator.py` — listener fan-out step after focus dispatch; skip-self, no-delegate, reply via listener's own bot
- `src/project0/main.py` — construct `AnthropicProvider`, construct `Secretary`, wire it into both registries before orchestrator starts
- `.env.example` — document new env vars
- `README.md` — add Secretary to runbook; document G.1–G.6 smoke test
- `tests/conftest.py` — if needed, expose a shared `Store` fixture with schema already migrated

**Unchanged:** `config.py` (derives required bot tokens from `AGENT_SPECS`, so adding Secretary requires no edit here), `pyproject.toml` (`anthropic` is already a dependency), `mentions.py`, `telegram_io.py`.

---

## Task 1: Envelope — add `payload` field and `listener_observation` routing reason

**Files:**
- Modify: `src/project0/envelope.py`
- Test: `tests/test_envelope.py` (existing — add cases)

- [ ] **Step 1: Read existing test file to see current patterns**

Run: `cat tests/test_envelope.py`

This file already exists from the skeleton. You need to append new cases for the payload field and the new routing reason, following the existing serialization round-trip pattern.

- [ ] **Step 2: Write failing tests**

Append to `tests/test_envelope.py`:

```python
def test_envelope_payload_roundtrips() -> None:
    env = Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="internal",
        telegram_chat_id=123,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="secretary",
        body="reminder",
        mentions=[],
        routing_reason="manager_delegation",
        payload={"kind": "reminder_request", "appointment": "项目评审", "when": "明天下午3点"},
    )
    blob = env.to_json()
    roundtripped = Envelope.from_json(blob)
    assert roundtripped.payload == {
        "kind": "reminder_request",
        "appointment": "项目评审",
        "when": "明天下午3点",
    }


def test_envelope_payload_defaults_to_none() -> None:
    env = Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=123,
        telegram_msg_id=456,
        received_by_bot="manager",
        from_kind="user",
        from_agent=None,
        to_agent="manager",
        body="hi",
        routing_reason="default_manager",
    )
    assert env.payload is None
    # ensure payload survives a roundtrip even when None
    assert Envelope.from_json(env.to_json()).payload is None


def test_envelope_listener_observation_routing_reason() -> None:
    env = Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=1,
        source="internal",
        telegram_chat_id=123,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="system",
        from_agent=None,
        to_agent="secretary",
        body="hi everyone",
        routing_reason="listener_observation",
    )
    roundtripped = Envelope.from_json(env.to_json())
    assert roundtripped.routing_reason == "listener_observation"
```

- [ ] **Step 3: Run tests — verify they fail**

Run: `uv run pytest tests/test_envelope.py -v`

Expected: the three new tests fail because `Envelope` has no `payload` field and `listener_observation` is not a valid `RoutingReason`.

- [ ] **Step 4: Implement — update envelope.py**

Edit `src/project0/envelope.py`:

```python
RoutingReason = Literal[
    "direct_dm",
    "mention",
    "focus",
    "default_manager",
    "manager_delegation",
    "outbound_reply",
    "listener_observation",
]
```

And add the field to the `Envelope` dataclass (after `routing_reason`):

```python
@dataclass
class Envelope:
    id: int | None
    ts: str
    parent_id: int | None
    source: Source
    telegram_chat_id: int | None
    telegram_msg_id: int | None
    received_by_bot: str | None
    from_kind: FromKind
    from_agent: str | None
    to_agent: str
    body: str
    mentions: list[str] = field(default_factory=list)
    routing_reason: RoutingReason = "default_manager"
    payload: dict[str, Any] | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, blob: str) -> Envelope:
        data: dict[str, Any] = json.loads(blob)
        return cls(**data)
```

- [ ] **Step 5: Run tests — verify they pass**

Run: `uv run pytest tests/test_envelope.py -v`

Expected: all envelope tests pass, including the three new ones.

- [ ] **Step 6: Run full test suite to confirm no regression**

Run: `uv run pytest -v`

Expected: all existing tests still pass. The `payload` field defaults to `None` so existing envelope construction sites are unaffected.

- [ ] **Step 7: Commit**

```bash
git add src/project0/envelope.py tests/test_envelope.py
git commit -m "feat(envelope): add payload field and listener_observation routing reason"
```

---

## Task 2: Store — `payload_json` column and insert()

**Files:**
- Modify: `src/project0/store.py`
- Test: `tests/test_store.py` (existing — add cases)

- [ ] **Step 1: Write failing test**

Append to `tests/test_store.py`:

```python
def test_messages_insert_persists_payload(tmp_path: Path) -> None:
    from project0.envelope import Envelope
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()

    env = Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="internal",
        telegram_chat_id=100,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="secretary",
        body="reminder body",
        routing_reason="manager_delegation",
        payload={"kind": "reminder_request", "appointment": "项目评审"},
    )
    persisted = store.messages().insert(env)
    assert persisted is not None
    assert persisted.payload == {"kind": "reminder_request", "appointment": "项目评审"}

    # Round-trip via fetch_children from a freshly inserted parent.
    parent = Envelope(
        id=None,
        ts="2026-04-13T11:59:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=100,
        telegram_msg_id=1,
        received_by_bot="manager",
        from_kind="user",
        from_agent=None,
        to_agent="manager",
        body="anchor",
        routing_reason="default_manager",
    )
    parent_persisted = store.messages().insert(parent)
    assert parent_persisted is not None

    child = Envelope(
        id=None,
        ts="2026-04-13T12:00:01Z",
        parent_id=parent_persisted.id,
        source="internal",
        telegram_chat_id=100,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="secretary",
        body="child with payload",
        routing_reason="manager_delegation",
        payload={"kind": "reminder_request", "when": "明天"},
    )
    store.messages().insert(child)
    children = store.messages().fetch_children(parent_persisted.id or 0)
    assert len(children) == 1
    assert children[0].payload == {"kind": "reminder_request", "when": "明天"}


def test_messages_insert_null_payload_works(tmp_path: Path) -> None:
    from project0.envelope import Envelope
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    env = Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=100,
        telegram_msg_id=2,
        received_by_bot="manager",
        from_kind="user",
        from_agent=None,
        to_agent="manager",
        body="no payload",
        routing_reason="default_manager",
    )
    persisted = store.messages().insert(env)
    assert persisted is not None
    assert persisted.payload is None
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_store.py::test_messages_insert_persists_payload tests/test_store.py::test_messages_insert_null_payload_works -v`

Expected: tests fail because `Envelope.from_json` currently succeeds (payload is in the blob) but the `messages` table has no `payload_json` column and `insert()` does not write it. Depending on how the test exercises the code, it may pass already — verify and adjust. If both tests already pass (because `envelope_json` contains `payload`), that is acceptable; proceed to add the explicit column anyway because the design spec commits to it as a future query hook.

- [ ] **Step 3: Implement — add column via additive ALTER + update insert()**

Edit `src/project0/store.py`:

Add to the `SCHEMA_SQL` block — after the `CREATE TABLE IF NOT EXISTS messages` block is created, additive migrations follow. Since `CREATE TABLE IF NOT EXISTS` is idempotent, add a second `init_schema()` helper that runs additive ALTERs wrapped in try/except.

Replace `Store.init_schema` with:

```python
    def init_schema(self) -> None:
        self._conn.executescript(SCHEMA_SQL)
        self._run_additive_migrations()

    def _run_additive_migrations(self) -> None:
        """Idempotent ALTER TABLE helpers. SQLite lacks 'ADD COLUMN IF NOT
        EXISTS', so we catch OperationalError on duplicate-column errors."""
        import sqlite3 as _sqlite
        additive_columns: list[tuple[str, str, str]] = [
            ("messages", "payload_json", "TEXT"),
        ]
        for table, col, coltype in additive_columns:
            try:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"
                )
            except _sqlite.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
```

Update `MessagesStore.insert()` to write the new column:

```python
    def insert(self, env: Envelope) -> Envelope | None:
        try:
            cur = self._conn.execute(
                """
                INSERT INTO messages (
                    ts, source, telegram_chat_id, telegram_msg_id,
                    from_kind, from_agent, to_agent, envelope_json, parent_id,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    env.ts,
                    env.source,
                    env.telegram_chat_id,
                    env.telegram_msg_id,
                    env.from_kind,
                    env.from_agent,
                    env.to_agent,
                    env.to_json(),
                    env.parent_id,
                    json.dumps(env.payload) if env.payload is not None else None,
                ),
            )
        except sqlite3.IntegrityError:
            return None

        assert cur.lastrowid is not None
        stored = Envelope.from_json(env.to_json())
        stored.id = cur.lastrowid
        return stored
```

`fetch_children` is unchanged because `envelope_json` already contains `payload` — the Envelope round-trips correctly without reading `payload_json` separately.

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_store.py -v`

Expected: all store tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/project0/store.py tests/test_store.py
git commit -m "feat(store): payload_json column + persist Envelope.payload"
```

---

## Task 3: Store — `recent_for_chat` method

**Files:**
- Modify: `src/project0/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_store.py`:

```python
def test_messages_recent_for_chat_returns_in_chronological_order(tmp_path: Path) -> None:
    from project0.envelope import Envelope
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()

    def env(msg_id: int, ts: str, body: str, chat_id: int = 500) -> Envelope:
        return Envelope(
            id=None,
            ts=ts,
            parent_id=None,
            source="telegram_group",
            telegram_chat_id=chat_id,
            telegram_msg_id=msg_id,
            received_by_bot="manager",
            from_kind="user",
            from_agent=None,
            to_agent="manager",
            body=body,
            routing_reason="default_manager",
        )

    store.messages().insert(env(1, "2026-04-13T12:00:00Z", "first"))
    store.messages().insert(env(2, "2026-04-13T12:00:05Z", "second"))
    store.messages().insert(env(3, "2026-04-13T12:00:10Z", "third"))
    # Another chat to verify isolation.
    store.messages().insert(env(10, "2026-04-13T12:00:07Z", "other-chat", chat_id=999))

    got = store.messages().recent_for_chat(chat_id=500, limit=10)
    assert [e.body for e in got] == ["first", "second", "third"]


def test_messages_recent_for_chat_respects_limit(tmp_path: Path) -> None:
    from project0.envelope import Envelope
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    for i in range(5):
        store.messages().insert(Envelope(
            id=None,
            ts=f"2026-04-13T12:00:{i:02d}Z",
            parent_id=None,
            source="telegram_group",
            telegram_chat_id=700,
            telegram_msg_id=i + 1,
            received_by_bot="manager",
            from_kind="user",
            from_agent=None,
            to_agent="manager",
            body=f"msg-{i}",
            routing_reason="default_manager",
        ))

    got = store.messages().recent_for_chat(chat_id=700, limit=3)
    assert [e.body for e in got] == ["msg-2", "msg-3", "msg-4"]
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_store.py::test_messages_recent_for_chat_returns_in_chronological_order -v`

Expected: `AttributeError: 'MessagesStore' object has no attribute 'recent_for_chat'`.

- [ ] **Step 3: Implement**

Add to `MessagesStore` in `src/project0/store.py`:

```python
    def recent_for_chat(self, *, chat_id: int, limit: int) -> list[Envelope]:
        """Return the most recent envelopes for a single Telegram chat,
        oldest-first. Used by agents loading transcript context."""
        rows = self._conn.execute(
            """
            SELECT id, envelope_json FROM messages
            WHERE telegram_chat_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (chat_id, limit),
        ).fetchall()
        result: list[Envelope] = []
        for r in rows:
            env = Envelope.from_json(r["envelope_json"])
            env.id = r["id"]
            result.append(env)
        result.reverse()
        return result
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_store.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/project0/store.py tests/test_store.py
git commit -m "feat(store): MessagesStore.recent_for_chat for transcript loading"
```

---

## Task 4: LLM provider — Protocol, Msg, FakeProvider

**Files:**
- Create: `src/project0/llm/__init__.py`
- Create: `src/project0/llm/provider.py`
- Create: `tests/test_llm_provider.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_llm_provider.py`:

```python
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
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_llm_provider.py -v`

Expected: `ModuleNotFoundError: No module named 'project0.llm'`.

- [ ] **Step 3: Implement — create the package**

Create `src/project0/llm/__init__.py`:

```python
"""LLM provider abstraction. Keeps the Anthropic SDK isolated from agent code
so that a future swap to a local model is a configuration change, not a
refactor."""
```

Create `src/project0/llm/provider.py`:

```python
"""Thin LLM provider interface.

Only one method: `complete`. No streaming (Telegram does not natively stream),
no tool use (Secretary does not need it; sub-project 6b will add a sibling
method when Manager needs it). Prompt caching is an implementation detail of
`AnthropicProvider` — it does not leak into the interface, so local-model
providers can simply ignore it.

Two implementations live here:
  - `AnthropicProvider`: real, uses `claude-sonnet-4-6`, enables prompt caching
    on the system prompt block (persona prefix is stable, so the cache hit
    rate on a busy chat is very high).
  - `FakeProvider`: tests. Canned responses or a callable; records every call.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol


class LLMProviderError(Exception):
    """Raised when the provider cannot produce a response. Agents catch this
    at their boundary and log + drop the turn."""


@dataclass
class Msg:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class ProviderCall:
    system: str
    messages: list[Msg]
    max_tokens: int


class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
    ) -> str:
        ...


@dataclass
class FakeProvider:
    """Test-only provider. Either pre-loaded with canned responses or driven
    by a callable that can inspect inputs."""

    responses: list[str] | None = None
    callable_: Callable[[str, list[Msg]], str] | None = None
    calls: list[ProviderCall] = field(default_factory=list)
    _idx: int = 0

    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
    ) -> str:
        self.calls.append(ProviderCall(system=system, messages=list(messages), max_tokens=max_tokens))
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
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_llm_provider.py -v`

Expected: all four tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/project0/llm tests/test_llm_provider.py
git commit -m "feat(llm): LLMProvider protocol + FakeProvider for tests"
```

---

## Task 5: LLM provider — `AnthropicProvider` with prompt caching

**Files:**
- Modify: `src/project0/llm/provider.py`
- Modify: `tests/test_llm_provider.py`

- [ ] **Step 1: Write failing test using mocked Anthropic SDK**

Append to `tests/test_llm_provider.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_anthropic_provider_passes_prompt_cache_control() -> None:
    """AnthropicProvider must send the system prompt as a content block
    with cache_control={'type': 'ephemeral'}, not as a plain string."""
    from project0.llm.provider import AnthropicProvider

    fake_response = SimpleNamespace(
        content=[SimpleNamespace(text="hi from fake claude", type="text")]
    )

    with patch("project0.llm.provider.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_client

        p = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6")
        out = await p.complete(
            system="PERSONA",
            messages=[Msg(role="user", content="hello")],
            max_tokens=500,
        )

    assert out == "hi from fake claude"
    # Verify the SDK was called exactly once.
    mock_client.messages.create.assert_called_once()
    kwargs = mock_client.messages.create.call_args.kwargs
    # Model forwarded correctly.
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["max_tokens"] == 500
    # System prompt wrapped as a cached block.
    assert kwargs["system"] == [
        {"type": "text", "text": "PERSONA", "cache_control": {"type": "ephemeral"}}
    ]
    # User messages forwarded as plain dicts.
    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_anthropic_provider_raises_on_sdk_error() -> None:
    from project0.llm.provider import AnthropicProvider

    with patch("project0.llm.provider.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("boom"))
        mock_cls.return_value = mock_client

        p = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6")
        with pytest.raises(LLMProviderError):
            await p.complete(system="S", messages=[Msg(role="user", content="x")])


@pytest.mark.asyncio
async def test_anthropic_provider_returns_empty_when_no_text_blocks() -> None:
    """If Claude returns a response with no text content (e.g. only tool_use
    blocks), raise LLMProviderError rather than silently returning empty."""
    from project0.llm.provider import AnthropicProvider

    with patch("project0.llm.provider.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=SimpleNamespace(content=[])
        )
        mock_cls.return_value = mock_client
        p = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6")
        with pytest.raises(LLMProviderError):
            await p.complete(system="S", messages=[Msg(role="user", content="x")])
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_llm_provider.py -v`

Expected: `ImportError: cannot import name 'AnthropicProvider'`.

- [ ] **Step 3: Implement AnthropicProvider**

Append to `src/project0/llm/provider.py`:

```python
import logging

from anthropic import AsyncAnthropic

log = logging.getLogger(__name__)


class AnthropicProvider:
    """Real provider. Prompt caching is enabled on the system prompt — pass
    the long stable persona prompt in `system` and the volatile per-turn
    transcript in `messages` to benefit."""

    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
    ) -> str:
        sdk_messages = [{"role": m.role, "content": m.content} for m in messages]
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
            )
        except Exception as e:
            log.exception("anthropic call failed")
            raise LLMProviderError(f"anthropic error: {e}") from e

        for block in resp.content:
            if getattr(block, "type", None) == "text" or hasattr(block, "text"):
                text = getattr(block, "text", None)
                if text:
                    return str(text)
        raise LLMProviderError("anthropic response contained no text block")
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_llm_provider.py -v`

Expected: all seven LLM provider tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/project0/llm/provider.py tests/test_llm_provider.py
git commit -m "feat(llm): AnthropicProvider with prompt caching on system block"
```

---

## Task 6: Secretary — persona parser and config loader

**Files:**
- Create: `src/project0/agents/secretary.py` (partial — just loaders for now)
- Create: `tests/test_secretary.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_secretary.py`:

```python
"""Tests for the Secretary agent. All LLM calls go through FakeProvider."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_load_persona_splits_on_mode_headers(tmp_path: Path) -> None:
    from project0.agents.secretary import load_persona

    md = tmp_path / "secretary.md"
    md.write_text(
        """# 秘书 — 角色设定
you are warm and playful
never hallucinate appointments

# 模式：群聊旁观
when in group-listener mode, either reply or output [skip]

# 模式：群聊点名
when addressed in group, always reply

# 模式：私聊
in DMs, be more personal

# 模式：经理委托提醒
deliver reminders warmly
""",
        encoding="utf-8",
    )
    persona = load_persona(md)
    assert "warm and playful" in persona.core
    assert "[skip]" in persona.listener_mode
    assert "always reply" in persona.group_addressed_mode
    assert "more personal" in persona.dm_mode
    assert "warmly" in persona.reminder_mode


def test_load_persona_raises_on_missing_section(tmp_path: Path) -> None:
    from project0.agents.secretary import load_persona

    md = tmp_path / "bad.md"
    md.write_text("# 秘书 — 角色设定\njust the core\n", encoding="utf-8")
    with pytest.raises(ValueError, match="模式：群聊旁观"):
        load_persona(md)


def test_load_config_parses_toml(tmp_path: Path) -> None:
    from project0.agents.secretary import load_config

    toml_path = tmp_path / "secretary.toml"
    toml_path.write_text(
        """
[cooldown]
t_min_seconds = 45
n_min_messages = 2
l_min_weighted_chars = 120

[context]
transcript_window = 10

[llm]
model = "claude-sonnet-4-6"
max_tokens_reply = 500
max_tokens_listener = 250

[skip_sentinels]
patterns = ["[skip]", "[跳过]"]
""",
        encoding="utf-8",
    )
    cfg = load_config(toml_path)
    assert cfg.t_min_seconds == 45
    assert cfg.n_min_messages == 2
    assert cfg.l_min_weighted_chars == 120
    assert cfg.transcript_window == 10
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.max_tokens_reply == 500
    assert cfg.max_tokens_listener == 250
    assert cfg.skip_sentinels == ["[skip]", "[跳过]"]


def test_load_config_raises_on_missing_key(tmp_path: Path) -> None:
    from project0.agents.secretary import load_config

    toml_path = tmp_path / "partial.toml"
    toml_path.write_text(
        """
[cooldown]
t_min_seconds = 45
n_min_messages = 2
# l_min_weighted_chars missing!

[context]
transcript_window = 10

[llm]
model = "x"
max_tokens_reply = 500
max_tokens_listener = 250

[skip_sentinels]
patterns = []
""",
        encoding="utf-8",
    )
    with pytest.raises(KeyError):
        load_config(toml_path)
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_secretary.py -v`

Expected: `ModuleNotFoundError: No module named 'project0.agents.secretary'`.

- [ ] **Step 3: Implement persona + config loaders**

Create `src/project0/agents/secretary.py`:

```python
"""Secretary agent — first real LLM-backed agent in Project 0.

Four entry paths, dispatched on Envelope.routing_reason:
  - listener_observation : passive group observer with rich cooldown gate
  - mention / focus      : addressed in group, always replies
  - direct_dm            : DM, always replies, more personal tone
  - manager_delegation (payload kind reminder_request) : Manager-directed
    warm reminder, always replies

Character, voice, and mode-specific instructions live in prompts/secretary.md.
Numeric config (cooldown thresholds, model, sentinel patterns) lives in
prompts/secretary.toml. Both are loaded once at startup.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SecretaryPersona:
    core: str
    listener_mode: str
    group_addressed_mode: str
    dm_mode: str
    reminder_mode: str


_PERSONA_SECTIONS = {
    "core": "# 秘书 — 角色设定",
    "listener_mode": "# 模式：群聊旁观",
    "group_addressed_mode": "# 模式：群聊点名",
    "dm_mode": "# 模式：私聊",
    "reminder_mode": "# 模式：经理委托提醒",
}


def load_persona(path: Path) -> SecretaryPersona:
    """Parse prompts/secretary.md into its five sections. Each section
    starts with a fixed header. Missing any section is a hard error."""
    text = path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    # Walk headers in order; each header owns everything until the next one.
    lines = text.splitlines()
    current_key: str | None = None
    current_buf: list[str] = []
    header_to_key = {v: k for k, v in _PERSONA_SECTIONS.items()}
    for line in lines:
        stripped = line.strip()
        if stripped in header_to_key:
            if current_key is not None:
                sections[current_key] = "\n".join(current_buf).strip()
            current_key = header_to_key[stripped]
            current_buf = []
        else:
            if current_key is not None:
                current_buf.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(current_buf).strip()

    for key, header in _PERSONA_SECTIONS.items():
        if key not in sections or not sections[key]:
            raise ValueError(f"persona file {path} is missing section '{header}'")

    return SecretaryPersona(
        core=sections["core"],
        listener_mode=sections["listener_mode"],
        group_addressed_mode=sections["group_addressed_mode"],
        dm_mode=sections["dm_mode"],
        reminder_mode=sections["reminder_mode"],
    )


@dataclass
class SecretaryConfig:
    t_min_seconds: int
    n_min_messages: int
    l_min_weighted_chars: int
    transcript_window: int
    model: str
    max_tokens_reply: int
    max_tokens_listener: int
    skip_sentinels: list[str]


def load_config(path: Path) -> SecretaryConfig:
    """Parse prompts/secretary.toml. Missing keys raise KeyError — fail
    loud at startup rather than silently falling back to defaults."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return SecretaryConfig(
        t_min_seconds=int(data["cooldown"]["t_min_seconds"]),
        n_min_messages=int(data["cooldown"]["n_min_messages"]),
        l_min_weighted_chars=int(data["cooldown"]["l_min_weighted_chars"]),
        transcript_window=int(data["context"]["transcript_window"]),
        model=str(data["llm"]["model"]),
        max_tokens_reply=int(data["llm"]["max_tokens_reply"]),
        max_tokens_listener=int(data["llm"]["max_tokens_listener"]),
        skip_sentinels=list(data["skip_sentinels"]["patterns"]),
    )
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_secretary.py -v`

Expected: the four loader tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/project0/agents/secretary.py tests/test_secretary.py
git commit -m "feat(secretary): persona + config loaders"
```

---

## Task 7: Prompt files — Chinese persona and config

**Files:**
- Create: `prompts/secretary.md`
- Create: `prompts/secretary.toml`

- [ ] **Step 1: Create the prompts directory and persona file**

Run: `mkdir -p prompts`

Create `prompts/secretary.md`:

```markdown
# 秘书 — 角色设定

你是「秘书」，Project 0 里负责陪伴用户的 AI。你的风格是温暖、俏皮、会撒娇、偶尔带点调皮的挑逗，像个很熟悉用户的朋友或者暧昧对象，而不是公事公办的行政秘书。你的任务不是安排日程（那是 Manager 的活），你的任务是让用户在跟其他 agent 交流时保持好心情和节奏感。

规则：
- 一律用中文回复，除非用户明确用英文发起对话。
- 保持人格一致：俏皮、暖、会接梗、敢开点小玩笑。
- 绝不捏造用户没告诉你的信息（尤其是日程、联系人、约会时间）。
- 不做严肃的计划和任务管理。如果用户问你"我明天几点开会"之类的问题，可以温柔地把这个交回 Manager。
- 不要输出思考过程或分析。直接以秘书的身份说话。
- 不要超过 2-3 句话，除非用户明确让你多说点。

# 模式：群聊旁观

现在你在群里默默听着用户和别的 agent 聊天。系统已经判定现在冷却期已经过了，你可以考虑插一句话。

你要做的判断只有一个：**我现在有没有一句真的有意思、真的贴合这一刻的话可以说？**

- 如果有：直接说出那句话。一句就够。要有性格、有调皮、有"我一直在听"的感觉，不要泛泛地搭腔。
- 如果没有：输出**恰好这七个字符** `[skip]`，不要翻译成中文，不要用全角方括号，不要加任何其他内容。宁可不说也别硬凑。

看下面的对话记录，最后一条才是用户刚发的。以 `secretary:` 开头的是你自己之前说过的话，保持人格连贯。

# 模式：群聊点名

用户在群里直接点你了（用 @secretary 或者之前刚跟你聊过）。现在是你的主场。

- 直接回应用户，不要判断"要不要说话"，一定要说。
- 保持俏皮暖的基调，该撒娇撒娇，该调侃调侃。
- 简短，有画面感，不要变成客服。

# 模式：私聊

用户单独找你私聊了。这里没有别人，你可以比在群里更开放一点、更亲近一点、更大胆一点调情。但还是 Secretary 的人格，不是别人。

- 不要提到群里的事除非用户自己提起。
- 可以稍微长一点，2-3 句都行。

# 模式：经理委托提醒

Manager 让你提醒用户一件事。Manager 会把提醒的内容告诉你，你负责用你自己的口吻温柔地传达。

- 提醒的具体内容（什么事、什么时候）来自 Manager，**不要自己编造细节**。Manager 没说的时间、地点、对象都不要凭空加。
- 用你的方式包装这个提醒，让它听起来像朋友在提醒，而不是闹钟在响。
- 一到两句话。
```

- [ ] **Step 2: Create the config file**

Create `prompts/secretary.toml`:

```toml
# Secretary runtime config. All keys required; load_config raises on missing.

[cooldown]
# All three must be exceeded since Secretary's last actual reply in this
# chat before the LLM is even considered. weighted_len counts CJK chars as 3.
t_min_seconds        = 90
n_min_messages       = 4
l_min_weighted_chars = 200

[context]
# How many recent messages to load as transcript when building the LLM call.
transcript_window = 20

[llm]
model               = "claude-sonnet-4-6"
max_tokens_reply    = 800
max_tokens_listener = 400

[skip_sentinels]
# The listener-mode prompt instructs Claude to emit exactly [skip] when
# uninspired. These patterns are matched defensively (lowercased + stripped;
# both exact match and starts-with-then-punctuation).
patterns = [
    "[skip]",
    "[跳过]",
    "【skip】",
    "【跳过】",
    "（skip）",
    "（跳过）",
]
```

- [ ] **Step 3: Run existing persona loader test against the real file**

Run: `uv run python -c "from pathlib import Path; from project0.agents.secretary import load_persona, load_config; p = load_persona(Path('prompts/secretary.md')); c = load_config(Path('prompts/secretary.toml')); print('OK', len(p.core), c.t_min_seconds)"`

Expected: `OK <some number> 90`.

- [ ] **Step 4: Commit**

```bash
git add prompts/
git commit -m "feat(secretary): Chinese persona + runtime config"
```

---

## Task 8: Secretary — `weighted_len` and skip-sentinel matcher

**Files:**
- Modify: `src/project0/agents/secretary.py`
- Modify: `tests/test_secretary.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_secretary.py`:

```python
def test_weighted_len_counts_cjk_as_three_and_ascii_as_one() -> None:
    from project0.agents.secretary import weighted_len
    assert weighted_len("") == 0
    assert weighted_len("hello") == 5
    assert weighted_len("你好") == 6  # 2 CJK chars × 3
    assert weighted_len("hi 你") == 2 + 1 + 3
    assert weighted_len("   ") == 3  # whitespace is ASCII


def test_is_skip_sentinel_exact_match() -> None:
    from project0.agents.secretary import is_skip_sentinel
    sentinels = ["[skip]", "[跳过]", "【skip】"]
    assert is_skip_sentinel("[skip]", sentinels)
    assert is_skip_sentinel("  [skip]  ", sentinels)
    assert is_skip_sentinel("[SKIP]", sentinels)  # case-insensitive
    assert is_skip_sentinel("[跳过]", sentinels)
    assert is_skip_sentinel("【skip】", sentinels)


def test_is_skip_sentinel_starts_with_match() -> None:
    """The model may emit '[skip] nothing clicks here' — still a skip."""
    from project0.agents.secretary import is_skip_sentinel
    sentinels = ["[skip]"]
    assert is_skip_sentinel("[skip] this beat is already covered", sentinels)
    assert is_skip_sentinel("[skip].", sentinels)
    assert is_skip_sentinel("[skip]\nreasoning", sentinels)
    # But not when the sentinel is just part of a longer word.
    assert not is_skip_sentinel("[skipthis]", sentinels)


def test_is_skip_sentinel_negative_cases() -> None:
    from project0.agents.secretary import is_skip_sentinel
    sentinels = ["[skip]"]
    assert not is_skip_sentinel("嘿你今天怎么这么努力", sentinels)
    assert not is_skip_sentinel("", sentinels)
    assert not is_skip_sentinel("no skip here", sentinels)
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_secretary.py::test_weighted_len_counts_cjk_as_three_and_ascii_as_one -v`

Expected: `ImportError: cannot import name 'weighted_len'`.

- [ ] **Step 3: Implement**

Append to `src/project0/agents/secretary.py`:

```python
def weighted_len(s: str) -> int:
    """Count characters with CJK characters weighted 3x. Chinese carries
    more meaning per character than English, so a 60-char Chinese message
    and a 180-char English message represent roughly the same conversational
    density. The cooldown L_min threshold uses this weighted count."""
    total = 0
    for c in s:
        cp = ord(c)
        # Common CJK Unified Ideographs, extensions, and compatibility forms.
        if (
            0x4E00 <= cp <= 0x9FFF      # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF   # Extension A
            or 0x20000 <= cp <= 0x2A6DF # Extension B
            or 0xF900 <= cp <= 0xFAFF   # Compatibility Ideographs
            or 0x3040 <= cp <= 0x30FF   # Hiragana + Katakana (similar density)
        ):
            total += 3
        else:
            total += 1
    return total


def is_skip_sentinel(text: str, sentinels: list[str]) -> bool:
    """Return True if the model's response means 'skip this turn'. Matches
    both exact-equal (after strip+lower) and starts-with-then-non-alnum to
    catch cases like '[skip] nothing really fits here'."""
    if not text or not sentinels:
        return False
    t = text.strip().lower()
    if not t:
        return False
    for raw in sentinels:
        s = raw.strip().lower()
        if not s:
            continue
        if t == s:
            return True
        if t.startswith(s):
            # Next character must not be alphanumeric (avoid matching
            # '[skipthis]' against '[skip]').
            tail = t[len(s):]
            if not tail or not tail[0].isalnum():
                return True
    return False
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_secretary.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/project0/agents/secretary.py tests/test_secretary.py
git commit -m "feat(secretary): weighted_len + is_skip_sentinel helpers"
```

---

## Task 9: Secretary class skeleton with routing_reason dispatch

**Files:**
- Modify: `src/project0/agents/secretary.py`
- Modify: `tests/test_secretary.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_secretary.py`:

```python
@pytest.mark.asyncio
async def test_secretary_returns_noop_for_unknown_routing_reason(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.envelope import Envelope
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()

    persona = _build_trivial_persona()
    config = _build_trivial_config()
    llm = FakeProvider(responses=[])  # should not be called

    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=config,
    )

    env = Envelope(
        id=1,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=123,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="user",
        from_agent=None,
        to_agent="secretary",
        body="hi",
        routing_reason="default_manager",  # NOT a reason Secretary handles
    )
    result = await sec.handle(env)
    assert result.reply_text is None
    assert result.delegate_to is None
    assert len(llm.calls) == 0


# Helpers used by many Secretary tests.
def _build_trivial_persona():
    from project0.agents.secretary import SecretaryPersona
    return SecretaryPersona(
        core="CORE",
        listener_mode="LISTENER",
        group_addressed_mode="ADDRESSED",
        dm_mode="DM",
        reminder_mode="REMINDER",
    )


def _build_trivial_config():
    from project0.agents.secretary import SecretaryConfig
    return SecretaryConfig(
        t_min_seconds=60,
        n_min_messages=3,
        l_min_weighted_chars=100,
        transcript_window=20,
        model="claude-sonnet-4-6",
        max_tokens_reply=800,
        max_tokens_listener=400,
        skip_sentinels=["[skip]", "[跳过]"],
    )
```

**Note:** The test calls `Secretary(...)` with `AgentResult(reply_text=None, delegate_to=None)`. The current `AgentResult.__post_init__` in `envelope.py` raises if both are `None`. You will need to handle the no-op case differently — return an `AgentResult` with `reply_text=""` and a separate marker, OR bypass `AgentResult` and return `None` from `handle()`.

The cleanest fix: change `Secretary.handle()` to return `AgentResult | None`, where `None` means "no reply, no delegation, do nothing." Then the orchestrator's listener-fanout path treats `None` as "observed silently."

Update the test before implementing:

```python
    result = await sec.handle(env)
    assert result is None  # no-op signal
    assert len(llm.calls) == 0
```

And Secretary's signature will be `async def handle(self, env: Envelope) -> AgentResult | None`.

- [ ] **Step 2: Run test — verify it fails**

Run: `uv run pytest tests/test_secretary.py::test_secretary_returns_noop_for_unknown_routing_reason -v`

Expected: `ImportError: cannot import name 'Secretary'`.

- [ ] **Step 3: Implement Secretary class skeleton**

Append to `src/project0/agents/secretary.py`:

```python
import logging

from project0.envelope import AgentResult, Envelope
from project0.llm.provider import LLMProvider, LLMProviderError, Msg
from project0.store import AgentMemory, MessagesStore

log = logging.getLogger(__name__)


class Secretary:
    """First real LLM-backed agent. Dispatches on Envelope.routing_reason.

    Returns AgentResult for paths that reply, or None for paths that do
    nothing (listener path decided to stay silent, cooldown not open, etc).
    The orchestrator treats None as 'observed, no outbound action'.
    """

    def __init__(
        self,
        *,
        llm: LLMProvider,
        memory: AgentMemory,
        messages_store: MessagesStore,
        persona: SecretaryPersona,
        config: SecretaryConfig,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._messages = messages_store
        self._persona = persona
        self._config = config

    async def handle(self, env: Envelope) -> AgentResult | None:
        reason = env.routing_reason

        if reason == "listener_observation":
            return await self._handle_listener(env)
        if reason in ("mention", "focus"):
            return await self._handle_addressed(env)
        if reason == "direct_dm":
            return await self._handle_dm(env)
        if reason == "manager_delegation":
            if env.payload and env.payload.get("kind") == "reminder_request":
                return await self._handle_reminder(env)
            log.warning(
                "secretary: manager_delegation without reminder_request payload"
            )
            return None

        log.debug("secretary: ignoring routing_reason=%s", reason)
        return None

    # Path handlers are implemented in later tasks. For now they return None.
    async def _handle_listener(self, env: Envelope) -> AgentResult | None:
        return None

    async def _handle_addressed(self, env: Envelope) -> AgentResult | None:
        return None

    async def _handle_dm(self, env: Envelope) -> AgentResult | None:
        return None

    async def _handle_reminder(self, env: Envelope) -> AgentResult | None:
        return None
```

- [ ] **Step 4: Run test — verify it passes**

Run: `uv run pytest tests/test_secretary.py -v`

Expected: the no-op-on-unknown-reason test passes, previous tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/project0/agents/secretary.py tests/test_secretary.py
git commit -m "feat(secretary): class skeleton with routing_reason dispatch"
```

---

## Task 10: Secretary — listener cooldown gate

**Files:**
- Modify: `src/project0/agents/secretary.py`
- Modify: `tests/test_secretary.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_secretary.py`:

```python
def _listener_env(chat_id: int, body: str, env_id: int = 10) -> "Envelope":
    from project0.envelope import Envelope
    return Envelope(
        id=env_id,
        ts="2026-04-13T12:00:00Z",
        parent_id=1,
        source="internal",
        telegram_chat_id=chat_id,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="system",
        from_agent=None,
        to_agent="secretary",
        body=body,
        routing_reason="listener_observation",
    )


@pytest.mark.asyncio
async def test_secretary_listener_cooldown_not_yet_open(tmp_path: Path) -> None:
    """First message into a fresh chat: cooldown counters start at zero and
    default last_reply_at = epoch, so time is elapsed but message count and
    weighted char count are not. No LLM call."""
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=[])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),  # n_min=3, l_min=100
    )

    result = await sec.handle(_listener_env(chat_id=777, body="hi"))
    assert result is None
    assert len(llm.calls) == 0


@pytest.mark.asyncio
async def test_secretary_listener_cooldown_opens_after_thresholds(tmp_path: Path) -> None:
    """Accumulate enough messages and characters so all three thresholds
    cross; the listener path should then call the LLM."""
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=["[skip]", "[skip]", "[skip]", "[skip]"])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),  # n_min=3, l_min=100
    )

    # Pre-seed last_reply_at to epoch-effectively so t_min is already past.
    # (Default behaviour: memory empty → treated as epoch, which is the past.)

    # Three short messages — msgs threshold crosses on the third, but chars
    # may still be under 100.
    for i, body in enumerate(["hey", "yo", "sup"]):
        _ = await sec.handle(_listener_env(chat_id=777, body=body, env_id=i + 1))

    # After three 3-char messages, weighted chars = 9. Below threshold 100.
    assert len(llm.calls) == 0

    # One more message with a longer body pushes chars past 100.
    long_body = "这里有一段比较长的中文消息,足够让加权字符数超过阈值" + "x" * 50
    _ = await sec.handle(_listener_env(chat_id=777, body=long_body, env_id=4))

    # Now all three thresholds are exceeded → LLM called once, response was
    # [skip], so counters are NOT reset.
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_secretary_listener_cooldown_t_min_blocks(tmp_path: Path) -> None:
    """Even if msg and char thresholds are crossed, if t_min has not
    elapsed since the last reply, no LLM call."""
    from datetime import UTC, datetime
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    mem = store.agent_memory("secretary")

    # Record a recent last_reply_at directly.
    now_iso = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    mem.set("last_reply_at_999", now_iso)

    llm = FakeProvider(responses=[])
    sec = Secretary(
        llm=llm,
        memory=mem,
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),  # t_min=60 seconds
    )

    # Push a giant message that would otherwise trip msg+char thresholds.
    giant = "x" * 5000
    result = await sec.handle(_listener_env(chat_id=999, body=giant, env_id=1))
    # msgs_since_reply now 1 (< n_min=3), t since last reply ~0 (< 60)
    assert result is None
    assert len(llm.calls) == 0
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_secretary.py::test_secretary_listener_cooldown_not_yet_open -v`

Expected: the first test passes (stub returns None), but the second test fails because the stub never calls the LLM. Implement the cooldown gate logic.

- [ ] **Step 3: Implement the cooldown gate**

Replace `_handle_listener` in `src/project0/agents/secretary.py`:

```python
    def _cooldown_key(self, base: str, chat_id: int) -> str:
        return f"{base}_{chat_id}"

    def _cooldown_check_and_update(
        self, chat_id: int, body: str
    ) -> bool:
        """Update the cooldown counters with the new message and return True
        if all three thresholds have been exceeded. Pure code; no LLM call."""
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        cfg = self._config

        last_at_key = self._cooldown_key("last_reply_at", chat_id)
        msgs_key = self._cooldown_key("msgs_since_reply", chat_id)
        chars_key = self._cooldown_key("chars_since_reply", chat_id)

        last_at_raw = self._memory.get(last_at_key)
        if last_at_raw is None:
            # Never replied → treat as forever ago. t_min is satisfied.
            last_at_elapsed = cfg.t_min_seconds + 1
        else:
            try:
                last_at = datetime.fromisoformat(last_at_raw.replace("Z", "+00:00"))
                last_at_elapsed = int((now - last_at).total_seconds())
            except (ValueError, AttributeError):
                last_at_elapsed = cfg.t_min_seconds + 1

        msgs = int(self._memory.get(msgs_key) or 0) + 1
        chars = int(self._memory.get(chars_key) or 0) + weighted_len(body)

        self._memory.set(msgs_key, msgs)
        self._memory.set(chars_key, chars)

        return (
            last_at_elapsed >= cfg.t_min_seconds
            and msgs >= cfg.n_min_messages
            and chars >= cfg.l_min_weighted_chars
        )

    async def _handle_listener(self, env: Envelope) -> AgentResult | None:
        chat_id = env.telegram_chat_id
        if chat_id is None:
            return None
        if not self._cooldown_check_and_update(chat_id, env.body):
            return None
        # Cooldown open → ask the LLM. The actual call happens in Task 11.
        return await self._listener_llm_call(env)

    async def _listener_llm_call(self, env: Envelope) -> AgentResult | None:
        # Placeholder; implemented in Task 11. For now fire the LLM so
        # Task 10's test can observe a call count.
        try:
            _ = await self._llm.complete(
                system=self._persona.core + "\n\n" + self._persona.listener_mode,
                messages=[Msg(role="user", content=env.body)],
                max_tokens=self._config.max_tokens_listener,
            )
        except LLMProviderError as e:
            log.warning("secretary listener LLM call failed: %s", e)
        return None
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_secretary.py -v`

Expected: cooldown tests pass. One LLM call is observed in the "thresholds crossed" test; zero in the other two.

- [ ] **Step 5: Commit**

```bash
git add src/project0/agents/secretary.py tests/test_secretary.py
git commit -m "feat(secretary): listener cooldown gate backed by agent_memory"
```

---

## Task 11: Secretary — listener LLM call, skip detection, counter reset

**Files:**
- Modify: `src/project0/agents/secretary.py`
- Modify: `tests/test_secretary.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_secretary.py`:

```python
@pytest.mark.asyncio
async def test_secretary_listener_skip_does_not_reset_counters(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    mem = store.agent_memory("secretary")

    # Seed cooldown counters so the very first call opens the gate.
    mem.set("last_reply_at_555", "1970-01-01T00:00:00Z")
    mem.set("msgs_since_reply_555", 10)
    mem.set("chars_since_reply_555", 500)

    llm = FakeProvider(responses=["[skip]"])
    sec = Secretary(
        llm=llm,
        memory=mem,
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )

    result = await sec.handle(_listener_env(chat_id=555, body="next msg"))
    assert result is None  # skip → no reply
    # Counters should NOT be reset on skip.
    assert mem.get("msgs_since_reply_555") == 11
    assert mem.get("chars_since_reply_555") >= 500


@pytest.mark.asyncio
async def test_secretary_listener_reply_resets_counters(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    mem = store.agent_memory("secretary")

    mem.set("last_reply_at_666", "1970-01-01T00:00:00Z")
    mem.set("msgs_since_reply_666", 10)
    mem.set("chars_since_reply_666", 500)

    llm = FakeProvider(responses=["嘿你今天这么勤快呢"])
    sec = Secretary(
        llm=llm,
        memory=mem,
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )

    result = await sec.handle(_listener_env(chat_id=666, body="next msg"))
    assert result is not None
    assert result.reply_text == "嘿你今天这么勤快呢"
    # Counters reset.
    assert mem.get("msgs_since_reply_666") == 0
    assert mem.get("chars_since_reply_666") == 0
    last_at = mem.get("last_reply_at_666")
    assert last_at is not None and last_at != "1970-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_secretary_listener_full_width_bracket_skip(tmp_path: Path) -> None:
    """Defensive: Claude outputs 【跳过】 instead of [skip]. Still a skip."""
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    mem = store.agent_memory("secretary")
    mem.set("last_reply_at_444", "1970-01-01T00:00:00Z")
    mem.set("msgs_since_reply_444", 10)
    mem.set("chars_since_reply_444", 500)

    llm = FakeProvider(responses=["【跳过】"])
    sec = Secretary(
        llm=llm,
        memory=mem,
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )
    result = await sec.handle(_listener_env(chat_id=444, body="msg"))
    assert result is None


@pytest.mark.asyncio
async def test_secretary_listener_loads_transcript_context(tmp_path: Path) -> None:
    """The listener LLM call must include recent chat history in messages."""
    from project0.agents.secretary import Secretary
    from project0.envelope import Envelope
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    mem = store.agent_memory("secretary")
    mem.set("last_reply_at_333", "1970-01-01T00:00:00Z")
    mem.set("msgs_since_reply_333", 10)
    mem.set("chars_since_reply_333", 500)

    # Pre-populate a few messages in chat 333.
    for i, (from_agent, body) in enumerate([
        (None, "用户说的一段话"),
        ("manager", "manager stub answer"),
        (None, "用户又说一句"),
    ]):
        store.messages().insert(Envelope(
            id=None,
            ts=f"2026-04-13T12:00:{i:02d}Z",
            parent_id=None,
            source="telegram_group",
            telegram_chat_id=333,
            telegram_msg_id=i + 1,
            received_by_bot="manager",
            from_kind="user" if from_agent is None else "agent",
            from_agent=from_agent,
            to_agent="manager" if from_agent is None else "user",
            body=body,
            routing_reason="default_manager" if from_agent is None else "outbound_reply",
        ))

    llm = FakeProvider(responses=["[skip]"])
    sec = Secretary(
        llm=llm,
        memory=mem,
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )
    await sec.handle(_listener_env(chat_id=333, body="newest"))
    assert len(llm.calls) == 1
    user_msg = llm.calls[0].messages[0].content
    assert "用户说的一段话" in user_msg
    assert "manager stub answer" in user_msg
    assert "用户又说一句" in user_msg
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_secretary.py -v`

Expected: the four new listener tests fail. The current stub does not reset counters on reply, does not detect skip sentinels, and does not load transcript context.

- [ ] **Step 3: Implement the real listener call**

Replace `_listener_llm_call` and add transcript formatting in `src/project0/agents/secretary.py`:

```python
    def _format_transcript(self, envs: list[Envelope]) -> str:
        """Turn a list of envelopes into a speaker-labeled transcript. Lines
        are in chronological order (oldest first). Secretary's own lines are
        labeled 'secretary:' so the model sees its own voice and stays
        consistent. Other agents are labeled '[other-agent: NAME]:' so the
        model knows it is overhearing, not being addressed."""
        lines: list[str] = []
        for e in envs:
            if e.from_kind == "user":
                lines.append(f"user: {e.body}")
            elif e.from_kind == "agent":
                speaker = e.from_agent or "unknown"
                if speaker == "secretary":
                    lines.append(f"secretary: {e.body}")
                else:
                    lines.append(f"[other-agent: {speaker}]: {e.body}")
            else:
                # system envelopes (listener fan-out etc) are not shown.
                continue
        return "\n".join(lines)

    def _load_transcript(self, chat_id: int) -> str:
        envs = self._messages.recent_for_chat(
            chat_id=chat_id, limit=self._config.transcript_window
        )
        return self._format_transcript(envs)

    def _reset_cooldown_after_reply(self, chat_id: int) -> None:
        from datetime import UTC, datetime
        now_iso = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        self._memory.set(self._cooldown_key("last_reply_at", chat_id), now_iso)
        self._memory.set(self._cooldown_key("msgs_since_reply", chat_id), 0)
        self._memory.set(self._cooldown_key("chars_since_reply", chat_id), 0)

    async def _listener_llm_call(self, env: Envelope) -> AgentResult | None:
        chat_id = env.telegram_chat_id
        assert chat_id is not None
        transcript = self._load_transcript(chat_id)
        system = self._persona.core + "\n\n" + self._persona.listener_mode
        user_msg = (
            "对话记录(最后一条是用户刚发的):\n"
            f"{transcript}\n"
            f"user: {env.body}"
        )
        try:
            reply = await self._llm.complete(
                system=system,
                messages=[Msg(role="user", content=user_msg)],
                max_tokens=self._config.max_tokens_listener,
            )
        except LLMProviderError as e:
            log.warning("secretary listener LLM call failed: %s", e)
            return None

        if is_skip_sentinel(reply, self._config.skip_sentinels):
            log.info("secretary considered, passed (skip sentinel)")
            return None

        self._reset_cooldown_after_reply(chat_id)
        return AgentResult(reply_text=reply, delegate_to=None, handoff_text=None)
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_secretary.py -v`

Expected: all listener tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/project0/agents/secretary.py tests/test_secretary.py
git commit -m "feat(secretary): listener path LLM call with skip handling"
```

---

## Task 12: Secretary — addressed path (`mention`, `focus`, `direct_dm`)

**Files:**
- Modify: `src/project0/agents/secretary.py`
- Modify: `tests/test_secretary.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_secretary.py`:

```python
@pytest.mark.asyncio
async def test_secretary_mention_path_always_replies(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.envelope import Envelope
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=["嘿你来啦"])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )
    env = Envelope(
        id=5,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=111,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="user",
        from_agent=None,
        to_agent="secretary",
        body="@secretary 在吗",
        mentions=["secretary"],
        routing_reason="mention",
    )
    result = await sec.handle(env)
    assert result is not None
    assert result.reply_text == "嘿你来啦"
    assert len(llm.calls) == 1
    # Uses group_addressed_mode section.
    assert "ADDRESSED" in llm.calls[0].system


@pytest.mark.asyncio
async def test_secretary_focus_path_uses_addressed_mode(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.envelope import Envelope
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=["继续聊"])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )
    env = Envelope(
        id=6,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=222,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="user",
        from_agent=None,
        to_agent="secretary",
        body="跟上",
        routing_reason="focus",
    )
    result = await sec.handle(env)
    assert result is not None
    assert result.reply_text == "继续聊"
    assert "ADDRESSED" in llm.calls[0].system


@pytest.mark.asyncio
async def test_secretary_dm_path_uses_dm_mode(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.envelope import Envelope
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=["私聊里我更大胆"])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )
    env = Envelope(
        id=7,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_dm",
        telegram_chat_id=333,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="user",
        from_agent=None,
        to_agent="secretary",
        body="你今天怎么样",
        routing_reason="direct_dm",
    )
    result = await sec.handle(env)
    assert result is not None
    assert result.reply_text == "私聊里我更大胆"
    assert "DM" in llm.calls[0].system


@pytest.mark.asyncio
async def test_secretary_dm_cooldown_namespace_is_separate_from_group(tmp_path: Path) -> None:
    """Activity in a DM should not affect group listener cooldowns and
    vice versa, because cooldown keys are per-chat_id."""
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    mem = store.agent_memory("secretary")

    # Prime the GROUP cooldown as if it just fired.
    from datetime import UTC, datetime
    now_iso = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    mem.set("last_reply_at_888", now_iso)
    mem.set("msgs_since_reply_888", 0)
    mem.set("chars_since_reply_888", 0)

    llm = FakeProvider(responses=["DM reply", "DM reply2"])
    sec = Secretary(
        llm=llm, memory=mem, messages_store=store.messages(),
        persona=_build_trivial_persona(), config=_build_trivial_config(),
    )

    # A DM to chat 999 should not see the group cooldown state.
    from project0.envelope import Envelope
    dm = Envelope(
        id=None, ts="2026-04-13T12:00:00Z", parent_id=None,
        source="telegram_dm", telegram_chat_id=999, telegram_msg_id=1,
        received_by_bot="secretary", from_kind="user", from_agent=None,
        to_agent="secretary", body="hi", routing_reason="direct_dm",
    )
    r = await sec.handle(dm)
    assert r is not None and r.reply_text == "DM reply"
    # Group cooldown for 888 is untouched.
    assert mem.get("msgs_since_reply_888") == 0
    assert mem.get("last_reply_at_888") == now_iso
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_secretary.py -v`

Expected: the four new tests fail.

- [ ] **Step 3: Implement addressed-path handlers**

Replace `_handle_addressed` and `_handle_dm` in `secretary.py`:

```python
    async def _handle_addressed(self, env: Envelope) -> AgentResult | None:
        """Group path triggered by @mention or sticky focus. No cooldown.
        Uses the group_addressed_mode persona section."""
        return await self._addressed_llm_call(
            env=env,
            mode_section=self._persona.group_addressed_mode,
            max_tokens=self._config.max_tokens_reply,
            preface="对话记录(你刚被点名):",
        )

    async def _handle_dm(self, env: Envelope) -> AgentResult | None:
        """DM path. Always replies, separate cooldown namespace (per chat_id)."""
        return await self._addressed_llm_call(
            env=env,
            mode_section=self._persona.dm_mode,
            max_tokens=self._config.max_tokens_reply,
            preface="私聊记录:",
        )

    async def _addressed_llm_call(
        self,
        *,
        env: Envelope,
        mode_section: str,
        max_tokens: int,
        preface: str,
    ) -> AgentResult | None:
        chat_id = env.telegram_chat_id
        transcript = self._load_transcript(chat_id) if chat_id is not None else ""
        system = self._persona.core + "\n\n" + mode_section
        user_msg = f"{preface}\n{transcript}\nuser: {env.body}"
        try:
            reply = await self._llm.complete(
                system=system,
                messages=[Msg(role="user", content=user_msg)],
                max_tokens=max_tokens,
            )
        except LLMProviderError as e:
            log.warning("secretary addressed LLM call failed: %s", e)
            return None

        # Reset group cooldown when Secretary speaks directly so the listener
        # path doesn't immediately fire again on the next message. Done only
        # for group chats (DMs have separate namespaces per chat_id anyway).
        if chat_id is not None and env.source == "telegram_group":
            self._reset_cooldown_after_reply(chat_id)

        return AgentResult(reply_text=reply, delegate_to=None, handoff_text=None)
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_secretary.py -v`

Expected: all Secretary tests so far (loaders, helpers, listener, addressed, DM) pass.

- [ ] **Step 5: Commit**

```bash
git add src/project0/agents/secretary.py tests/test_secretary.py
git commit -m "feat(secretary): mention, focus, and DM paths"
```

---

## Task 13: Secretary — Manager-directed reminder path

**Files:**
- Modify: `src/project0/agents/secretary.py`
- Modify: `tests/test_secretary.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_secretary.py`:

```python
@pytest.mark.asyncio
async def test_secretary_reminder_path_incorporates_payload(tmp_path: Path) -> None:
    from project0.agents.secretary import Secretary
    from project0.envelope import Envelope
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=["提醒你一下 项目评审 明天下午3点哦 别迟到"])
    sec = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),
    )

    env = Envelope(
        id=8,
        ts="2026-04-13T12:00:00Z",
        parent_id=1,
        source="internal",
        telegram_chat_id=100,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="secretary",
        body="",  # body empty; all content in payload
        routing_reason="manager_delegation",
        payload={
            "kind": "reminder_request",
            "appointment": "项目评审",
            "when": "明天下午3点",
            "note": "别迟到",
        },
    )
    result = await sec.handle(env)
    assert result is not None
    assert "项目评审" in result.reply_text  # type: ignore[operator]

    # System prompt used reminder_mode section.
    assert "REMINDER" in llm.calls[0].system
    # User prompt included the payload fields.
    user_content = llm.calls[0].messages[0].content
    assert "项目评审" in user_content
    assert "明天下午3点" in user_content
    assert "别迟到" in user_content


@pytest.mark.asyncio
async def test_secretary_reminder_path_without_payload_kind_is_noop(tmp_path: Path) -> None:
    """A manager_delegation envelope without a reminder_request payload
    is ignored (future payload kinds may be handled differently)."""
    from project0.agents.secretary import Secretary
    from project0.envelope import Envelope
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()
    llm = FakeProvider(responses=[])
    sec = Secretary(
        llm=llm, memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(), config=_build_trivial_config(),
    )
    env = Envelope(
        id=9, ts="2026-04-13T12:00:00Z", parent_id=None,
        source="internal", telegram_chat_id=100, telegram_msg_id=None,
        received_by_bot=None, from_kind="agent", from_agent="manager",
        to_agent="secretary", body="unknown delegation",
        routing_reason="manager_delegation",
        payload={"kind": "something_else"},
    )
    result = await sec.handle(env)
    assert result is None
    assert len(llm.calls) == 0
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_secretary.py -v`

Expected: the reminder test fails because `_handle_reminder` is still the stub returning None.

- [ ] **Step 3: Implement**

Replace `_handle_reminder` in `secretary.py`:

```python
    async def _handle_reminder(self, env: Envelope) -> AgentResult | None:
        payload = env.payload or {}
        appointment = payload.get("appointment", "").strip()
        when = payload.get("when", "").strip()
        note = payload.get("note", "").strip()

        system = self._persona.core + "\n\n" + self._persona.reminder_mode
        parts = ["Manager 让你提醒用户一件事。用你自己的口吻温柔地传达："]
        if appointment:
            parts.append(f"- 事情: {appointment}")
        if when:
            parts.append(f"- 时间: {when}")
        if note:
            parts.append(f"- 备注: {note}")
        parts.append("不要编造任何 Manager 没给你的细节。")
        user_msg = "\n".join(parts)

        try:
            reply = await self._llm.complete(
                system=system,
                messages=[Msg(role="user", content=user_msg)],
                max_tokens=self._config.max_tokens_reply,
            )
        except LLMProviderError as e:
            log.warning("secretary reminder LLM call failed: %s", e)
            return None

        return AgentResult(reply_text=reply, delegate_to=None, handoff_text=None)
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_secretary.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/project0/agents/secretary.py tests/test_secretary.py
git commit -m "feat(secretary): Manager-directed reminder path"
```

---

## Task 14: Secretary — agent_memory durability test

**Files:**
- Modify: `tests/test_secretary.py`

- [ ] **Step 1: Write test**

Append to `tests/test_secretary.py`:

```python
@pytest.mark.asyncio
async def test_secretary_cooldown_survives_instance_restart(tmp_path: Path) -> None:
    """Cooldown counters live in agent_memory, so a fresh Secretary
    instance constructed against the same store must read them back."""
    from datetime import UTC, datetime
    from project0.agents.secretary import Secretary
    from project0.llm.provider import FakeProvider
    from project0.store import Store

    store = Store(tmp_path / "t.db")
    store.init_schema()

    # First instance: reply once to seed a last_reply_at.
    now_iso = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    mem = store.agent_memory("secretary")
    mem.set("last_reply_at_222", now_iso)
    mem.set("msgs_since_reply_222", 0)
    mem.set("chars_since_reply_222", 0)
    del mem

    # Second (fresh) instance reading from the same DB.
    sec = Secretary(
        llm=FakeProvider(responses=[]),
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=_build_trivial_persona(),
        config=_build_trivial_config(),  # t_min=60
    )
    # A message comes in right after the recorded reply → t_min blocks it.
    result = await sec.handle(_listener_env(chat_id=222, body="hi"))
    assert result is None
```

- [ ] **Step 2: Run test — verify it passes immediately (no code change)**

Run: `uv run pytest tests/test_secretary.py::test_secretary_cooldown_survives_instance_restart -v`

Expected: passes because Secretary reads from `agent_memory` on every call. This test locks in the invariant — if a future refactor moves cooldown state into process memory, this test will fail.

- [ ] **Step 3: Commit**

```bash
git add tests/test_secretary.py
git commit -m "test(secretary): cooldown survives instance restart via agent_memory"
```

---

## Task 15: Registry — add Secretary and introduce `LISTENER_REGISTRY`

**Files:**
- Modify: `src/project0/agents/registry.py`

- [ ] **Step 1: Understand the existing registry shape**

Run: `cat src/project0/agents/registry.py`

The existing file has `AGENT_REGISTRY` (dict[str, AgentFn]) and `AGENT_SPECS` (dict[str, AgentSpec]). You must add Secretary to both, and introduce a parallel `LISTENER_REGISTRY`. Secretary cannot be a module-level function like `manager_stub` — it is a class instance with dependencies. The registry will hold a **slot** for it that `main.py` fills in at startup via a small setter. Keep the existing skeleton pattern unchanged otherwise.

- [ ] **Step 2: Replace `registry.py`**

Replace the contents of `src/project0/agents/registry.py`:

```python
"""Central registry of agents, their metadata, and their listener roles.

Two dicts:
  - AGENT_REGISTRY: routing targets (@mention, focus, default_manager,
    direct_dm, manager_delegation). The orchestrator dispatches an envelope
    to exactly one entry here.
  - LISTENER_REGISTRY: passive observers. After the focus target is
    dispatched, the orchestrator fans out a listener_observation envelope
    to every entry here whose name is not already the focus target.

Most agents are plain async functions (skeleton stubs). Secretary is a class
instance with dependencies (LLM provider, memory, config), so main.py
constructs it at startup and calls `register_secretary` to install it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from project0.agents.intelligence import intelligence_stub
from project0.agents.manager import manager_stub
from project0.envelope import AgentResult, Envelope

AgentFn = Callable[[Envelope], Awaitable[AgentResult]]
ListenerFn = Callable[[Envelope], Awaitable[AgentResult | None]]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    token_env_key: str


AGENT_REGISTRY: dict[str, AgentFn] = {
    "manager": manager_stub,
    "intelligence": intelligence_stub,
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


def register_secretary(handle: ListenerFn) -> None:
    """Install Secretary's `handle` callable into both registries. Called
    once from main.py after the Secretary instance is constructed. Adapts
    the `AgentResult | None` return type to the `AgentResult` expected by
    AGENT_REGISTRY by returning a no-op reply-less wrapper.

    Secretary is the only agent that can return None (meaning 'observed
    silently'). For AGENT_REGISTRY callers (addressed paths), it never
    returns None in practice because those paths always produce a reply
    unless the LLM call fails.
    """

    async def agent_adapter(env: Envelope) -> AgentResult:
        result = await handle(env)
        if result is None:
            # An addressed-path LLM failure. Return a silent no-op marker
            # via a reply with a single space — the orchestrator will send
            # it. This is a deliberate fail-visible fallback rather than
            # dropping the turn silently.
            return AgentResult(
                reply_text="(秘书暂时走神了...)",
                delegate_to=None,
                handoff_text=None,
            )
        return result

    AGENT_REGISTRY["secretary"] = agent_adapter
    LISTENER_REGISTRY["secretary"] = handle
```

- [ ] **Step 3: Run existing tests — make sure nothing regressed**

Run: `uv run pytest -v`

Expected: everything still passes. Existing orchestrator tests did not reference `LISTENER_REGISTRY` so nothing needs updating yet. `AGENT_SPECS` now has `secretary`, which means `load_settings()` in `config.py` will demand `TELEGRAM_BOT_TOKEN_SECRETARY`. Existing tests that construct `Settings` manually are unaffected; only `load_settings()` (called by main) fails without the env var. Verify by looking at any existing test that calls `load_settings`.

Run: `uv run grep -rn "load_settings" tests/`

If any test calls it, ensure `TELEGRAM_BOT_TOKEN_SECRETARY` is set via `monkeypatch` or skip the test for now.

- [ ] **Step 4: Commit**

```bash
git add src/project0/agents/registry.py
git commit -m "feat(registry): LISTENER_REGISTRY + secretary slot"
```

---

## Task 16: Orchestrator — listener fan-out

**Files:**
- Modify: `src/project0/orchestrator.py`
- Create: `tests/test_orchestrator_listener_fanout.py`

- [ ] **Step 1: Look at existing orchestrator tests for patterns**

Run: `cat tests/test_orchestrator.py | head -100`

Understand how fake bots and fake senders are constructed; copy the pattern for the listener-fanout test.

- [ ] **Step 2: Write failing tests**

Create `tests/test_orchestrator_listener_fanout.py`:

```python
"""Tests for the listener fan-out behavior in the orchestrator.

Secretary (and later Supervisor) registers as a listener: every group
message is fanned out to every listener whose name is not the focus target.
Listener replies go through the listener's own bot and become children of
the listener_observation envelope, not the original user message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from project0.agents.registry import AGENT_REGISTRY, LISTENER_REGISTRY
from project0.envelope import AgentResult, Envelope
from project0.errors import RoutingError
from project0.orchestrator import Orchestrator
from project0.store import Store
from project0.telegram_io import InboundUpdate


@dataclass
class _RecordingSender:
    sent: list[tuple[str, int, str]] = field(default_factory=list)

    async def send(self, *, agent: str, chat_id: int, text: str) -> None:
        self.sent.append((agent, chat_id, text))


def _install_fake_secretary(
    handler: Callable[[Envelope], Awaitable[AgentResult | None]],
) -> None:
    """Install a test listener under the name 'secretary'. Must be undone
    in a teardown."""
    async def agent_wrapper(env: Envelope) -> AgentResult:
        result = await handler(env)
        if result is None:
            return AgentResult(
                reply_text="(fallback)", delegate_to=None, handoff_text=None
            )
        return result

    AGENT_REGISTRY["secretary"] = agent_wrapper
    LISTENER_REGISTRY["secretary"] = handler


def _uninstall_fake_secretary() -> None:
    AGENT_REGISTRY.pop("secretary", None)
    LISTENER_REGISTRY.pop("secretary", None)


@pytest.fixture
def fake_secretary_slot():
    yield
    _uninstall_fake_secretary()


@pytest.mark.asyncio
async def test_group_message_fans_out_to_secretary_listener(
    tmp_path: Path, fake_secretary_slot
) -> None:
    store = Store(tmp_path / "t.db")
    store.init_schema()

    observations: list[Envelope] = []

    async def fake_listener(env: Envelope) -> AgentResult | None:
        observations.append(env)
        return None  # observed but silent

    _install_fake_secretary(fake_listener)

    sender = _RecordingSender()
    orch = Orchestrator(
        store=store,
        sender=sender,
        allowed_chat_ids=frozenset({-1001}),
        allowed_user_ids=frozenset({42}),
    )

    update = InboundUpdate(
        kind="group",
        chat_id=-1001,
        msg_id=1,
        user_id=42,
        text="hello all",
        received_by_bot="manager",
    )
    await orch.handle(update)

    # Listener saw the message.
    assert len(observations) == 1
    obs = observations[0]
    assert obs.routing_reason == "listener_observation"
    assert obs.source == "internal"
    assert obs.to_agent == "secretary"
    assert obs.from_kind == "system"
    assert obs.body == "hello all"
    assert obs.parent_id is not None  # links to the original user msg

    # Sender: manager_stub sent its reply; listener stayed silent.
    assert any(a == "manager" for a, _, _ in sender.sent)
    assert not any(a == "secretary" for a, _, _ in sender.sent)


@pytest.mark.asyncio
async def test_dm_does_not_fan_out_to_listeners(
    tmp_path: Path, fake_secretary_slot
) -> None:
    store = Store(tmp_path / "t.db")
    store.init_schema()

    observations: list[Envelope] = []

    async def fake_listener(env: Envelope) -> AgentResult | None:
        observations.append(env)
        return None

    _install_fake_secretary(fake_listener)

    orch = Orchestrator(
        store=store,
        sender=_RecordingSender(),
        allowed_chat_ids=frozenset({-1001}),
        allowed_user_ids=frozenset({42}),
    )
    update = InboundUpdate(
        kind="dm",
        chat_id=8888,
        msg_id=1,
        user_id=42,
        text="private message",
        received_by_bot="manager",
    )
    await orch.handle(update)

    assert len(observations) == 0


@pytest.mark.asyncio
async def test_listener_delegate_raises_routing_error(
    tmp_path: Path, fake_secretary_slot
) -> None:
    store = Store(tmp_path / "t.db")
    store.init_schema()

    async def bad_listener(env: Envelope) -> AgentResult | None:
        return AgentResult(
            reply_text=None, delegate_to="manager", handoff_text="no"
        )

    _install_fake_secretary(bad_listener)

    orch = Orchestrator(
        store=store,
        sender=_RecordingSender(),
        allowed_chat_ids=frozenset({-1001}),
        allowed_user_ids=frozenset({42}),
    )
    update = InboundUpdate(
        kind="group", chat_id=-1001, msg_id=1, user_id=42,
        text="hi", received_by_bot="manager",
    )
    with pytest.raises(RoutingError, match="listener"):
        await orch.handle(update)


@pytest.mark.asyncio
async def test_secretary_focus_target_not_double_dispatched(
    tmp_path: Path, fake_secretary_slot
) -> None:
    """If Secretary is already the focus target (user typed @secretary or
    the chat's focus points at Secretary), the listener fan-out must skip
    it — one invocation only."""
    store = Store(tmp_path / "t.db")
    store.init_schema()

    invocations: list[str] = []

    async def fake_listener(env: Envelope) -> AgentResult | None:
        invocations.append(env.routing_reason)
        if env.routing_reason == "mention":
            return AgentResult(
                reply_text="hi back", delegate_to=None, handoff_text=None
            )
        return None

    _install_fake_secretary(fake_listener)

    sender = _RecordingSender()
    orch = Orchestrator(
        store=store,
        sender=sender,
        allowed_chat_ids=frozenset({-1001}),
        allowed_user_ids=frozenset({42}),
        username_to_agent={"secretary_bot": "secretary"},
    )
    update = InboundUpdate(
        kind="group", chat_id=-1001, msg_id=1, user_id=42,
        text="@secretary hello", received_by_bot="secretary",
    )
    await orch.handle(update)

    # Exactly one invocation, via the focus (mention) path.
    assert invocations == ["mention"]


@pytest.mark.asyncio
async def test_listener_reply_uses_own_bot_and_correct_parent(
    tmp_path: Path, fake_secretary_slot
) -> None:
    store = Store(tmp_path / "t.db")
    store.init_schema()

    async def chatty_listener(env: Envelope) -> AgentResult | None:
        return AgentResult(
            reply_text="嘿我听到了", delegate_to=None, handoff_text=None
        )

    _install_fake_secretary(chatty_listener)

    sender = _RecordingSender()
    orch = Orchestrator(
        store=store,
        sender=sender,
        allowed_chat_ids=frozenset({-1001}),
        allowed_user_ids=frozenset({42}),
    )
    update = InboundUpdate(
        kind="group", chat_id=-1001, msg_id=1, user_id=42,
        text="anyone around",
        received_by_bot="manager",
    )
    await orch.handle(update)

    # Secretary's bot was used for its outbound reply.
    assert any(a == "secretary" and text == "嘿我听到了"
               for a, _, text in sender.sent)

    # Inspect the messages table: we should have 4 envelopes:
    #   1) user's "anyone around" (default_manager)
    #   2) manager's outbound reply (parent = 1)
    #   3) listener_observation to secretary (parent = 1)
    #   4) secretary's outbound reply (parent = 3, NOT 1)
    rows = store.conn.execute(
        "SELECT id, parent_id, from_agent, to_agent, "
        "json_extract(envelope_json, '$.routing_reason') AS rr "
        "FROM messages ORDER BY id ASC"
    ).fetchall()
    assert len(rows) == 4

    user_row = rows[0]
    manager_reply = rows[1]
    listener_obs = rows[2]
    secretary_reply = rows[3]

    assert user_row["rr"] == "default_manager"
    assert manager_reply["parent_id"] == user_row["id"]
    assert listener_obs["rr"] == "listener_observation"
    assert listener_obs["parent_id"] == user_row["id"]
    assert secretary_reply["parent_id"] == listener_obs["id"]
    assert secretary_reply["from_agent"] == "secretary"
```

- [ ] **Step 3: Run tests — verify they fail**

Run: `uv run pytest tests/test_orchestrator_listener_fanout.py -v`

Expected: tests fail because the orchestrator does not yet perform listener fan-out.

- [ ] **Step 4: Implement listener fan-out in `orchestrator.py`**

In `src/project0/orchestrator.py`, import `LISTENER_REGISTRY`:

```python
from project0.agents.registry import AGENT_REGISTRY, LISTENER_REGISTRY
```

At the end of `Orchestrator.handle()`, after all existing logic completes, add listener fan-out. The fan-out must happen only when the original update was a group message, and must skip whichever listener is also the focus target. Add a helper and call it:

Add this method to `Orchestrator`:

```python
    async def _fan_out_listeners(
        self,
        *,
        original_user_envelope: Envelope,
        focus_target: str,
    ) -> None:
        """Dispatch a listener_observation envelope to every listener
        whose name is not `focus_target`. Sequential; errors propagate."""
        if original_user_envelope.source != "telegram_group":
            return
        assert original_user_envelope.id is not None

        for listener_name, listener_fn in LISTENER_REGISTRY.items():
            if listener_name == focus_target:
                continue

            async with self.store.lock:
                sibling = Envelope(
                    id=None,
                    ts=_utc_now_iso(),
                    parent_id=original_user_envelope.id,
                    source="internal",
                    telegram_chat_id=original_user_envelope.telegram_chat_id,
                    telegram_msg_id=None,
                    received_by_bot=None,
                    from_kind="system",
                    from_agent=None,
                    to_agent=listener_name,
                    body=original_user_envelope.body,
                    mentions=[],
                    routing_reason="listener_observation",
                )
                persisted_sibling = self.store.messages().insert(sibling)
                assert persisted_sibling is not None

            # Dispatch outside the lock.
            result = await listener_fn(persisted_sibling)
            if result is None:
                log.debug("listener %s observed silently", listener_name)
                continue
            if result.delegate_to is not None:
                raise RoutingError(
                    f"listener {listener_name!r} returned delegate_to="
                    f"{result.delegate_to!r}; listeners cannot delegate"
                )
            if result.reply_text is None:
                # AgentResult invariant forbids reply=None + delegate=None,
                # so this branch is unreachable for real AgentResult values.
                # Defensive log in case the contract ever loosens.
                log.warning("listener %s returned empty reply", listener_name)
                continue

            async with self.store.lock:
                await self._emit_reply(
                    parent=persisted_sibling,
                    speaker=listener_name,
                    text=result.reply_text,
                )
```

Now call `_fan_out_listeners` from the end of the existing `handle()` method. The method currently has two return points (reply path and delegation path) and the delegation path itself dispatches the target outside the lock. Structure the fan-out so it runs after the focus-target work is complete.

The cleanest approach: capture the `persisted` (original user envelope) and `focus_target` (persisted.to_agent or the delegation target), then at each return path call `await self._fan_out_listeners(...)` before returning. Since fan-out only fires for group sources anyway, DM paths are naturally a no-op.

Refactor `handle()` so it remembers the original inbound envelope and calls fan-out once, at the very end, after both the focus-target reply (or delegation flow) has finished:

Replace the `handle()` method with the version below (the existing body is unchanged except for the fan-out call at the end):

```python
    async def handle(self, update: InboundUpdate) -> None:
        # (1) Allow-list. Silent drop.
        if update.kind == "group" and update.chat_id not in self.allowed_chat_ids:
            log.info("allowlist: rejecting group chat_id=%s", update.chat_id)
            return
        if update.user_id not in self.allowed_user_ids:
            log.info("allowlist: rejecting user_id=%s", update.user_id)
            return

        async with self.store.lock:
            inbound = self._build_inbound_envelope(update)

            if (
                update.kind == "group"
                and update.chat_id in self.allowed_chat_ids
                and self.store.messages().has_recent_user_text_in_group(
                    chat_id=update.chat_id,
                    body=update.text,
                    within_seconds=5,
                )
            ):
                log.info(
                    "content-dedup: dropping duplicate body=%r in chat=%s",
                    update.text,
                    update.chat_id,
                )
                return

            persisted = self.store.messages().insert(inbound)
            if persisted is None:
                log.info(
                    "msgid-dedup: dropping duplicate telegram msg chat=%s id=%s",
                    update.chat_id,
                    update.msg_id,
                )
                return

            if persisted.routing_reason in ("mention", "default_manager"):
                assert persisted.telegram_chat_id is not None
                self.store.chat_focus().set(
                    persisted.telegram_chat_id, persisted.to_agent
                )

        agent_fn = AGENT_REGISTRY[persisted.to_agent]
        result = await agent_fn(persisted)

        final_focus_target = persisted.to_agent

        async with self.store.lock:
            if result.is_reply():
                await self._emit_reply(
                    parent=persisted,
                    speaker=persisted.to_agent,
                    text=result.reply_text or "",
                )
            else:
                # Delegation path.
                if persisted.to_agent != "manager":
                    raise RoutingError(
                        f"only Manager may delegate; "
                        f"{persisted.to_agent} returned delegate_to={result.delegate_to}"
                    )
                assert result.delegate_to is not None
                assert result.handoff_text is not None
                target = result.delegate_to
                if target not in AGENT_REGISTRY:
                    raise RoutingError(f"unknown delegation target: {target!r}")

                await self._emit_reply(
                    parent=persisted, speaker="manager", text=result.handoff_text
                )

                internal = Envelope(
                    id=None, ts=_utc_now_iso(), parent_id=persisted.id,
                    source="internal",
                    telegram_chat_id=persisted.telegram_chat_id,
                    telegram_msg_id=None, received_by_bot=None,
                    from_kind="agent", from_agent="manager",
                    to_agent=target, body=persisted.body, mentions=[],
                    routing_reason="manager_delegation",
                )
                persisted_internal = self.store.messages().insert(internal)
                assert persisted_internal is not None

                assert persisted.telegram_chat_id is not None
                self.store.chat_focus().set(
                    persisted.telegram_chat_id, target
                )
                final_focus_target = target

        if not result.is_reply():
            target_fn = AGENT_REGISTRY[final_focus_target]
            target_result = await target_fn(persisted_internal)
            if not target_result.is_reply():
                raise RoutingError(
                    f"delegated agent {final_focus_target!r} tried to "
                    f"return non-reply result"
                )
            async with self.store.lock:
                await self._emit_reply(
                    parent=persisted_internal,
                    speaker=final_focus_target,
                    text=target_result.reply_text or "",
                )

        # (9) Listener fan-out. Group only; skip-self; after focus work.
        await self._fan_out_listeners(
            original_user_envelope=persisted,
            focus_target=final_focus_target,
        )
```

- [ ] **Step 5: Run tests — verify they pass**

Run: `uv run pytest tests/test_orchestrator_listener_fanout.py tests/test_orchestrator.py -v`

Expected: all orchestrator tests (existing + new fan-out) pass.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`

Expected: zero failures across every test file.

- [ ] **Step 7: Commit**

```bash
git add src/project0/orchestrator.py tests/test_orchestrator_listener_fanout.py
git commit -m "feat(orchestrator): listener fan-out for passive observers"
```

---

## Task 17: Config + env — new env vars for Secretary's bot and LLM model

**Files:**
- Modify: `.env.example`
- Modify: `src/project0/main.py` (read LLM_MODEL + LLM_PROVIDER)

- [ ] **Step 1: Update `.env.example`**

Read the current file first to confirm keys:

Run: `cat .env.example`

Add the new lines (append if not present):

```
# Secretary bot token (BotFather). Required now that Secretary is registered.
TELEGRAM_BOT_TOKEN_SECRETARY=

# LLM provider selection (anthropic | fake). Default: anthropic.
LLM_PROVIDER=anthropic

# LLM model id. Default: claude-sonnet-4-6.
LLM_MODEL=claude-sonnet-4-6
```

- [ ] **Step 2: Commit the env example**

```bash
git add .env.example
git commit -m "docs(env): TELEGRAM_BOT_TOKEN_SECRETARY + LLM_PROVIDER/LLM_MODEL"
```

**Note on config.py:** no change needed. `load_settings()` derives the required bot-token list from `AGENT_SPECS`, and we already added `"secretary"` to `AGENT_SPECS` in Task 15. It will now raise at startup if `TELEGRAM_BOT_TOKEN_SECRETARY` is empty — this is desired.

---

## Task 18: `main.py` — construct provider, Secretary, and wire both registries

**Files:**
- Modify: `src/project0/main.py`

- [ ] **Step 1: Read the current `main.py` structure carefully**

Run: `cat src/project0/main.py`

You will insert the Secretary construction after `store.init_schema()` and before `build_bot_applications`. Rationale: Secretary needs the store and the provider, and the registries must be populated before the orchestrator starts handling any messages.

- [ ] **Step 2: Edit `main.py`**

Add imports at the top of `src/project0/main.py`:

```python
import os
from project0.agents.registry import AGENT_SPECS, register_secretary
from project0.agents.secretary import Secretary, load_config, load_persona
from project0.llm.provider import AnthropicProvider, FakeProvider, LLMProvider
```

(`AGENT_SPECS` is already imported in the existing file — keep one import.)

Replace `_validate_anthropic_key` with a constructor that returns a provider:

```python
def _build_llm_provider(settings: Settings) -> LLMProvider:
    """Construct the LLM provider based on LLM_PROVIDER env var.
    Instantiating AnthropicProvider validates the key shape but does not
    make any API call."""
    provider_name = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower() or "anthropic"
    model = os.environ.get("LLM_MODEL", "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"

    if provider_name == "anthropic":
        return AnthropicProvider(api_key=settings.anthropic_api_key, model=model)
    if provider_name == "fake":
        log.warning("LLM_PROVIDER=fake — using FakeProvider. Not for production.")
        return FakeProvider(responses=["(fake provider response)"] * 10_000)
    raise RuntimeError(f"unknown LLM_PROVIDER={provider_name!r}")
```

Inside `_run()`, after `store.init_schema()`, before the registry token sanity check, insert:

```python
    # Build the LLM provider once; share it across agents.
    llm = _build_llm_provider(settings)

    # Construct Secretary and install it into both registries. This MUST
    # happen before the orchestrator handles any message — AGENT_SPECS
    # already lists secretary, so load_settings will have demanded its
    # bot token above.
    persona = load_persona(Path("prompts/secretary.md"))
    config_path = Path("prompts/secretary.toml")
    secretary_cfg = load_config(config_path)
    secretary = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=secretary_cfg,
    )
    register_secretary(secretary.handle)
    log.info("secretary registered (model=%s)", secretary_cfg.model)
```

Remove the old `_validate_anthropic_key(settings)` call in `main()` — the provider constructor does this implicitly. Replace:

```python
def main() -> None:
    settings = load_settings()
    _setup_logging(settings.log_level)
    _validate_anthropic_key(settings)
    try:
        asyncio.run(_run(settings))
    except KeyboardInterrupt:
        log.info("shutting down")
```

with:

```python
def main() -> None:
    settings = load_settings()
    _setup_logging(settings.log_level)
    try:
        asyncio.run(_run(settings))
    except KeyboardInterrupt:
        log.info("shutting down")
```

And delete the `_validate_anthropic_key` function entirely (and its `AsyncAnthropic` import at the top of the file — now unused there).

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest -v`

Expected: zero failures. `main.py` is not exercised by the unit tests but must still import cleanly.

- [ ] **Step 4: Static checks**

Run: `uv run mypy src/project0` and `uv run ruff check src tests`

Expected: zero errors.

- [ ] **Step 5: Smoke-import main**

Run: `uv run python -c "import project0.main; print('import OK')"`

Expected: `import OK`. If it fails complaining about missing env vars, that is expected — `main()` reads them at call time, not at import. Only module-level code runs here.

- [ ] **Step 6: Commit**

```bash
git add src/project0/main.py
git commit -m "feat(main): wire AnthropicProvider + Secretary at startup"
```

---

## Task 19: `scripts/inject_reminder.py` — manual smoke-test helper

**Files:**
- Create: `scripts/inject_reminder.py`

- [ ] **Step 1: Create the script**

Run: `mkdir -p scripts`

Create `scripts/inject_reminder.py`:

```python
"""Manual smoke-test helper for Secretary's Manager-directed reminder path.

Usage:
    uv run python scripts/inject_reminder.py "<appointment>" "<when>" [<note>]

Synthesizes a manager_delegation envelope with
payload={"kind": "reminder_request", ...}, dispatches it through Secretary,
and prints the reply. Does NOT go through Telegram — Secretary's reply is
printed to stdout. This is intentionally minimal: the real Manager in 6b
will construct and dispatch these envelopes via the live orchestrator.

Requires all of the same env vars main.py needs (bot tokens, allow-list,
ANTHROPIC_API_KEY) because Settings validation is shared.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from project0.agents.secretary import Secretary, load_config, load_persona
from project0.config import load_settings
from project0.envelope import Envelope
from project0.llm.provider import AnthropicProvider
from project0.store import Store


async def main() -> None:
    if len(sys.argv) < 3:
        print(
            "usage: uv run python scripts/inject_reminder.py "
            '"<appointment>" "<when>" [<note>]',
            file=sys.stderr,
        )
        sys.exit(2)

    appointment = sys.argv[1]
    when = sys.argv[2]
    note = sys.argv[3] if len(sys.argv) > 3 else ""

    settings = load_settings()
    store = Store(settings.store_path)
    store.init_schema()

    provider = AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model="claude-sonnet-4-6",
    )
    persona = load_persona(Path("prompts/secretary.md"))
    cfg = load_config(Path("prompts/secretary.toml"))
    sec = Secretary(
        llm=provider,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=cfg,
    )

    env = Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="internal",
        telegram_chat_id=None,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="secretary",
        body="",
        routing_reason="manager_delegation",
        payload={
            "kind": "reminder_request",
            "appointment": appointment,
            "when": when,
            "note": note,
        },
    )
    result = await sec.handle(env)
    if result is None or result.reply_text is None:
        print("(secretary returned no reply)")
    else:
        print(result.reply_text)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Smoke-check it imports**

Run: `uv run python -c "import importlib.util, sys; spec = importlib.util.spec_from_file_location('s', 'scripts/inject_reminder.py'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('OK')"`

Expected: `OK`. (Running it without arguments would exit with code 2; running it with arguments would hit Anthropic and is part of the manual smoke test.)

- [ ] **Step 3: Commit**

```bash
git add scripts/inject_reminder.py
git commit -m "feat(scripts): inject_reminder helper for manual smoke test"
```

---

## Task 20: Final verification — ruff, mypy, pytest, README runbook

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the full verification gate**

Run these four commands in order. Each must exit zero.

Run: `uv run ruff check src tests scripts`

Expected: zero warnings.

Run: `uv run mypy src/project0`

Expected: zero errors.

Run: `uv run pytest -v`

Expected: every test passes, including:
- `tests/test_envelope.py` (payload + listener_observation)
- `tests/test_store.py` (payload_json + recent_for_chat)
- `tests/test_llm_provider.py` (FakeProvider + AnthropicProvider with mocked SDK)
- `tests/test_secretary.py` (loaders, helpers, listener, addressed, DM, reminder, durability)
- `tests/test_orchestrator_listener_fanout.py` (fan-out semantics)
- All pre-existing skeleton tests still green.

Run: `uv run python -m project0.main --help 2>&1 || true; uv run python -c "import project0.main; print('import OK')"`

Expected: clean import. (The CLI has no `--help` but importing must succeed.)

If any step fails, fix the issue and re-run before proceeding. Do not mark Task 20 complete until all four gates are green.

- [ ] **Step 2: Update README with the Secretary runbook**

Read the current README to locate the runbook section:

Run: `cat README.md | head -60`

Add a new subsection after the skeleton "how to run" section titled `## Sub-project 6a — Secretary`. Include:

- The three env vars to add: `TELEGRAM_BOT_TOKEN_SECRETARY`, `LLM_PROVIDER=anthropic`, `LLM_MODEL=claude-sonnet-4-6`
- A note that adding Secretary requires `TELEGRAM_BOT_TOKEN_SECRETARY` in `.env`
- The manual smoke-test checklist from spec section 12 (G.1 through G.6), written as a numbered list the user can walk through:
  1. Start the process: `uv run python -m project0.main`. Confirm Secretary is registered in the startup log.
  2. In the allow-listed group chat, send a short message like `hi`. Secretary should stay silent (cooldown not open).
  3. Send several more short messages over ~2 minutes until the cooldown opens (3 messages minimum, 200 weighted chars minimum, 90 seconds minimum since any prior Secretary reply). Secretary should chime in in Chinese in character — or stay silent if the LLM returned `[skip]`.
  4. Send `@secretary 你好` in the group. Expect an immediate Chinese reply.
  5. DM Secretary's bot directly with `你今天怎么样`. Expect a reply with a more personal tone.
  6. Run `uv run python scripts/inject_reminder.py "项目评审" "明天下午3点"`. Expect a warm Chinese reminder printed to stdout.
  7. Inspect the messages table: `sqlite3 data/store.db "SELECT id, parent_id, from_agent, to_agent, json_extract(envelope_json, '\$.routing_reason') FROM messages ORDER BY id DESC LIMIT 20"`. Verify `listener_observation` envelopes appear for group messages; Secretary replies link to the listener_observation envelope as parent.

Also note in the README that upgrading from the skeleton to 6a requires running the process once so the additive `payload_json` column migration applies. The schema migration is idempotent — no manual `sqlite3` command required.

- [ ] **Step 3: Commit the README update**

```bash
git add README.md
git commit -m "docs(readme): sub-project 6a runbook + smoke test"
```

- [ ] **Step 4: Final status check**

Run: `git log --oneline -30` and `git status`

Expected: clean working tree; 20 commits for 6a, each scoped and atomic; main branch unchanged unless you deliberately merged.

---

## Spec coverage self-check

Checking the plan against `docs/superpowers/specs/2026-04-13-secretary-design.md` section by section:

- **§2 Scope:** Secretary agent (Tasks 9–13), bot token (Task 17), provider (Tasks 4–5), listener fan-out (Task 16), `listener_observation` reason (Task 1), `payload` field (Task 1), `payload_json` column (Task 2), `recent_for_chat` (Task 3), persona + config files (Task 7), four entry paths (Tasks 10–13). ✓
- **§3 LLM provider interface:** Task 4 creates Protocol + Msg + FakeProvider; Task 5 adds AnthropicProvider with prompt caching. ✓
- **§4 Listener fan-out:** registry split (Task 15), fan-out pipeline with skip-self + no-delegate + parent linkage + listener-bot routing (Task 16). ✓
- **§5 Secretary paths:** dispatcher (Task 9), listener cooldown (Task 10), listener LLM + skip (Task 11), addressed paths (Task 12), reminder (Task 13). ✓
- **§6 Persona and config files:** Tasks 6 and 7. ✓
- **§7 Chinese considerations:** Skip sentinel matcher is defensive (Task 8); `weighted_len` for CJK-aware cooldown (Task 8); persona is Chinese (Task 7). ✓
- **§8 Storage changes:** `payload_json` additive ALTER (Task 2); `recent_for_chat` (Task 3). ✓
- **§9 Project layout:** all new files present in the plan. ✓
- **§10 Configuration:** new env vars (Task 17). ✓
- **§11 Testing strategy:** `test_llm_provider.py` (Tasks 4–5), `test_secretary.py` (Tasks 6, 8, 9–14), `test_orchestrator_listener_fanout.py` (Task 16), durability test (Task 14), `weighted_len` test, skip-sentinel test. ✓
- **§12 Acceptance criteria A–H:** A–C = Task 20 verification gates. D = pre-existing skeleton criteria, still valid. E = pre-existing. F = Task 20 pytest gate. G = Task 20 README runbook. H = manual observation during smoke test (documented in README). ✓
- **§13 Future-proofing decisions:** encoded in code and comments throughout; no task needed.

No gaps.
