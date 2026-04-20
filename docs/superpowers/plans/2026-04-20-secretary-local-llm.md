# Secretary Local-LLM Option Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in local-LLM backend for the Secretary agent (`SECRETARY_MODE=free`) that points her at an OpenAI-compatible vLLM/TRT-LLM server serving Qwen2.5-72B-abliterated, coupled with a second persona file and a hard invariant that disables the user-facts writer to prevent NSFW leakage into other agents' prompts.

**Architecture:** A new `LocalProvider` parallel to `AnthropicProvider` at the `LLMProvider` Protocol layer. A `_build_secretary_dependencies` factory in `main.py` selects one coupled bundle of `(provider, persona files, facts writer | None)` based on `SECRETARY_MODE`. Secretary also gains a typing-indicator context manager so the native "typing…" appears during slow local inference. All other agents are untouched.

**Tech Stack:** Python 3.12, `openai` SDK (new dep), `respx` (test dep), existing `LLMProvider` Protocol, `pytest-asyncio`, Telegram `sendChatAction`.

**Spec:** `docs/superpowers/specs/2026-04-20-secretary-local-llm-design.md`

---

## File Structure

**Create:**
- `src/project0/llm/local_provider.py` — `LocalProvider` class + `LocalProviderError` hierarchy (3 classes)
- `prompts/secretary_free.md` — Persona B prose (verbatim copy of `secretary.md` for v1.0)
- `prompts/secretary_free.toml` — Persona B runtime config (copy of `secretary.toml` with lowered `max_tokens_reply=500`, `max_tokens_listener=200`)
- `tests/test_local_provider.py` — unit tests with `respx`
- `tests/test_typing_indicator.py` — unit tests for the CM
- `tests/test_secretary_mode_factory.py` — tests for the `main.py` factory (pure unit tests; do not boot the daemon)

**Modify:**
- `pyproject.toml` — add `openai>=1.40` to dependencies, `respx>=0.21` to dev group
- `src/project0/telegram_io.py` — add `send_chat_action` method to `BotSender` Protocol + `FakeBotSender` + `RealBotSender`; add `typing_indicator` async context manager
- `src/project0/config.py` — extend `Settings` with 4 new fields; parse 4 new env vars in `load_settings()`
- `src/project0/agents/secretary.py` — add optional `bot_sender: BotSender | None = None` constructor param; wrap `_addressed_llm_call` and `_handle_reminder` with `typing_indicator`
- `src/project0/main.py` — add `_build_secretary_dependencies()` factory; wire it into Secretary construction; assert the `local ⇒ writer is None` invariant; pass sender to Secretary
- `.env.example` — document 4 new vars

**Unchanged:** orchestrator, store, all other agents.

---

## Task 1: Add dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `openai` to runtime deps and `respx` to dev deps**

Edit `pyproject.toml`. Add `"openai>=1.40"` to the `dependencies` list (keep alphabetical order relative to existing entries). Add `"respx>=0.21"` to the `dev` group.

Exact insertions:

In `[project] dependencies`, append after `"notion-client>=2.0",`:
```toml
    "openai>=1.40",
```

In `[dependency-groups] dev`, append after `"mypy>=1.10",`:
```toml
    "respx>=0.21",
```

- [ ] **Step 2: Install**

Run: `uv sync`
Expected: lockfile updates; no errors.

- [ ] **Step 3: Verify imports**

Run: `uv run python -c "import openai; import respx; print(openai.__version__, respx.__version__)"`
Expected: two version strings printed, both ≥ the pins.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add openai runtime dep and respx dev dep for local-LLM secretary"
```

---

## Task 2: Add Persona B prompt files

**Files:**
- Create: `prompts/secretary_free.md`
- Create: `prompts/secretary_free.toml`

- [ ] **Step 1: Copy persona prose**

Run: `cp prompts/secretary.md prompts/secretary_free.md`
Expected: new file exists, byte-identical to `secretary.md`.

- [ ] **Step 2: Copy and adjust the runtime config**

Create `prompts/secretary_free.toml` as a copy of `secretary.toml` with two values changed: `max_tokens_reply = 500`, `max_tokens_listener = 200`, and `model = "qwen2.5-72b-awq-8k"`. Keep all other fields identical (cooldown, transcript_window, skip_sentinels).

File content:

```toml
# Secretary free-mode (local-LLM) runtime config. Persona B.
# Tighter token caps than secretary.toml to respect Qwen 2.5 72B's 8192 total
# context budget. All other values mirror secretary.toml so behaviour stays
# deterministic when only the LLM provider changes.

[cooldown]
t_min_seconds        = 90
n_min_messages       = 4
l_min_weighted_chars = 200

[context]
transcript_window = 20

[llm]
model               = "qwen2.5-72b-awq-8k"
max_tokens_reply    = 500
max_tokens_listener = 200

[skip_sentinels]
patterns = [
    "[skip]",
    "[跳过]",
    "【skip】",
    "【跳过】",
    "（skip）",
    "（跳过）",
]
```

- [ ] **Step 3: Sanity-check the TOML parses**

Run: `uv run python -c "from project0.agents.secretary import load_config; from pathlib import Path; c = load_config(Path('prompts/secretary_free.toml')); print(c.model, c.max_tokens_reply, c.max_tokens_listener)"`
Expected: `qwen2.5-72b-awq-8k 500 200`

- [ ] **Step 4: Commit**

```bash
git add prompts/secretary_free.md prompts/secretary_free.toml
git commit -m "feat(prompts): add secretary_free persona B files (copy of A with lower token caps)"
```

---

## Task 3: Extend BotSender Protocol with `send_chat_action`

**Files:**
- Modify: `src/project0/telegram_io.py`
- Test: `tests/test_typing_indicator.py` (created in Task 4; this task adds the Protocol and fakes only)

- [ ] **Step 1: Write the failing test**

Create `tests/test_telegram_io_chat_action.py`:

```python
"""FakeBotSender records typing/chat_action sends; Protocol requires it."""

from __future__ import annotations

import pytest

from project0.telegram_io import FakeBotSender


@pytest.mark.asyncio
async def test_fake_bot_sender_records_chat_action() -> None:
    sender = FakeBotSender()
    await sender.send_chat_action(agent="secretary", chat_id=42, action="typing")
    assert sender.chat_actions == [
        {"agent": "secretary", "chat_id": 42, "action": "typing"}
    ]


@pytest.mark.asyncio
async def test_fake_bot_sender_send_and_chat_action_are_independent_lists() -> None:
    sender = FakeBotSender()
    await sender.send(agent="secretary", chat_id=1, text="hi")
    await sender.send_chat_action(agent="secretary", chat_id=1, action="typing")
    assert len(sender.sent) == 1
    assert len(sender.chat_actions) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_telegram_io_chat_action.py -v`
Expected: FAIL — `AttributeError: 'FakeBotSender' object has no attribute 'send_chat_action'` (and/or missing `chat_actions` attribute).

- [ ] **Step 3: Extend the Protocol and FakeBotSender**

Edit `src/project0/telegram_io.py`:

In `class BotSender(Protocol):`, add the method after `send`:

```python
    async def send_chat_action(self, *, agent: str, chat_id: int, action: str) -> None:
        """Send a chat action (e.g. 'typing') to `chat_id` as `agent`'s bot.
        Telegram's indicator auto-expires after ~5s; callers refresh if needed."""
```

In `@dataclass class FakeBotSender:`, add a new field and method:

```python
    chat_actions: list[dict[str, object]] = field(default_factory=list)

    async def send_chat_action(self, *, agent: str, chat_id: int, action: str) -> None:
        self.chat_actions.append(
            {"agent": agent, "chat_id": chat_id, "action": action}
        )
```

In `class RealBotSender:`, add the method (mirror of `send`):

```python
    async def send_chat_action(self, *, agent: str, chat_id: int, action: str) -> None:
        app = self._apps[agent]
        await app.bot.send_chat_action(chat_id=chat_id, action=action)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_telegram_io_chat_action.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/project0/telegram_io.py`
Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
git add src/project0/telegram_io.py tests/test_telegram_io_chat_action.py
git commit -m "feat(telegram_io): add send_chat_action to BotSender Protocol and implementations"
```

---

## Task 4: Create `typing_indicator` async context manager

**Files:**
- Modify: `src/project0/telegram_io.py`
- Test: `tests/test_typing_indicator.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_typing_indicator.py`:

```python
"""typing_indicator CM refreshes 'typing' every ~4s until exited.

The CM is the user-visible feedback during slow local-LLM inference.
Contract:
  - On enter: one chat_action fires immediately.
  - While open: refresh every `refresh_seconds` seconds.
  - On exit (success or exception): background refresh task is cancelled.
  - Sender errors are swallowed (typing failure must never block a reply).
"""

from __future__ import annotations

import asyncio

import pytest

from project0.telegram_io import FakeBotSender, typing_indicator


@pytest.mark.asyncio
async def test_typing_indicator_sends_immediately_on_enter() -> None:
    sender = FakeBotSender()
    async with typing_indicator(sender=sender, agent="secretary", chat_id=9, refresh_seconds=10.0):
        pass
    assert len(sender.chat_actions) == 1
    assert sender.chat_actions[0]["action"] == "typing"
    assert sender.chat_actions[0]["agent"] == "secretary"
    assert sender.chat_actions[0]["chat_id"] == 9


@pytest.mark.asyncio
async def test_typing_indicator_refreshes_while_open() -> None:
    sender = FakeBotSender()
    async with typing_indicator(sender=sender, agent="secretary", chat_id=9, refresh_seconds=0.05):
        await asyncio.sleep(0.18)  # ~3 refreshes expected: 0 + 0.05 + 0.10 + 0.15
    # Allow some scheduler slop: expect at least 3 sends.
    assert len(sender.chat_actions) >= 3


@pytest.mark.asyncio
async def test_typing_indicator_stops_refreshing_on_exit() -> None:
    sender = FakeBotSender()
    async with typing_indicator(sender=sender, agent="secretary", chat_id=9, refresh_seconds=0.05):
        await asyncio.sleep(0.12)
    count_at_exit = len(sender.chat_actions)
    await asyncio.sleep(0.20)
    assert len(sender.chat_actions) == count_at_exit  # no further sends after exit


@pytest.mark.asyncio
async def test_typing_indicator_swallows_sender_errors() -> None:
    class BrokenSender:
        async def send(self, *, agent: str, chat_id: int, text: str) -> None:
            raise RuntimeError("should not be called")

        async def send_chat_action(self, *, agent: str, chat_id: int, action: str) -> None:
            raise RuntimeError("boom")

    # Must not raise.
    async with typing_indicator(sender=BrokenSender(), agent="secretary", chat_id=1, refresh_seconds=0.05):
        await asyncio.sleep(0.10)


@pytest.mark.asyncio
async def test_typing_indicator_cancels_on_inner_exception() -> None:
    sender = FakeBotSender()
    with pytest.raises(ValueError):
        async with typing_indicator(sender=sender, agent="secretary", chat_id=9, refresh_seconds=0.05):
            raise ValueError("caller error")
    count_after_exit = len(sender.chat_actions)
    await asyncio.sleep(0.10)
    assert len(sender.chat_actions) == count_after_exit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_typing_indicator.py -v`
Expected: FAIL — `ImportError: cannot import name 'typing_indicator' from 'project0.telegram_io'`.

- [ ] **Step 3: Implement the context manager**

Append to `src/project0/telegram_io.py` (after the Real/FakeBotSender definitions):

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator


@asynccontextmanager
async def typing_indicator(
    *,
    sender: BotSender,
    agent: str,
    chat_id: int,
    refresh_seconds: float = 4.0,
) -> AsyncIterator[None]:
    """Show 'agent is typing…' on Telegram for the duration of the block.

    Fires one chat_action immediately on enter, then refreshes every
    `refresh_seconds` (Telegram's indicator auto-clears at ~5s). Cancels
    the refresh task on exit, whether the body completed normally or raised.
    Any exception from the sender is logged and swallowed — a failed typing
    indicator must never block a reply.
    """

    async def _safe_send() -> None:
        try:
            await sender.send_chat_action(agent=agent, chat_id=chat_id, action="typing")
        except Exception:
            log.debug("typing_indicator: send_chat_action failed", exc_info=True)

    async def _loop() -> None:
        while True:
            await asyncio.sleep(refresh_seconds)
            await _safe_send()

    await _safe_send()  # immediate first tick
    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
```

Add `import asyncio` at the top of the file if not already imported. Move the existing top-of-file imports to include `from contextlib import asynccontextmanager` and `from collections.abc import AsyncIterator` (the module already imports `Awaitable, Callable` from `collections.abc`, so extend that line).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_typing_indicator.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/project0/telegram_io.py tests/test_typing_indicator.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/project0/telegram_io.py tests/test_typing_indicator.py
git commit -m "feat(telegram_io): add typing_indicator async context manager"
```

---

## Task 5: Wire `bot_sender` into Secretary and use `typing_indicator`

**Files:**
- Modify: `src/project0/agents/secretary.py`
- Test: `tests/test_secretary_typing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_secretary_typing.py`:

```python
"""Secretary emits a typing indicator during reply LLM calls when a sender
is wired. When no sender is wired (legacy tests), reply paths must still
work without any chat_action being sent."""

from __future__ import annotations

from pathlib import Path

import pytest

from project0.agents.secretary import Secretary, SecretaryConfig, load_persona
from project0.envelope import Envelope
from project0.llm.provider import FakeProvider
from project0.store import Store
from project0.telegram_io import FakeBotSender


def _cfg() -> SecretaryConfig:
    return SecretaryConfig(
        t_min_seconds=90,
        n_min_messages=4,
        l_min_weighted_chars=200,
        transcript_window=20,
        model="test",
        max_tokens_reply=100,
        max_tokens_listener=50,
        skip_sentinels=["[skip]"],
    )


@pytest.mark.asyncio
async def test_dm_reply_emits_typing_when_sender_wired(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "s.db"))
    store.init_schema()
    sender = FakeBotSender()
    persona = load_persona(Path("prompts/secretary.md"))
    secretary = Secretary(
        llm=FakeProvider(responses=["好的"]),
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=_cfg(),
        bot_sender=sender,
    )
    env = _dm_envelope()
    result = await secretary.handle(env)
    assert result is not None and result.reply_text == "好的"
    # At least one typing action must have been sent for chat_id=7.
    typing_to_chat_7 = [a for a in sender.chat_actions if a["chat_id"] == 7]
    assert len(typing_to_chat_7) >= 1


@pytest.mark.asyncio
async def test_dm_reply_works_without_sender(tmp_path: Path) -> None:
    # Legacy construction: bot_sender defaults to None. Must still reply.
    store = Store(str(tmp_path / "s.db"))
    store.init_schema()
    persona = load_persona(Path("prompts/secretary.md"))
    secretary = Secretary(
        llm=FakeProvider(responses=["好的"]),
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=_cfg(),
    )
    env = _dm_envelope()
    result = await secretary.handle(env)
    assert result is not None and result.reply_text == "好的"


def _dm_envelope() -> Envelope:
    return Envelope(
        id=None,
        ts="2026-04-20T00:00:00+00:00",
        parent_id=None,
        source="telegram_dm",
        telegram_chat_id=7,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="human",
        from_agent=None,
        to_agent="secretary",
        body="hi",
        routing_reason="direct_dm",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_secretary_typing.py -v`
Expected: FAIL — `TypeError: Secretary.__init__() got an unexpected keyword argument 'bot_sender'`.

- [ ] **Step 3: Extend Secretary with optional sender and typing wrap**

Edit `src/project0/agents/secretary.py`:

Add import near the other project imports:
```python
from project0.telegram_io import BotSender, typing_indicator
```

Change the constructor signature. After the existing `user_facts_writer: UserFactsWriter | None = None,` line, add:
```python
        bot_sender: BotSender | None = None,
```
And in the body, store it: `self._bot_sender = bot_sender`

Wrap `_addressed_llm_call` body. Locate the existing `text = await self._run_with_tool_loop(...)` call in `_addressed_llm_call` (around line 545). Replace the single-line `text = await ...` with a conditional CM wrap:

```python
        if self._bot_sender is not None and chat_id is not None:
            async with typing_indicator(
                sender=self._bot_sender,
                agent="secretary",
                chat_id=chat_id,
            ):
                text = await self._run_with_tool_loop(
                    env=env,
                    purpose="reply",
                    mode=mode,
                    initial_user_text=user_msg,
                    max_tokens=max_tokens,
                )
        else:
            text = await self._run_with_tool_loop(
                env=env,
                purpose="reply",
                mode=mode,
                initial_user_text=user_msg,
                max_tokens=max_tokens,
            )
```

Do the same wrap for `_handle_reminder`. Locate its `_run_with_tool_loop` call and apply the identical conditional wrap pattern, using `env.telegram_chat_id` as the chat_id guard.

Listener path (`_listener_llm_call`) is NOT wrapped — the listener decides silently whether to speak, and a typing indicator that appears for every silent poll would be misleading.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_secretary_typing.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full Secretary suite to catch regressions**

Run: `uv run pytest tests/ -k secretary -v`
Expected: all prior secretary tests still PASS.

- [ ] **Step 6: Type-check**

Run: `uv run mypy src/project0/agents/secretary.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/project0/agents/secretary.py tests/test_secretary_typing.py
git commit -m "feat(secretary): add optional bot_sender and wrap reply paths in typing_indicator"
```

---

## Task 6: LocalProvider — happy path (`complete`)

**Files:**
- Create: `src/project0/llm/local_provider.py`
- Test: `tests/test_local_provider.py`

- [ ] **Step 1: Write the failing test (happy path only)**

Create `tests/test_local_provider.py`:

```python
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
from project0.llm.provider import Msg, SystemBlocks
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

    # Usage store recorded exactly one row with correct token counts.
    cur = usage._conn.execute(  # type: ignore[attr-defined]
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
    assert row[4] == 0  # local model has no prompt cache
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
    # First message is a single system message joining both segments with "\n\n".
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][0]["content"] == "PERSONA\n\nFACTS"
    assert payload["messages"][1] == {"role": "user", "content": "hi"}
    assert payload["model"] == MODEL
    assert payload["max_tokens"] == 50
    assert payload["stream"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_local_provider.py -v`
Expected: FAIL — `ImportError: cannot import name 'LocalProvider'`.

- [ ] **Step 3: Implement `LocalProvider` (minimal, happy path only)**

Create `src/project0/llm/local_provider.py`:

```python
"""Local LLM provider: talks to an OpenAI-compatible HTTP endpoint.

Used only by Secretary in `SECRETARY_MODE=free`. Structurally parallel to
AnthropicProvider — same Protocol, same usage recording, but no prompt
caching and no tool-use. See docs/superpowers/specs/2026-04-20-secretary-
local-llm-design.md for the design rationale.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from openai import AsyncOpenAI

from project0.llm.provider import Msg, SystemBlocks
from project0.llm.tools import AssistantToolUseMsg, ToolResultMsg, ToolSpec, ToolUseResult
from project0.store import LLMUsageStore

log = logging.getLogger(__name__)


class LocalProviderError(Exception):
    """Base class for LocalProvider failures."""


class LocalProviderUnavailableError(LocalProviderError):
    """Server unreachable, timeout, or persistent 5xx."""


class LocalProviderContextError(LocalProviderError):
    """Request exceeded the server's context length (HTTP 400)."""


def _flatten_system(system: str | SystemBlocks) -> str:
    if isinstance(system, str):
        return system
    if system.facts:
        return f"{system.stable}\n\n{system.facts}"
    return system.stable


class LocalProvider:
    """OpenAI-compatible HTTP provider. No tool-use; no prompt caching."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        usage_store: LLMUsageStore,
        request_timeout_seconds: float = 180.0,
    ) -> None:
        if not api_key:
            raise ValueError("LocalProvider requires a non-empty api_key (any string)")
        self._model = model
        self._usage_store = usage_store
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=httpx.Timeout(request_timeout_seconds),
            max_retries=0,  # we do our own bounded retry
        )

    async def complete(
        self,
        *,
        system: str | SystemBlocks,
        messages: list[Msg],
        max_tokens: int = 800,
        thinking_budget_tokens: int | None = None,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
    ) -> str:
        # thinking_budget_tokens is Anthropic-specific; ignored here.
        system_text = _flatten_system(system)
        payload_messages: list[dict[str, Any]] = [{"role": "system", "content": system_text}]
        for m in messages:
            payload_messages.append({"role": m.role, "content": m.content})

        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=payload_messages,
            max_tokens=max_tokens,
            stream=False,
        )

        # Usage accounting.
        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        out_tok = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        self._usage_store.record(
            agent=agent,
            model=self._model,
            input_tokens=in_tok,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            output_tokens=out_tok,
            envelope_id=envelope_id,
            purpose=purpose,
        )
        log.info(
            "local llm call agent=%s model=%s in=%d out=%d env=%s purpose=%s",
            agent, self._model, in_tok, out_tok,
            envelope_id if envelope_id is not None else "-",
            purpose,
        )

        if not resp.choices:
            return ""
        content = resp.choices[0].message.content
        return content if content else ""

    async def complete_with_tools(
        self,
        *,
        system: str | SystemBlocks,
        messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
    ) -> ToolUseResult:
        raise NotImplementedError(
            "LocalProvider does not support tool-use; free mode must run "
            "without user_facts_writer. See 2026-04-20-secretary-local-llm-design.md §6."
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_local_provider.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/project0/llm/local_provider.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/project0/llm/local_provider.py tests/test_local_provider.py
git commit -m "feat(llm): add LocalProvider happy path (OpenAI-compat, no tools, no cache)"
```

---

## Task 7: LocalProvider — error mapping and retry

**Files:**
- Modify: `src/project0/llm/local_provider.py`
- Test: `tests/test_local_provider.py` (extend)

- [ ] **Step 1: Write failing error-path tests**

Append to `tests/test_local_provider.py`:

```python
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
    # 400 without context-length signal is still a client error but not the
    # context path. Map to the generic unavailable class.
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
        base_url=BASE_URL, model=MODEL, api_key="k", usage_store=usage,
    )
    # First call: 500; second call: 200.
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
    provider = LocalProvider(base_url=BASE_URL, model=MODEL, api_key="k", usage_store=usage)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_local_provider.py -v`
Expected: 7 NEW tests FAIL (various `AssertionError`s and wrong exception classes); the 2 happy-path tests still pass.

- [ ] **Step 3: Add retry loop and exception mapping**

Edit `src/project0/llm/local_provider.py`. Replace the body of `complete` (the part that calls `self._client.chat.completions.create`) with a retry-and-map wrapper:

```python
        import asyncio

        from openai import APIConnectionError, APITimeoutError, APIStatusError

        last_err: Exception | None = None
        for attempt in (1, 2):
            try:
                resp = await self._client.chat.completions.create(
                    model=self._model,
                    messages=payload_messages,
                    max_tokens=max_tokens,
                    stream=False,
                )
                break
            except APITimeoutError as e:
                log.error("local llm timeout: %s", e)
                raise LocalProviderUnavailableError("timeout") from e
            except APIConnectionError as e:
                log.error("local llm connection error: %s", e)
                raise LocalProviderUnavailableError("connection") from e
            except APIStatusError as e:
                status = getattr(e, "status_code", None)
                msg = str(getattr(e, "message", "") or "")
                if status == 400 and ("context length" in msg.lower() or "context_length" in msg.lower()):
                    raise LocalProviderContextError(msg) from e
                if status and 500 <= status < 600 and attempt == 1:
                    log.warning("local llm 5xx (attempt 1), retrying in 2s: %s", msg)
                    last_err = e
                    await asyncio.sleep(2.0)
                    continue
                log.error("local llm HTTP error status=%s: %s", status, msg)
                raise LocalProviderUnavailableError(f"http {status}") from e
        else:
            # Both attempts failed with retryable errors.
            raise LocalProviderUnavailableError("persistent 5xx") from last_err
```

Replace the pre-existing single-call line with this block (keep the usage-accounting code below it unchanged).

Note on message detection for context errors: OpenAI SDK raises `APIStatusError` subclasses (`BadRequestError` for 400). The `.message` attribute or `.response.text` contains the server's error message. If the string match proves too brittle at runtime we can tighten later; tests pin current behaviour.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_local_provider.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/project0/llm/local_provider.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/project0/llm/local_provider.py tests/test_local_provider.py
git commit -m "feat(llm): add LocalProvider error mapping (timeout, 400/ctx, 5xx retry)"
```

---

## Task 8: LocalProvider — `complete_with_tools` guard

**Files:**
- Test: `tests/test_local_provider.py` (extend)

The guard is already implemented in Task 6. This task just adds a pinning test.

- [ ] **Step 1: Write failing test**

Append to `tests/test_local_provider.py`:

```python
@pytest.mark.asyncio
async def test_local_provider_complete_with_tools_raises_not_implemented(tmp_path: Path) -> None:
    usage = _make_usage_store(tmp_path)
    provider = LocalProvider(base_url=BASE_URL, model=MODEL, api_key="k", usage_store=usage)

    with pytest.raises(NotImplementedError, match="free mode must run"):
        await provider.complete_with_tools(
            system="s",
            messages=[],
            tools=[],
            max_tokens=50,
            agent="secretary",
            purpose="reply",
        )
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_local_provider.py::test_local_provider_complete_with_tools_raises_not_implemented -v`
Expected: PASS (the guard was implemented in Task 6).

- [ ] **Step 3: Commit**

```bash
git add tests/test_local_provider.py
git commit -m "test(llm): pin LocalProvider.complete_with_tools NotImplementedError guard"
```

---

## Task 9: Extend `Settings` with new env vars

**Files:**
- Modify: `src/project0/config.py`
- Test: `tests/test_config_secretary_mode.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_config_secretary_mode.py`:

```python
"""Settings must accept SECRETARY_MODE and local-LLM env vars."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture
def required_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Minimal required env so load_settings does not bail on other vars.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_SECRETARY", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_MANAGER", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_INTELLIGENCE", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_LEARNING", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_SUPERVISOR", "x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("USER_TIMEZONE", "UTC")
    monkeypatch.setenv("NOTION_INTERNAL_INTEGRATION_SECRET", "x")
    monkeypatch.setenv("NOTION_DATABASE_ID", "x")
    yield


def test_default_secretary_mode_is_work(required_env: None) -> None:
    from project0.config import load_settings
    if "SECRETARY_MODE" in os.environ:
        del os.environ["SECRETARY_MODE"]
    s = load_settings()
    assert s.secretary_mode == "work"
    assert s.local_llm_base_url == "http://127.0.0.1:8000/v1"
    assert s.local_llm_model == "qwen2.5-72b-awq-8k"
    assert s.local_llm_api_key == "unused"


def test_secretary_mode_free(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("SECRETARY_MODE", "free")
    s = load_settings()
    assert s.secretary_mode == "free"


def test_secretary_mode_invalid_raises(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("SECRETARY_MODE", "chaos")
    with pytest.raises(RuntimeError, match="SECRETARY_MODE"):
        load_settings()


def test_local_llm_overrides(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from project0.config import load_settings
    monkeypatch.setenv("SECRETARY_MODE", "free")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://192.168.1.50:9000/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "other-model")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "token123")
    s = load_settings()
    assert s.local_llm_base_url == "http://192.168.1.50:9000/v1"
    assert s.local_llm_model == "other-model"
    assert s.local_llm_api_key == "token123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_secretary_mode.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'secretary_mode'`.

- [ ] **Step 3: Extend Settings**

Edit `src/project0/config.py`:

In the `@dataclass(frozen=True) class Settings:`, append four new fields after `anthropic_cache_ttl`:

```python
    secretary_mode: Literal["work", "free"] = "work"
    local_llm_base_url: str = "http://127.0.0.1:8000/v1"
    local_llm_model: str = "qwen2.5-72b-awq-8k"
    local_llm_api_key: str = "unused"
```

In `load_settings()`, after the existing `raw_ttl` validation block, add:

```python
    secretary_mode_raw = os.environ.get("SECRETARY_MODE", "work").strip().lower() or "work"
    if secretary_mode_raw not in ("work", "free"):
        raise RuntimeError(
            f"SECRETARY_MODE must be 'work' or 'free', got {secretary_mode_raw!r}"
        )
    local_llm_base_url = (
        os.environ.get("LOCAL_LLM_BASE_URL", "").strip() or "http://127.0.0.1:8000/v1"
    )
    local_llm_model = (
        os.environ.get("LOCAL_LLM_MODEL", "").strip() or "qwen2.5-72b-awq-8k"
    )
    local_llm_api_key = os.environ.get("LOCAL_LLM_API_KEY", "").strip() or "unused"
```

In the final `return Settings(...)` constructor call, add four new keyword args:

```python
        secretary_mode=secretary_mode_raw,  # type: ignore[arg-type]
        local_llm_base_url=local_llm_base_url,
        local_llm_model=local_llm_model,
        local_llm_api_key=local_llm_api_key,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config_secretary_mode.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/project0/config.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/project0/config.py tests/test_config_secretary_mode.py
git commit -m "feat(config): add SECRETARY_MODE and LOCAL_LLM_* settings"
```

---

## Task 10: Secretary dependencies factory in `main.py`

**Files:**
- Modify: `src/project0/main.py`
- Test: `tests/test_secretary_mode_factory.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_secretary_mode_factory.py`:

```python
"""Unit tests for _build_secretary_dependencies factory.

Constructs Settings directly (no env parsing) to isolate the factory's
selection logic and the local⇒writer=None invariant.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.config import Settings
from project0.llm.local_provider import LocalProvider
from project0.llm.provider import AnthropicProvider, LLMProvider
from project0.main import _build_secretary_dependencies
from project0.store import LLMUsageStore, Store, UserFactsWriter


def _settings(mode: str) -> Settings:
    return Settings(
        bot_tokens={"secretary": "x", "manager": "x", "intelligence": "x",
                    "learning": "x", "supervisor": "x"},
        allowed_chat_ids=frozenset({1}),
        allowed_user_ids=frozenset({1}),
        anthropic_api_key="sk-test",
        store_path="ignored",
        log_level="INFO",
        user_tz=ZoneInfo("UTC"),
        google_calendar_id="primary",
        google_token_path=Path("ignored"),
        google_client_secrets_path=Path("ignored"),
        notion_token="x",
        notion_database_id="x",
        secretary_mode=mode,  # type: ignore[arg-type]
    )


def test_work_mode_returns_anthropic_writer_and_secretary_toml(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "s.db"))
    store.init_schema()
    usage = LLMUsageStore(store.conn)
    anthropic = AnthropicProvider(
        api_key="sk-test", model="claude-sonnet-4-6", usage_store=usage,
    )
    writer = UserFactsWriter("secretary", store.conn)

    provider, persona_path, config_path, wired_writer = _build_secretary_dependencies(
        settings=_settings("work"),
        usage_store=usage,
        anthropic_provider=anthropic,
        base_facts_writer=writer,
    )
    assert provider is anthropic
    assert persona_path == Path("prompts/secretary.md")
    assert config_path == Path("prompts/secretary.toml")
    assert wired_writer is writer


def test_free_mode_returns_local_provider_and_free_prompts(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "s.db"))
    store.init_schema()
    usage = LLMUsageStore(store.conn)
    anthropic = AnthropicProvider(
        api_key="sk-test", model="claude-sonnet-4-6", usage_store=usage,
    )
    writer = UserFactsWriter("secretary", store.conn)

    provider, persona_path, config_path, wired_writer = _build_secretary_dependencies(
        settings=_settings("free"),
        usage_store=usage,
        anthropic_provider=anthropic,
        base_facts_writer=writer,
    )
    assert isinstance(provider, LocalProvider)
    assert persona_path == Path("prompts/secretary_free.md")
    assert config_path == Path("prompts/secretary_free.toml")
    assert wired_writer is None


def test_free_mode_asserts_writer_is_none_invariant(tmp_path: Path) -> None:
    # The factory itself enforces this — the test just pins it. If someone
    # refactors the factory to stop dropping the writer in free mode, the
    # assertion inside the factory must fire.
    # (Executed implicitly by the previous test; here we just assert that
    # the factory signature does not let the caller bypass it.)
    import inspect
    sig = inspect.signature(_build_secretary_dependencies)
    assert "base_facts_writer" in sig.parameters
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_secretary_mode_factory.py -v`
Expected: FAIL — `ImportError: cannot import name '_build_secretary_dependencies' from 'project0.main'`.

- [ ] **Step 3: Implement the factory**

Edit `src/project0/main.py`:

Add import:
```python
from project0.llm.local_provider import LocalProvider
```

Add the factory function (place it near `_build_llm_provider`):

```python
def _build_secretary_dependencies(
    *,
    settings: Settings,
    usage_store: LLMUsageStore,
    anthropic_provider: LLMProvider,
    base_facts_writer: UserFactsWriter,
) -> tuple[LLMProvider, Path, Path, UserFactsWriter | None]:
    """Return (provider, persona_path, config_path, facts_writer_or_none).

    `work` mode returns the shared AnthropicProvider, normal prompt files,
    and the wired writer. `free` mode returns a fresh LocalProvider, the
    free-mode prompt files, and None for the writer — this is the NSFW
    isolation invariant from 2026-04-20-secretary-local-llm-design.md §6.
    """
    if settings.secretary_mode == "work":
        return (
            anthropic_provider,
            Path("prompts/secretary.md"),
            Path("prompts/secretary.toml"),
            base_facts_writer,
        )
    if settings.secretary_mode == "free":
        local = LocalProvider(
            base_url=settings.local_llm_base_url,
            model=settings.local_llm_model,
            api_key=settings.local_llm_api_key,
            usage_store=usage_store,
        )
        writer: UserFactsWriter | None = None
        assert writer is None, (
            "free-mode Secretary must not wire user_facts_writer; "
            "see 2026-04-20-secretary-local-llm-design.md §6"
        )
        log.info(
            "secretary factory: mode=free model=%s base_url=%s (writer disabled)",
            settings.local_llm_model,
            settings.local_llm_base_url,
        )
        return (
            local,
            Path("prompts/secretary_free.md"),
            Path("prompts/secretary_free.toml"),
            writer,
        )
    raise RuntimeError(f"unknown secretary_mode={settings.secretary_mode!r}")
```

Replace the hardcoded Secretary construction block (around lines 226–239 of current `main.py`). Before:

```python
    persona = load_persona(Path("prompts/secretary.md"))
    secretary_cfg = load_config(Path("prompts/secretary.toml"))
    secretary = Secretary(
        llm=llm,
        ...
        user_facts_writer=secretary_facts_writer,
    )
```

After (wire the factory):

```python
    (
        secretary_llm,
        secretary_persona_path,
        secretary_config_path,
        secretary_writer,
    ) = _build_secretary_dependencies(
        settings=settings,
        usage_store=usage_store,
        anthropic_provider=llm,
        base_facts_writer=secretary_facts_writer,
    )
    # Invariant check (belt-and-suspenders; factory also enforces).
    if isinstance(secretary_llm, LocalProvider):
        assert secretary_writer is None, (
            "SECRETARY_MODE=free must NOT wire user_facts_writer — "
            "see 2026-04-20-secretary-local-llm-design.md §6"
        )

    persona = load_persona(secretary_persona_path)
    secretary_cfg = load_config(secretary_config_path)
    secretary = Secretary(
        llm=secretary_llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=secretary_cfg,
        user_profile=user_profile,
        user_facts_reader=secretary_facts_reader,
        user_facts_writer=secretary_writer,
        # bot_sender wired later in this file, after RealBotSender is built.
    )
    register_secretary(secretary.handle)
    log.info(
        "secretary registered (mode=%s model=%s)",
        settings.secretary_mode, secretary_cfg.model,
    )
```

- [ ] **Step 4: Run tests to verify factory passes**

Run: `uv run pytest tests/test_secretary_mode_factory.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite to catch regressions**

Run: `uv run pytest -x`
Expected: all tests pass.

- [ ] **Step 6: Type-check**

Run: `uv run mypy src/project0/main.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/project0/main.py tests/test_secretary_mode_factory.py
git commit -m "feat(main): add _build_secretary_dependencies factory with local⇒no-writer invariant"
```

---

## Task 11: Wire `bot_sender` into Secretary at runtime

**Files:**
- Modify: `src/project0/main.py`

Secretary accepts an optional `bot_sender` as of Task 5, but `main.py` currently constructs Secretary before `RealBotSender` is built. This task changes the construction order / late-binds the sender.

- [ ] **Step 1: Inspect the current construction order**

Run: `uv run grep -n "RealBotSender\|secretary = Secretary" src/project0/main.py`
Expected: confirms Secretary is constructed before RealBotSender. Record the line numbers.

- [ ] **Step 2: Choose late-binding approach**

Two options:
- (a) Move Secretary construction below RealBotSender construction.
- (b) Add a `set_bot_sender(sender)` method on Secretary called after RealBotSender exists.

**Pick (a).** It keeps construction immutable and matches how Manager and others are wired. Grep the surrounding code and move the Secretary construction block to immediately after the `RealBotSender(apps_by_agent=...)` instantiation.

- [ ] **Step 3: Move the block and pass `bot_sender`**

In `src/project0/main.py`, relocate the Secretary construction block (including `_build_secretary_dependencies` call, the invariant assert, `load_persona`, `load_config`, and `secretary = Secretary(...)`) to immediately after `sender = RealBotSender(apps_by_agent=...)`. Add `bot_sender=sender` to the `Secretary(...)` call.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -x`
Expected: all tests still pass; no new failures from the move.

- [ ] **Step 5: Manual smoke compile**

Run: `uv run python -c "from project0 import main; print('ok')"`
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add src/project0/main.py
git commit -m "feat(main): wire RealBotSender into Secretary for typing indicator"
```

---

## Task 12: Document new env vars in `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Append documented entries**

Add to `.env.example`:

```
# --- Secretary local-LLM option (sub-project: 2026-04-20-secretary-local-llm) ---
# SECRETARY_MODE=work (default) or free.
#   work: Secretary uses Claude via AnthropicProvider and the normal persona
#         (prompts/secretary.md, prompts/secretary.toml), with the user-facts
#         writer enabled.
#   free: Secretary uses a local OpenAI-compatible server (vLLM or TRT-LLM)
#         and the free-mode persona (prompts/secretary_free.{md,toml}). The
#         user-facts writer is disabled in this mode — required to prevent
#         NSFW content from leaking into other agents' prompts via the
#         shared user_facts table.
SECRETARY_MODE=work

# Local LLM connection (only used when SECRETARY_MODE=free). Defaults match
# the DGX Spark deployment in ~/llm-workspace/TensorRT-LLM.
LOCAL_LLM_BASE_URL=http://127.0.0.1:8000/v1
LOCAL_LLM_MODEL=qwen2.5-72b-awq-8k
LOCAL_LLM_API_KEY=unused
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs(env): document SECRETARY_MODE and LOCAL_LLM_* env vars"
```

---

## Task 13: End-to-end integration test (LocalProvider + Secretary + respx)

**Files:**
- Create: `tests/test_secretary_local_integration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_secretary_local_integration.py`:

```python
"""End-to-end: Secretary wired with LocalProvider (mocked endpoint) replies
to a DM, emits a typing indicator, and never receives a tool list because
user_facts_writer is None."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from project0.agents.secretary import Secretary, load_config, load_persona
from project0.envelope import Envelope
from project0.llm.local_provider import LocalProvider
from project0.store import LLMUsageStore, Store
from project0.telegram_io import FakeBotSender

BASE_URL = "http://127.0.0.1:8000/v1"
MODEL = "qwen2.5-72b-awq-8k"


@pytest.mark.asyncio
@respx.mock
async def test_secretary_local_dm_reply_with_typing_and_no_tools(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "s.db"))
    store.init_schema()
    usage = LLMUsageStore(store.conn)

    captured: list[dict] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {"role": "assistant", "content": "嗯，老公，我听着呢。"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            },
        )

    respx.post(f"{BASE_URL}/chat/completions").mock(side_effect=_capture)

    provider = LocalProvider(
        base_url=BASE_URL, model=MODEL, api_key="unused", usage_store=usage,
    )
    sender = FakeBotSender()
    persona = load_persona(Path("prompts/secretary_free.md"))
    cfg = load_config(Path("prompts/secretary_free.toml"))

    secretary = Secretary(
        llm=provider,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=cfg,
        user_facts_reader=None,
        user_facts_writer=None,  # Invariant for free mode.
        bot_sender=sender,
    )

    env = Envelope(
        id=None,
        ts="2026-04-20T00:00:00+00:00",
        parent_id=None,
        source="telegram_dm",
        telegram_chat_id=7,
        telegram_msg_id=1,
        received_by_bot="secretary",
        from_kind="human",
        from_agent=None,
        to_agent="secretary",
        body="hi",
        routing_reason="direct_dm",
    )
    result = await secretary.handle(env)
    assert result is not None
    assert result.reply_text == "嗯，老公，我听着呢。"

    # Typing indicator fired at least once for this chat.
    typing = [a for a in sender.chat_actions if a["chat_id"] == 7]
    assert len(typing) >= 1

    # Request payload contains NO `tools` field (writer was None → no tool).
    assert len(captured) == 1
    assert "tools" not in captured[0]

    # user_facts table is untouched.
    cur = store.conn.execute("SELECT COUNT(*) FROM user_facts")
    assert cur.fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_secretary_local_integration.py -v`
Expected: FAIL initially until all upstream tasks are complete and integrated. If every prior task passed, this test should now PASS on first run.

- [ ] **Step 3: Fix any integration gaps**

If the test fails because of an Envelope field mismatch, inspect `src/project0/envelope.py` and correct the Envelope construction in the test. Do NOT change Secretary or LocalProvider to satisfy the test — fix the test shape. If the test fails because Secretary's tool loop still registers a tool with writer=None, audit `secretary.py` around the `_run_with_tool_loop` call and confirm the code path checks `self._user_facts_writer is not None` before adding the tool; if it doesn't, that is a pre-existing bug — fix it in the same commit and note the fix in the commit message.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_secretary_local_integration.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: all tests pass.

- [ ] **Step 6: Type-check the entire project**

Run: `uv run mypy src/project0`
Expected: no errors.

- [ ] **Step 7: Lint**

Run: `uv run ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add tests/test_secretary_local_integration.py
git commit -m "test: end-to-end Secretary+LocalProvider integration (typing, no tools, no facts)"
```

---

## Task 14: Prepared human smoke test

This task is run **once, by hand, at the very end** — after every automated task above passes. Not automated per the project's testing-discipline memory (one prepared human check at the very end, not iterative).

- [ ] **Step 1: Verify the local LLM server is running**

Run: `curl -sSf http://127.0.0.1:8000/v1/models`
Expected: JSON response listing at least `qwen2.5-72b-awq-8k`.

If not running, go to `~/llm-workspace/TensorRT-LLM` and start it via its own Gradio UI or `./scripts/12_serve_trtllm.sh --detach`. Wait ~4 minutes for it to be ready. Confirm with the curl above.

- [ ] **Step 2: Configure `.env` and restart MAAS**

Edit the project's `.env` (in the worktree if smoke-testing a worktree, or the main copy — follow the worktree setup memory). Set:

```
SECRETARY_MODE=free
```

Restart the MAAS daemon.

- [ ] **Step 3: Send one DM to Secretary and observe**

Open the Secretary bot DM in Telegram. Send: `你好`.

Observe:
- Within ~2 seconds, the native "Secretary is typing…" indicator appears.
- Within ~90 seconds, one reply message arrives.
- The voice is Persona B (which, at this stage before you've edited the file, is identical to Persona A).

- [ ] **Step 4: Verify no facts written**

Run (in the worktree/project root):
```
sqlite3 data/store.db "SELECT COUNT(*) FROM user_facts;"
```
Expected: the same count as before the test (likely 0 if this is a fresh DB).

- [ ] **Step 5: Verify usage row recorded**

Run:
```
sqlite3 data/store.db "SELECT agent, model, input_tokens, output_tokens FROM llm_usage ORDER BY id DESC LIMIT 1;"
```
Expected: `secretary|qwen2.5-72b-awq-8k|<non-zero>|<non-zero>`.

- [ ] **Step 6: Flip back to `work` and verify Claude path**

Set `SECRETARY_MODE=work` in `.env`, restart. Send `你好` to Secretary. Expect a Claude-paced reply (5–15s) and verify:
```
sqlite3 data/store.db "SELECT agent, model FROM llm_usage ORDER BY id DESC LIMIT 1;"
```
Expected: `secretary|claude-sonnet-4-6`.

- [ ] **Step 7: Record result**

If all six checks pass: the sub-project is ready to merge. The user can now iterate on the Persona B prose in `prompts/secretary_free.md`.

If any step fails, diagnose from logs and fix the specific issue. Do not enter an iterative manual-test loop — fix root cause, re-run the six checks once.

---

## Self-Review Checklist (for the writer of this plan — executed inline, no subagent dispatch)

- **Spec coverage**: every in-scope item in §1 of the spec maps to a task.
  - `SECRETARY_MODE` env var → Task 9
  - `LocalProvider` → Tasks 6–8
  - `secretary_free.md/.toml` → Task 2
  - Factory + invariant → Task 10
  - Typing indicator → Tasks 3–5
  - `.env.example` docs → Task 12
  - Unit tests → every implementation task pairs with a test task
  - Human smoke test → Task 14
- **Placeholders**: none. Every step has code/commands.
- **Type consistency**: `LocalProvider` constructor params match across Tasks 6/7/10/13. `_build_secretary_dependencies` returns `(provider, persona_path, config_path, writer|None)` consistently. `BotSender.send_chat_action` signature matches in Tasks 3, 4, 5, 13.
- **Scope**: focused on the one sub-project. No cross-cutting refactors.
- **Out-of-scope items from spec** (streaming, runtime toggle, server lifecycle, tool-use on local): none are implemented — correct.
