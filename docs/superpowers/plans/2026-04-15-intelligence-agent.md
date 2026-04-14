# Intelligence Agent Implementation Plan (Sub-project 6d)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `intelligence_stub` with a real LLM-backed Intelligence agent that ingests Twitter/X via twitterapi.io, generates a structured DailyReport JSON file via a one-Opus-call deterministic pipeline, and answers user questions about the latest report via a Sonnet tool-use agentic loop.

**Architecture:** New `src/project0/intelligence/` package holds the Twitter source (protocol + twitterapi.io client + fake), watchlist loader, report schema/storage, generation pipeline, and summarizer prompts. A thin `Intelligence` class in `src/project0/agents/intelligence.py` dispatches chat turns through a shared tool-use loop (extracted from `Manager` into `agents/_tool_loop.py`) with four tools: `generate_daily_report`, `get_latest_report`, `get_report`, `list_reports`. Reports are flat JSON files under `data/intelligence/reports/YYYY-MM-DD.json`. Intelligence takes two LLM providers: Opus for the summarizer, Sonnet for Q&A.

**Tech Stack:** Python 3.12, asyncio, httpx, Anthropic SDK (existing), tomllib, pytest, pytest-asyncio, `typing.Protocol` for `TwitterSource`.

**Spec:** `docs/superpowers/specs/2026-04-15-intelligence-agent-design.md`

---

## File Structure

**New files:**

- `src/project0/intelligence/__init__.py` — empty package marker
- `src/project0/intelligence/source.py` — `Tweet`, `TwitterSource` protocol, `TwitterSourceError`
- `src/project0/intelligence/fake_source.py` — `FakeTwitterSource` for tests
- `src/project0/intelligence/twitterapi_io.py` — `TwitterApiIoSource` concrete HTTP client
- `src/project0/intelligence/watchlist.py` — `WatchEntry`, `load_watchlist`
- `src/project0/intelligence/report.py` — `validate_report_dict`, `atomic_write_json`, `read_report`, `list_report_dates`, `parse_json_strict`
- `src/project0/intelligence/summarizer_prompt.py` — `SUMMARIZER_SYSTEM_PROMPT`, `build_user_prompt`, `build_qa_user_prompt`, `build_delegated_user_prompt`
- `src/project0/intelligence/generate.py` — `generate_daily_report` pipeline function
- `src/project0/agents/_tool_loop.py` — `TurnState`, `LoopResult`, `run_agentic_loop` (extracted from Manager)
- `prompts/intelligence.md` — Chinese persona, 5 sections
- `prompts/intelligence.toml` — `[llm.summarizer]`, `[llm.qa]`, `[context]`, `[twitter]`, `[[watch]]` array
- Tests: one file per new module (see individual tasks)

**Modified files:**

- `src/project0/agents/manager.py` — `_agentic_loop` becomes a thin wrapper around `run_agentic_loop`; `TurnState` re-exported from `_tool_loop.py`
- `src/project0/agents/intelligence.py` — full rewrite: `IntelligencePersona`, `IntelligenceConfig`, loaders, `Intelligence` class
- `src/project0/agents/registry.py` — add `register_intelligence`, remove `intelligence_stub` import
- `src/project0/main.py` — wire Intelligence (two providers, watchlist, reports_dir, registration)
- `.gitignore` — add `data/intelligence/reports/`
- `tests/test_agents.py` — remove stub assertions, add register_intelligence assertion

---

## Task 1: Extract agentic loop into `agents/_tool_loop.py`

**Goal:** Move `TurnState` + the core `while iter: complete_with_tools → dispatch → append` loop out of `Manager._agentic_loop` into a reusable helper. Behavior-preserving: all existing Manager tests must still pass.

**Files:**
- Create: `src/project0/agents/_tool_loop.py`
- Modify: `src/project0/agents/manager.py` (remove `TurnState`, rewrite `_agentic_loop`)
- Test: `tests/agents/test_tool_loop_shared.py`

- [ ] **Step 1: Write the failing test for `run_agentic_loop`**

Create `tests/agents/test_tool_loop_shared.py`:

```python
"""Shared agentic loop helper tests. The helper is the extraction of
Manager._agentic_loop into a reusable module. Intelligence will also use it.
These tests verify the loop mechanics in isolation from any agent class."""
from __future__ import annotations

import pytest

from project0.agents._tool_loop import LoopResult, TurnState, run_agentic_loop
from project0.llm.provider import FakeProvider, LLMProviderError, Msg
from project0.llm.tools import ToolCall, ToolSpec, ToolUseResult

TOOL_ECHO = ToolSpec(
    name="echo",
    description="Echo the input back.",
    input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
)


async def _dispatch_echo(call: ToolCall, turn_state: TurnState) -> tuple[str, bool]:
    if call.name == "echo":
        return call.input.get("text", ""), False
    return f"unknown tool: {call.name}", True


@pytest.mark.asyncio
async def test_plain_text_response_returns_loop_result():
    llm = FakeProvider(tool_responses=[
        ToolUseResult(kind="text", text="hello world", tool_calls=[], stop_reason="end_turn"),
    ])
    result = await run_agentic_loop(
        llm=llm,
        system="sys",
        initial_user_text="hi",
        tools=[TOOL_ECHO],
        dispatch_tool=_dispatch_echo,
        max_iterations=4,
        max_tokens=256,
    )
    assert isinstance(result, LoopResult)
    assert result.final_text == "hello world"
    assert result.errored is False
    assert result.turn_state.delegation_target is None


@pytest.mark.asyncio
async def test_tool_use_then_text_runs_dispatch_then_returns_text():
    llm = FakeProvider(tool_responses=[
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(id="t1", name="echo", input={"text": "ping"})],
            stop_reason="tool_use",
        ),
        ToolUseResult(kind="text", text="ping done", tool_calls=[], stop_reason="end_turn"),
    ])
    result = await run_agentic_loop(
        llm=llm,
        system="sys",
        initial_user_text="hi",
        tools=[TOOL_ECHO],
        dispatch_tool=_dispatch_echo,
        max_iterations=4,
        max_tokens=256,
    )
    assert result.final_text == "ping done"
    assert result.errored is False


@pytest.mark.asyncio
async def test_llm_error_is_caught_and_flagged():
    # FakeProvider exhaustion raises LLMProviderError on the first call.
    llm = FakeProvider(tool_responses=[])
    result = await run_agentic_loop(
        llm=llm,
        system="sys",
        initial_user_text="hi",
        tools=[TOOL_ECHO],
        dispatch_tool=_dispatch_echo,
        max_iterations=4,
        max_tokens=256,
    )
    assert result.errored is True
    assert result.final_text is None


@pytest.mark.asyncio
async def test_iteration_overflow_raises():
    # Keep returning tool_use forever → loop exceeds max_iterations.
    infinite = [
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(id=f"t{i}", name="echo", input={"text": "x"})],
            stop_reason="tool_use",
        )
        for i in range(10)
    ]
    llm = FakeProvider(tool_responses=infinite)
    with pytest.raises(LLMProviderError, match="max_iterations"):
        await run_agentic_loop(
            llm=llm,
            system="sys",
            initial_user_text="hi",
            tools=[TOOL_ECHO],
            dispatch_tool=_dispatch_echo,
            max_iterations=3,
            max_tokens=256,
        )


@pytest.mark.asyncio
async def test_dispatch_tool_sees_turn_state_and_mutation_persists():
    async def dispatch_set_delegation(call: ToolCall, turn_state: TurnState) -> tuple[str, bool]:
        turn_state.delegation_target = "secretary"
        turn_state.delegation_handoff = "please remind X"
        turn_state.delegation_payload = {"kind": "reminder_request"}
        return "delegated", False

    llm = FakeProvider(tool_responses=[
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(id="t1", name="echo", input={"text": "x"})],
            stop_reason="tool_use",
        ),
        ToolUseResult(kind="text", text="ok", tool_calls=[], stop_reason="end_turn"),
    ])
    result = await run_agentic_loop(
        llm=llm,
        system="sys",
        initial_user_text="hi",
        tools=[TOOL_ECHO],
        dispatch_tool=dispatch_set_delegation,
        max_iterations=4,
        max_tokens=256,
    )
    assert result.turn_state.delegation_target == "secretary"
    assert result.turn_state.delegation_handoff == "please remind X"
    assert result.turn_state.delegation_payload == {"kind": "reminder_request"}
    assert result.final_text == "ok"
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/agents/test_tool_loop_shared.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'project0.agents._tool_loop'`.

- [ ] **Step 3: Create `src/project0/agents/_tool_loop.py`**

```python
"""Shared agentic tool-use loop used by Manager (6c) and Intelligence (6d).

Extracted from ``Manager._agentic_loop`` in 6d so both agents can drive the
same loop mechanics without duplication. The loop is stateless across calls
— all per-turn state lives in a local ``TurnState`` so concurrent turns
cannot cross-contaminate.

Semantics:
  - iterate up to ``max_iterations`` times
  - each iteration calls ``llm.complete_with_tools`` with the running
    messages list
  - on ``kind='text'``: return ``LoopResult(final_text=..., errored=False)``
  - on ``kind='tool_use'``: append an ``AssistantToolUseMsg``, run
    ``dispatch_tool`` for every tool_call, append a ``ToolResultMsg`` for
    each, continue
  - ``LLMProviderError`` from ``complete_with_tools`` is caught and surfaced
    as ``LoopResult(final_text=None, errored=True)``; the caller decides
    whether to drop the turn
  - exceeding ``max_iterations`` raises ``LLMProviderError``

Callers are responsible for finalizing the ``LoopResult`` into an
``AgentResult`` — Manager applies its pulse-silent and delegation-wins
rules; Intelligence just maps ``final_text`` straight to ``reply_text``.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from project0.llm.provider import LLMProvider, LLMProviderError, Msg
from project0.llm.tools import (
    AssistantToolUseMsg,
    ToolCall,
    ToolResultMsg,
    ToolSpec,
)

log = logging.getLogger(__name__)


@dataclass
class TurnState:
    """Mutable state for one agentic loop invocation. Lives in the local
    scope of ``run_agentic_loop`` so concurrent turns never share state."""
    delegation_target: str | None = None
    delegation_handoff: str | None = None
    delegation_payload: dict[str, Any] | None = None


@dataclass
class LoopResult:
    """Raw output of ``run_agentic_loop``. Callers finalize it into
    ``AgentResult`` using agent-specific rules (pulse suppression,
    delegation-wins, etc)."""
    final_text: str | None
    turn_state: TurnState
    errored: bool = False


DispatchTool = Callable[[ToolCall, TurnState], Awaitable[tuple[str, bool]]]


async def run_agentic_loop(
    *,
    llm: LLMProvider,
    system: str,
    initial_user_text: str,
    tools: list[ToolSpec],
    dispatch_tool: DispatchTool,
    max_iterations: int,
    max_tokens: int,
) -> LoopResult:
    turn_state = TurnState()
    messages: list = [Msg(role="user", content=initial_user_text)]

    for _iter in range(max_iterations):
        try:
            result = await llm.complete_with_tools(
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
            )
        except LLMProviderError as e:
            log.warning("tool loop LLM call failed: %s", e)
            return LoopResult(final_text=None, turn_state=turn_state, errored=True)

        if result.kind == "text":
            return LoopResult(
                final_text=result.text or "",
                turn_state=turn_state,
                errored=False,
            )

        # tool_use branch
        messages.append(
            AssistantToolUseMsg(
                tool_calls=list(result.tool_calls),
                text=result.text,
            )
        )
        for call in result.tool_calls:
            content_str, is_err = await dispatch_tool(call, turn_state)
            messages.append(
                ToolResultMsg(
                    tool_use_id=call.id,
                    content=content_str,
                    is_error=is_err,
                )
            )

    raise LLMProviderError(f"tool loop exceeded max_iterations={max_iterations}")
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run pytest tests/agents/test_tool_loop_shared.py -v`
Expected: all five tests PASS.

- [ ] **Step 5: Refactor `Manager._agentic_loop` to use the shared helper**

Edit `src/project0/agents/manager.py`:

1. Replace the local `TurnState` class definition (lines 143–149) with a re-export:

```python
# --- per-turn state ----------------------------------------------------------

from project0.agents._tool_loop import LoopResult, TurnState, run_agentic_loop  # re-exported for tests
```

(Delete the `@dataclass class TurnState: ...` block that follows — `TurnState` now lives in `_tool_loop.py`. Keep the import at the module level near the other imports rather than mid-file if you prefer; either works.)

2. Replace the body of `_agentic_loop` (lines 510–577) with:

```python
    async def _agentic_loop(
        self,
        *,
        system: str,
        initial_user_text: str,
        max_tokens: int,
        is_pulse: bool,
    ) -> AgentResult | None:
        assert self._llm is not None
        loop = await run_agentic_loop(
            llm=self._llm,
            system=system,
            initial_user_text=initial_user_text,
            tools=self._tool_specs,
            dispatch_tool=self._dispatch_tool,
            max_iterations=self._config.max_tool_iterations,
            max_tokens=max_tokens,
        )
        if loop.errored:
            return None

        turn_state = loop.turn_state
        if turn_state.delegation_target is not None:
            # Delegation queued: suppress the trailing text, return as delegation.
            return AgentResult(
                reply_text=None,
                delegate_to=turn_state.delegation_target,
                handoff_text=turn_state.delegation_handoff,
                delegation_payload=turn_state.delegation_payload,
            )

        # Pulse path: a plain-text result means "nothing to do". Return None so
        # the orchestrator does NOT emit a visible Telegram message. The pulse
        # envelope itself is already persisted by handle_pulse as the audit
        # trail; Manager's internal reasoning does not need to reach the user.
        if is_pulse:
            return None

        return AgentResult(
            reply_text=loop.final_text or "",
            delegate_to=None,
            handoff_text=None,
        )
```

3. Delete the now-unused imports from `manager.py`'s header: `AssistantToolUseMsg`, `ToolResultMsg`, `ToolUseResult`. Keep `ToolCall`, `ToolSpec` (still used by `_dispatch_tool` and `_build_tool_specs`). Keep `Msg` only if still used elsewhere in the file (grep to check); in the current file the only remaining `Msg` use was inside `_agentic_loop`, so drop it.

- [ ] **Step 6: Run all Manager tests to verify behavior is preserved**

Run: `uv run pytest tests/agents/test_manager_tool_loop.py tests/agents/test_manager_tool_dispatch.py tests/agents/test_manager_persona_load.py tests/agents/test_register_manager.py -v`
Expected: every test PASSES unchanged. If any test imports `TurnState` from `project0.agents.manager`, it still works because of the re-export at the top of `manager.py`.

- [ ] **Step 7: Run the full test suite to catch any other fallout**

Run: `uv run pytest -q`
Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/project0/agents/_tool_loop.py src/project0/agents/manager.py tests/agents/test_tool_loop_shared.py
git commit -m "$(cat <<'EOF'
refactor(agents): extract agentic tool-use loop into _tool_loop.py

Extracted TurnState + the inner iterate-complete_with_tools-dispatch-append
body from Manager._agentic_loop into agents/_tool_loop.py as
run_agentic_loop + LoopResult. Manager.finalization (pulse suppression,
delegation-wins-over-text) stays inside Manager where it belongs.

Behavior-preserving. All existing Manager tests pass unchanged.
Sets up 6d Intelligence to reuse the same loop mechanics.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: TwitterSource protocol + Tweet dataclass + TwitterSourceError

**Goal:** Define the abstract contract for pulling tweets, with one data type (`Tweet`), one protocol (`TwitterSource`), and one exception (`TwitterSourceError`). Concrete implementations come in later tasks.

**Files:**
- Create: `src/project0/intelligence/__init__.py`
- Create: `src/project0/intelligence/source.py`
- Create: `tests/intelligence/__init__.py`
- Test: `tests/intelligence/test_source_types.py`

- [ ] **Step 1: Write the failing test**

Create `tests/intelligence/__init__.py` as an empty file, then `tests/intelligence/test_source_types.py`:

```python
"""Basic type + protocol shape tests for intelligence.source.
No HTTP or async work — just confirm the data types and protocol are
importable and have the expected fields. Concrete source tests live in
test_twitterapi_io_source.py and test_fake_source.py."""
from __future__ import annotations

from datetime import UTC, datetime

from project0.intelligence.source import Tweet, TwitterSource, TwitterSourceError


def test_tweet_dataclass_has_expected_fields():
    t = Tweet(
        handle="sama",
        tweet_id="123",
        url="https://x.com/sama/status/123",
        text="hello",
        posted_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        reply_count=1,
        like_count=2,
        retweet_count=3,
    )
    assert t.handle == "sama"
    assert t.tweet_id == "123"
    assert t.url == "https://x.com/sama/status/123"
    assert t.text == "hello"
    assert t.posted_at.tzinfo is not None
    assert t.reply_count == 1
    assert t.like_count == 2
    assert t.retweet_count == 3


def test_tweet_is_frozen():
    import dataclasses
    t = Tweet(
        handle="sama", tweet_id="1", url="u", text="t",
        posted_at=datetime(2026, 4, 15, tzinfo=UTC),
        reply_count=0, like_count=0, retweet_count=0,
    )
    try:
        t.handle = "other"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("Tweet should be frozen")


def test_twitter_source_is_a_protocol():
    # Protocol classes cannot be instantiated directly.
    from typing import Protocol, runtime_checkable
    # Just verify it's importable and isinstance-checking works structurally.
    assert hasattr(TwitterSource, "fetch_user_timeline")
    assert hasattr(TwitterSource, "fetch_tweet")
    assert hasattr(TwitterSource, "search")


def test_twitter_source_error_is_exception():
    err = TwitterSourceError("boom")
    assert isinstance(err, Exception)
    assert str(err) == "boom"
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/intelligence/test_source_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'project0.intelligence'`.

- [ ] **Step 3: Create the package and source module**

Create `src/project0/intelligence/__init__.py` as an empty file.

Create `src/project0/intelligence/source.py`:

```python
"""Protocol + data types for pulling tweets from an external source.

6d has one concrete implementation (``TwitterApiIoSource`` hitting
twitterapi.io) plus a ``FakeTwitterSource`` for tests. ``fetch_tweet`` and
``search`` are declared on the protocol but not used in 6d — they are
placeholders so 6f (on-demand tweet lookup) and 6g (search-based pulses)
don't need to reshape the protocol."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Tweet:
    handle: str
    tweet_id: str
    url: str
    text: str
    posted_at: datetime       # timezone-aware, UTC preferred
    reply_count: int
    like_count: int
    retweet_count: int


class TwitterSourceError(Exception):
    """Raised when a twitter source cannot fulfill a request.

    Concrete sources catch underlying HTTP/network/parse errors and
    re-raise as TwitterSourceError with a short human-readable message.
    Callers (e.g. generate_daily_report) catch this at the per-handle
    boundary and record the failure, letting other handles continue."""


class TwitterSource(Protocol):
    async def fetch_user_timeline(
        self,
        handle: str,
        *,
        since: datetime,
        max_results: int,
    ) -> list[Tweet]:
        ...

    async def fetch_tweet(self, url_or_id: str) -> Tweet:
        ...

    async def search(
        self,
        query: str,
        *,
        since: datetime,
        max_results: int,
    ) -> list[Tweet]:
        ...
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run pytest tests/intelligence/test_source_types.py -v`
Expected: all four tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/project0/intelligence/__init__.py src/project0/intelligence/source.py tests/intelligence/__init__.py tests/intelligence/test_source_types.py
git commit -m "feat(intelligence): TwitterSource protocol + Tweet dataclass

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: FakeTwitterSource

**Goal:** An in-memory `TwitterSource` implementation seeded from a dict, used by every non-live test. Mirrors the shape of `FakeProvider` in `llm/provider.py`.

**Files:**
- Create: `src/project0/intelligence/fake_source.py`
- Test: `tests/intelligence/test_fake_source.py`

- [ ] **Step 1: Write the failing test**

Create `tests/intelligence/test_fake_source.py`:

```python
"""FakeTwitterSource behavior tests. The fake must: return seeded tweets,
filter by ``since``, respect ``max_results``, and raise TwitterSourceError
on unknown handles so tests that exercise the per-handle failure path
actually hit the error branch in generate_daily_report."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from project0.intelligence.fake_source import FakeTwitterSource
from project0.intelligence.source import Tweet, TwitterSourceError


def _tweet(handle: str, tid: str, posted_at: datetime, text: str = "t") -> Tweet:
    return Tweet(
        handle=handle,
        tweet_id=tid,
        url=f"https://x.com/{handle}/status/{tid}",
        text=text,
        posted_at=posted_at,
        reply_count=0,
        like_count=0,
        retweet_count=0,
    )


@pytest.mark.asyncio
async def test_fetch_user_timeline_returns_seeded_tweets():
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    src = FakeTwitterSource(
        timelines={
            "sama": [
                _tweet("sama", "1", now - timedelta(hours=1)),
                _tweet("sama", "2", now - timedelta(hours=2)),
            ]
        }
    )
    got = await src.fetch_user_timeline("sama", since=now - timedelta(hours=3), max_results=10)
    assert [t.tweet_id for t in got] == ["1", "2"]


@pytest.mark.asyncio
async def test_fetch_user_timeline_filters_by_since():
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    src = FakeTwitterSource(
        timelines={
            "sama": [
                _tweet("sama", "recent", now - timedelta(hours=1)),
                _tweet("sama", "old", now - timedelta(days=5)),
            ]
        }
    )
    got = await src.fetch_user_timeline("sama", since=now - timedelta(hours=2), max_results=10)
    assert [t.tweet_id for t in got] == ["recent"]


@pytest.mark.asyncio
async def test_fetch_user_timeline_respects_max_results():
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    src = FakeTwitterSource(
        timelines={
            "sama": [_tweet("sama", str(i), now - timedelta(minutes=i)) for i in range(10)]
        }
    )
    got = await src.fetch_user_timeline("sama", since=now - timedelta(days=1), max_results=3)
    assert len(got) == 3
    # Newest first.
    assert [t.tweet_id for t in got] == ["0", "1", "2"]


@pytest.mark.asyncio
async def test_unknown_handle_raises_twitter_source_error():
    src = FakeTwitterSource(timelines={})
    with pytest.raises(TwitterSourceError, match="unknown handle"):
        await src.fetch_user_timeline("nobody", since=datetime(2026, 1, 1, tzinfo=UTC), max_results=5)


@pytest.mark.asyncio
async def test_fetch_tweet_and_search_not_implemented():
    src = FakeTwitterSource(timelines={})
    with pytest.raises(NotImplementedError):
        await src.fetch_tweet("x")
    with pytest.raises(NotImplementedError):
        await src.search("q", since=datetime(2026, 1, 1, tzinfo=UTC), max_results=5)
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/intelligence/test_fake_source.py -v`
Expected: FAIL with `ImportError: cannot import name 'FakeTwitterSource'`.

- [ ] **Step 3: Create `fake_source.py`**

```python
"""In-memory TwitterSource for tests. Seeded from a dict of handle → list
of Tweet. Filters by ``since`` and truncates to ``max_results``. Unknown
handles raise TwitterSourceError so tests that exercise the per-handle
failure path in generate_daily_report actually hit the error branch."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from project0.intelligence.source import Tweet, TwitterSourceError


@dataclass
class FakeTwitterSource:
    timelines: dict[str, list[Tweet]] = field(default_factory=dict)

    async def fetch_user_timeline(
        self,
        handle: str,
        *,
        since: datetime,
        max_results: int,
    ) -> list[Tweet]:
        handle = handle.lstrip("@").lower()
        if handle not in self.timelines:
            raise TwitterSourceError(f"unknown handle: {handle}")
        filtered = [t for t in self.timelines[handle] if t.posted_at >= since]
        filtered.sort(key=lambda t: t.posted_at, reverse=True)
        return filtered[:max_results]

    async def fetch_tweet(self, url_or_id: str) -> Tweet:
        raise NotImplementedError("FakeTwitterSource.fetch_tweet not used in 6d")

    async def search(
        self,
        query: str,
        *,
        since: datetime,
        max_results: int,
    ) -> list[Tweet]:
        raise NotImplementedError("FakeTwitterSource.search not used in 6d")
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run pytest tests/intelligence/test_fake_source.py -v`
Expected: all five tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/project0/intelligence/fake_source.py tests/intelligence/test_fake_source.py
git commit -m "feat(intelligence): FakeTwitterSource for tests

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: TwitterApiIoSource (real HTTP client)

**Goal:** Concrete `TwitterSource` talking to twitterapi.io via `httpx.AsyncClient`. Mocked tests only — the optional live smoke test (Task 4b, gated on env var) is added at the end.

**API reference:** twitterapi.io uses a REST API at `https://api.twitterapi.io/twitter/user/last_tweets` with an `x-api-key` header. The exact path and field names are verified at implementation time from their docs; the shape below uses the most common pattern (confirm on their site before running the live smoke test). If the real API returns different JSON, only the response-parsing code inside `_parse_tweet` needs to change — the rest of the module (URL, headers, error handling) is stable.

**Files:**
- Create: `src/project0/intelligence/twitterapi_io.py`
- Test: `tests/intelligence/test_twitterapi_io_source.py`

- [ ] **Step 1: Write the failing test**

Create `tests/intelligence/test_twitterapi_io_source.py`:

```python
"""TwitterApiIoSource tests. All HTTP is mocked via a fake httpx transport.
The response fixture mirrors the shape the real twitterapi.io endpoint
returns; if the real API changes, update ``_FIXTURE`` and the parsing code
in twitterapi_io.py together."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from project0.intelligence.source import TwitterSourceError
from project0.intelligence.twitterapi_io import TwitterApiIoSource

_FIXTURE = {
    "tweets": [
        {
            "id": "123456789",
            "url": "https://x.com/sama/status/123456789",
            "text": "gm, shipping today",
            "createdAt": "2026-04-15T03:17:00.000Z",
            "author": {"userName": "sama"},
            "replyCount": 12,
            "likeCount": 345,
            "retweetCount": 67,
        },
        {
            "id": "123456788",
            "url": "https://x.com/sama/status/123456788",
            "text": "older tweet",
            "createdAt": "2026-04-14T22:00:00.000Z",
            "author": {"userName": "sama"},
            "replyCount": 1,
            "likeCount": 20,
            "retweetCount": 2,
        },
    ]
}


def _mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fetch_user_timeline_happy_path():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json=_FIXTURE)

    src = TwitterApiIoSource(api_key="sk-test", transport=_mock_transport(handler))
    tweets = await src.fetch_user_timeline(
        "sama",
        since=datetime(2026, 4, 14, 0, 0, tzinfo=UTC),
        max_results=50,
    )
    await src.aclose()

    assert "sama" in seen["url"]
    assert seen["headers"].get("x-api-key") == "sk-test"
    assert len(tweets) == 2
    assert tweets[0].tweet_id == "123456789"
    assert tweets[0].handle == "sama"
    assert tweets[0].text == "gm, shipping today"
    assert tweets[0].posted_at.tzinfo is not None
    assert tweets[0].like_count == 345


@pytest.mark.asyncio
async def test_fetch_user_timeline_filters_by_since_client_side():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_FIXTURE)

    src = TwitterApiIoSource(api_key="sk-test", transport=_mock_transport(handler))
    tweets = await src.fetch_user_timeline(
        "sama",
        since=datetime(2026, 4, 15, 0, 0, tzinfo=UTC),
        max_results=50,
    )
    await src.aclose()
    # Only the tweet from 03:17 on the 15th; the 14th 22:00 one is filtered out.
    assert [t.tweet_id for t in tweets] == ["123456789"]


@pytest.mark.asyncio
async def test_http_error_raises_twitter_source_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server blew up")

    src = TwitterApiIoSource(api_key="sk-test", transport=_mock_transport(handler))
    with pytest.raises(TwitterSourceError, match="HTTP 500"):
        await src.fetch_user_timeline(
            "sama", since=datetime(2026, 4, 14, tzinfo=UTC), max_results=10,
        )
    await src.aclose()


@pytest.mark.asyncio
async def test_malformed_json_raises_twitter_source_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    src = TwitterApiIoSource(api_key="sk-test", transport=_mock_transport(handler))
    with pytest.raises(TwitterSourceError, match="malformed response"):
        await src.fetch_user_timeline(
            "sama", since=datetime(2026, 4, 14, tzinfo=UTC), max_results=10,
        )
    await src.aclose()


@pytest.mark.asyncio
async def test_empty_timeline_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tweets": []})

    src = TwitterApiIoSource(api_key="sk-test", transport=_mock_transport(handler))
    tweets = await src.fetch_user_timeline(
        "sama", since=datetime(2026, 4, 14, tzinfo=UTC), max_results=10,
    )
    await src.aclose()
    assert tweets == []
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/intelligence/test_twitterapi_io_source.py -v`
Expected: FAIL with `ImportError: cannot import name 'TwitterApiIoSource'`.

- [ ] **Step 3: Create `twitterapi_io.py`**

```python
"""Concrete TwitterSource talking to twitterapi.io.

One httpx.AsyncClient owned per instance. Auth via ``x-api-key`` header.
No retries: twitterapi.io is reliable at watchlist-sized daily load, and
generate_daily_report's partial-failure handling already deals with
per-handle failures.

If twitterapi.io changes its response shape, the only code that needs
updating is ``_parse_tweet`` — everything else (URL, auth, error handling)
is stable."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import httpx

from project0.intelligence.source import Tweet, TwitterSourceError

log = logging.getLogger(__name__)

_BASE_URL = "https://api.twitterapi.io"


class TwitterApiIoSource:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers={"x-api-key": api_key, "accept": "application/json"},
            timeout=timeout_seconds,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_user_timeline(
        self,
        handle: str,
        *,
        since: datetime,
        max_results: int,
    ) -> list[Tweet]:
        handle = handle.lstrip("@")
        try:
            resp = await self._client.get(
                "/twitter/user/last_tweets",
                params={"userName": handle, "count": max_results},
            )
        except httpx.TimeoutException as e:
            raise TwitterSourceError(f"timeout fetching {handle}: {e}") from e
        except httpx.HTTPError as e:
            raise TwitterSourceError(f"http error fetching {handle}: {e}") from e

        if resp.status_code >= 400:
            body = resp.text[:200] if resp.text else ""
            raise TwitterSourceError(f"HTTP {resp.status_code} fetching {handle}: {body}")

        try:
            data = resp.json()
        except (ValueError, json.JSONDecodeError) as e:
            raise TwitterSourceError(f"malformed response for {handle}: {e}") from e

        raw_tweets = data.get("tweets") or []
        if not isinstance(raw_tweets, list):
            raise TwitterSourceError(
                f"malformed response for {handle}: 'tweets' is not a list"
            )

        out: list[Tweet] = []
        for raw in raw_tweets:
            try:
                t = self._parse_tweet(raw, fallback_handle=handle)
            except (KeyError, ValueError, TypeError) as e:
                log.warning("twitterapi_io: skipping malformed tweet: %s", e)
                continue
            if t.posted_at >= since:
                out.append(t)
        # Newest first.
        out.sort(key=lambda t: t.posted_at, reverse=True)
        return out[:max_results]

    async def fetch_tweet(self, url_or_id: str) -> Tweet:
        raise NotImplementedError("fetch_tweet not used in 6d")

    async def search(
        self,
        query: str,
        *,
        since: datetime,
        max_results: int,
    ) -> list[Tweet]:
        raise NotImplementedError("search not used in 6d")

    @staticmethod
    def _parse_tweet(raw: dict[str, Any], *, fallback_handle: str) -> Tweet:
        tid = str(raw["id"])
        url = str(raw.get("url") or f"https://x.com/{fallback_handle}/status/{tid}")
        text = str(raw.get("text") or "")
        created_at = str(raw["createdAt"])
        # twitterapi.io uses ISO8601 with trailing 'Z' for UTC.
        posted_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        author = raw.get("author") or {}
        handle = str(author.get("userName") or fallback_handle).lstrip("@")
        return Tweet(
            handle=handle,
            tweet_id=tid,
            url=url,
            text=text,
            posted_at=posted_at,
            reply_count=int(raw.get("replyCount") or 0),
            like_count=int(raw.get("likeCount") or 0),
            retweet_count=int(raw.get("retweetCount") or 0),
        )
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run pytest tests/intelligence/test_twitterapi_io_source.py -v`
Expected: all five tests PASS.

- [ ] **Step 5: Create the optional live smoke test (skipped without env var)**

Create `tests/intelligence/test_twitterapi_io_live.py`:

```python
"""Live smoke test for TwitterApiIoSource. Gated on TWITTERAPI_IO_API_KEY;
skipped entirely when the env var is missing. Matches the 6b
test_google_calendar_live.py pattern. Do NOT run in CI by default."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from project0.intelligence.twitterapi_io import TwitterApiIoSource

API_KEY = os.environ.get("TWITTERAPI_IO_API_KEY")

pytestmark = pytest.mark.skipif(
    not API_KEY, reason="TWITTERAPI_IO_API_KEY not set — skipping live smoke test"
)


@pytest.mark.asyncio
async def test_live_fetch_user_timeline_returns_tweets():
    src = TwitterApiIoSource(api_key=API_KEY or "")
    try:
        tweets = await src.fetch_user_timeline(
            "sama",
            since=datetime.now(UTC) - timedelta(days=7),
            max_results=5,
        )
    finally:
        await src.aclose()
    assert len(tweets) > 0, "expected at least one tweet in the last 7 days"
    t = tweets[0]
    assert t.tweet_id
    assert t.url.startswith("https://")
    assert t.posted_at.tzinfo is not None
```

- [ ] **Step 6: Run the live test locally to confirm it's wired correctly (optional)**

Run (will skip without the env var): `uv run pytest tests/intelligence/test_twitterapi_io_live.py -v`
Expected: one SKIPPED test, or PASS if `TWITTERAPI_IO_API_KEY` is set in your shell.

- [ ] **Step 7: Commit**

```bash
git add src/project0/intelligence/twitterapi_io.py tests/intelligence/test_twitterapi_io_source.py tests/intelligence/test_twitterapi_io_live.py
git commit -m "feat(intelligence): TwitterApiIoSource concrete client

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Watchlist loader

**Goal:** Parse the `[[watch]]` array from `prompts/intelligence.toml` into a list of `WatchEntry` dataclasses. Mirrors `load_pulse_entries` shape from 6c.

**Files:**
- Create: `src/project0/intelligence/watchlist.py`
- Test: `tests/intelligence/test_watchlist_loader.py`

- [ ] **Step 1: Write the failing test**

Create `tests/intelligence/test_watchlist_loader.py`:

```python
"""Watchlist loader tests. The loader reads the [[watch]] array from a
TOML file and produces a list of frozen WatchEntry records. Malformed
entries raise RuntimeError naming the file and field so the failure
message is directly actionable."""
from __future__ import annotations

from pathlib import Path

import pytest

from project0.intelligence.watchlist import WatchEntry, load_watchlist


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "intelligence.toml"
    p.write_text(content, encoding="utf-8")
    return p


def test_valid_toml_produces_watch_entries(tmp_path: Path):
    p = _write(tmp_path, """
[llm.summarizer]
model = "claude-opus-4-6"

[[watch]]
handle = "openai"
tags = ["ai-labs", "first-party"]
notes = "OpenAI official"

[[watch]]
handle = "sama"
tags = ["executive"]

[[watch]]
handle = "@anthropicai"
""")
    entries = load_watchlist(p)
    assert len(entries) == 3
    assert entries[0] == WatchEntry(handle="openai", tags=("ai-labs", "first-party"), notes="OpenAI official")
    assert entries[1] == WatchEntry(handle="sama", tags=("executive",), notes="")
    # Leading @ is stripped, handle is lowercased.
    assert entries[2] == WatchEntry(handle="anthropicai", tags=(), notes="")


def test_missing_watch_array_returns_empty_list(tmp_path: Path):
    p = _write(tmp_path, """
[llm.summarizer]
model = "claude-opus-4-6"
""")
    assert load_watchlist(p) == []


def test_missing_handle_raises_runtime_error(tmp_path: Path):
    p = _write(tmp_path, """
[[watch]]
tags = ["orphan"]
""")
    with pytest.raises(RuntimeError, match="handle"):
        load_watchlist(p)


def test_duplicate_handle_raises_runtime_error(tmp_path: Path):
    p = _write(tmp_path, """
[[watch]]
handle = "sama"

[[watch]]
handle = "SAMA"
""")
    with pytest.raises(RuntimeError, match="duplicate"):
        load_watchlist(p)


def test_empty_handle_raises_runtime_error(tmp_path: Path):
    p = _write(tmp_path, """
[[watch]]
handle = ""
""")
    with pytest.raises(RuntimeError, match="handle"):
        load_watchlist(p)
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/intelligence/test_watchlist_loader.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_watchlist'`.

- [ ] **Step 3: Create `watchlist.py`**

```python
"""Watchlist loader. Reads the [[watch]] array from an intelligence TOML
file and returns a list of frozen WatchEntry records.

Static for 6d. Mutable dynamic-follow tooling lives in 6h."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WatchEntry:
    handle: str
    tags: tuple[str, ...]
    notes: str


def load_watchlist(path: Path) -> list[WatchEntry]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw_entries = data.get("watch") or []
    if not isinstance(raw_entries, list):
        raise RuntimeError(f"{path}: [[watch]] must be an array of tables")

    seen: set[str] = set()
    out: list[WatchEntry] = []
    for i, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise RuntimeError(f"{path}: [[watch]] entry {i} is not a table")
        handle_raw = raw.get("handle")
        if not isinstance(handle_raw, str) or not handle_raw.strip():
            raise RuntimeError(
                f"{path}: [[watch]] entry {i}: missing or empty 'handle'"
            )
        handle = handle_raw.strip().lstrip("@").lower()
        if handle in seen:
            raise RuntimeError(f"{path}: duplicate handle {handle!r}")
        seen.add(handle)

        tags_raw: Any = raw.get("tags") or []
        if not isinstance(tags_raw, list) or not all(isinstance(t, str) for t in tags_raw):
            raise RuntimeError(
                f"{path}: [[watch]] entry {i}: 'tags' must be a list of strings"
            )

        notes_raw = raw.get("notes") or ""
        if not isinstance(notes_raw, str):
            raise RuntimeError(
                f"{path}: [[watch]] entry {i}: 'notes' must be a string"
            )

        out.append(WatchEntry(handle=handle, tags=tuple(tags_raw), notes=notes_raw))

    return out
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run pytest tests/intelligence/test_watchlist_loader.py -v`
Expected: all five tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/project0/intelligence/watchlist.py tests/intelligence/test_watchlist_loader.py
git commit -m "feat(intelligence): watchlist TOML loader

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Report schema, validator, and atomic storage

**Goal:** Everything needed to persist a `DailyReport` JSON file on disk. One module holds: `parse_json_strict` (with markdown-fence tolerance), `validate_report_dict` (schema hard rules), `atomic_write_json` (tmp+rename), `read_report`, `list_report_dates`.

**Files:**
- Create: `src/project0/intelligence/report.py`
- Test: `tests/intelligence/test_report_schema.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/intelligence/test_report_schema.py`:

```python
"""Report schema + storage tests. Covers parse_json_strict (with code-fence
tolerance), every hard rule from §5.3 of the spec, atomic write safety,
round-trip read, and list_report_dates filename filtering."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from project0.intelligence.report import (
    atomic_write_json,
    list_report_dates,
    parse_json_strict,
    read_report,
    validate_report_dict,
)


def _valid_report() -> dict[str, Any]:
    return {
        "date": "2026-04-15",
        "generated_at": "2026-04-15T08:00:00+08:00",
        "user_tz": "Asia/Shanghai",
        "watchlist_snapshot": ["sama"],
        "news_items": [
            {
                "id": "n1",
                "headline": "h1",
                "summary": "s1",
                "importance": "high",
                "importance_reason": "r1",
                "topics": ["ai"],
                "source_tweets": [
                    {
                        "handle": "sama",
                        "url": "https://x.com/sama/status/1",
                        "text": "hi",
                        "posted_at": "2026-04-15T03:00:00Z",
                    }
                ],
            }
        ],
        "suggested_accounts": [
            {"handle": "researcher1", "reason": "cited by sama", "seen_in_items": ["n1"]}
        ],
        "stats": {
            "tweets_fetched": 10,
            "handles_attempted": 5,
            "handles_succeeded": 5,
            "items_generated": 1,
            "errors": [],
        },
    }


# --- parse_json_strict ------------------------------------------------------

def test_parse_json_strict_plain_json():
    assert parse_json_strict('{"a": 1}') == {"a": 1}


def test_parse_json_strict_with_markdown_fence():
    text = "```json\n{\"a\": 1}\n```"
    assert parse_json_strict(text) == {"a": 1}


def test_parse_json_strict_with_leading_whitespace():
    assert parse_json_strict("\n   {\"a\": 1}   \n") == {"a": 1}


def test_parse_json_strict_rejects_nonjson():
    with pytest.raises(ValueError, match="JSON"):
        parse_json_strict("not json at all")


def test_parse_json_strict_rejects_nondict_top_level():
    with pytest.raises(ValueError, match="top-level"):
        parse_json_strict("[1, 2, 3]")


# --- validate_report_dict ---------------------------------------------------

def test_valid_report_passes_validation():
    validate_report_dict(_valid_report())  # no exception


def test_missing_top_level_key_raises():
    r = _valid_report()
    del r["news_items"]
    with pytest.raises(ValueError, match="news_items"):
        validate_report_dict(r)


def test_bad_date_format_raises():
    r = _valid_report()
    r["date"] = "Apr 15, 2026"
    with pytest.raises(ValueError, match="date"):
        validate_report_dict(r)


def test_invalid_importance_raises():
    r = _valid_report()
    r["news_items"][0]["importance"] = "critical"
    with pytest.raises(ValueError, match="importance"):
        validate_report_dict(r)


def test_empty_source_tweets_raises():
    r = _valid_report()
    r["news_items"][0]["source_tweets"] = []
    with pytest.raises(ValueError, match="source_tweets"):
        validate_report_dict(r)


def test_duplicate_news_item_id_raises():
    r = _valid_report()
    r["news_items"].append({**r["news_items"][0], "id": "n1"})
    with pytest.raises(ValueError, match="duplicate"):
        validate_report_dict(r)


def test_dangling_seen_in_items_raises():
    r = _valid_report()
    r["suggested_accounts"][0]["seen_in_items"] = ["n99"]
    with pytest.raises(ValueError, match="seen_in_items"):
        validate_report_dict(r)


def test_handles_succeeded_gt_attempted_raises():
    r = _valid_report()
    r["stats"]["handles_succeeded"] = 10
    r["stats"]["handles_attempted"] = 5
    with pytest.raises(ValueError, match="handles_succeeded"):
        validate_report_dict(r)


# --- atomic_write_json + read_report ---------------------------------------

def test_atomic_write_and_read_round_trip(tmp_path: Path):
    p = tmp_path / "2026-04-15.json"
    atomic_write_json(p, _valid_report())
    assert p.exists()
    loaded = read_report(p)
    assert loaded["date"] == "2026-04-15"
    # tmp file should not exist.
    assert not (tmp_path / "2026-04-15.json.tmp").exists()


def test_atomic_write_overwrites_existing(tmp_path: Path):
    p = tmp_path / "2026-04-15.json"
    atomic_write_json(p, {"old": "data"})
    atomic_write_json(p, _valid_report())
    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded["date"] == "2026-04-15"
    assert "old" not in loaded


def test_read_report_validates(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"not": "a report"}), encoding="utf-8")
    with pytest.raises(ValueError):
        read_report(p)


# --- list_report_dates -----------------------------------------------------

def test_list_report_dates_sorted_descending(tmp_path: Path):
    for d in ["2026-04-13", "2026-04-15", "2026-04-14"]:
        (tmp_path / f"{d}.json").write_text("{}", encoding="utf-8")
    dates = list_report_dates(tmp_path)
    assert dates == [date(2026, 4, 15), date(2026, 4, 14), date(2026, 4, 13)]


def test_list_report_dates_ignores_non_matching_files(tmp_path: Path):
    (tmp_path / "2026-04-15.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "backup.json.bak").write_text("x", encoding="utf-8")
    (tmp_path / "2026-04-15.json.tmp").write_text("x", encoding="utf-8")
    dates = list_report_dates(tmp_path)
    assert dates == [date(2026, 4, 15)]


def test_list_report_dates_empty_when_dir_missing(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    assert list_report_dates(missing) == []
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/intelligence/test_report_schema.py -v`
Expected: FAIL with `ImportError: cannot import name 'validate_report_dict'`.

- [ ] **Step 3: Create `report.py`**

```python
"""DailyReport schema, validator, and filesystem helpers.

Reports live as flat JSON files at ``data/intelligence/reports/YYYY-MM-DD.json``.
One file per day. Hand-written validator (no Pydantic — not a project
dependency and not worth adding for one file).

Hard rules enforced by ``validate_report_dict`` match §5.3 of the spec."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
_VALID_IMPORTANCE = {"high", "medium", "low"}

_REQUIRED_TOP_LEVEL = {
    "date",
    "generated_at",
    "user_tz",
    "watchlist_snapshot",
    "news_items",
    "suggested_accounts",
    "stats",
}


def parse_json_strict(text: str) -> dict[str, Any]:
    """Parse JSON from LLM output. Tolerates a surrounding markdown code
    fence and leading/trailing whitespace but nothing else. Rejects
    anything whose top level is not a JSON object."""
    s = text.strip()
    # Strip an optional ```json ... ``` or ``` ... ``` fence.
    if s.startswith("```"):
        # Drop the opening fence line.
        first_nl = s.find("\n")
        if first_nl == -1:
            raise ValueError("JSON code fence has no body")
        s = s[first_nl + 1 :]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[: -3].rstrip()
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError(f"JSON top-level must be an object, got {type(parsed).__name__}")
    return parsed


def validate_report_dict(d: dict[str, Any]) -> None:
    """Raise ValueError with a path-qualified message on any hard-rule
    violation. See §5.3 of the spec for the full rule list."""
    missing = _REQUIRED_TOP_LEVEL - set(d.keys())
    if missing:
        raise ValueError(f"report missing keys: {sorted(missing)}")

    if not isinstance(d["date"], str) or not _DATE_RE.match(d["date"]):
        raise ValueError(f"report.date must be YYYY-MM-DD, got {d['date']!r}")

    news_items = d["news_items"]
    if not isinstance(news_items, list):
        raise ValueError("report.news_items must be a list")

    seen_ids: set[str] = set()
    for i, item in enumerate(news_items):
        if not isinstance(item, dict):
            raise ValueError(f"report.news_items[{i}] must be an object")
        for req in ("id", "headline", "summary", "importance", "source_tweets"):
            if req not in item:
                raise ValueError(f"report.news_items[{i}] missing key: {req}")
        if item["importance"] not in _VALID_IMPORTANCE:
            raise ValueError(
                f"report.news_items[{i}].importance must be one of "
                f"{sorted(_VALID_IMPORTANCE)}, got {item['importance']!r}"
            )
        if item["id"] in seen_ids:
            raise ValueError(f"report.news_items: duplicate id {item['id']!r}")
        seen_ids.add(item["id"])
        srcs = item["source_tweets"]
        if not isinstance(srcs, list) or len(srcs) == 0:
            raise ValueError(
                f"report.news_items[{i}].source_tweets must be a non-empty list"
            )

    suggested = d["suggested_accounts"]
    if not isinstance(suggested, list):
        raise ValueError("report.suggested_accounts must be a list")
    for i, acc in enumerate(suggested):
        if not isinstance(acc, dict):
            raise ValueError(f"report.suggested_accounts[{i}] must be an object")
        seen_in = acc.get("seen_in_items") or []
        if not isinstance(seen_in, list):
            raise ValueError(
                f"report.suggested_accounts[{i}].seen_in_items must be a list"
            )
        for ref in seen_in:
            if ref not in seen_ids:
                raise ValueError(
                    f"report.suggested_accounts[{i}].seen_in_items "
                    f"references unknown news_item id {ref!r}"
                )

    stats = d["stats"]
    if not isinstance(stats, dict):
        raise ValueError("report.stats must be an object")
    attempted = int(stats.get("handles_attempted", 0))
    succeeded = int(stats.get("handles_succeeded", 0))
    if succeeded > attempted:
        raise ValueError(
            f"report.stats.handles_succeeded ({succeeded}) > "
            f"handles_attempted ({attempted})"
        )


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` to ``path`` atomically via tmp+fsync+rename. Ensures
    no partial file is left behind if the process crashes mid-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_report(path: Path) -> dict[str, Any]:
    """Load a report file, parse, validate, return the dict."""
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level is not an object")
    validate_report_dict(data)
    return data


def list_report_dates(reports_dir: Path) -> list[date]:
    """Return all YYYY-MM-DD.json filenames in ``reports_dir`` as a sorted
    descending list of ``date`` objects. Non-matching filenames (.tmp,
    .bak, notes.txt) are ignored. A missing directory returns []."""
    if not reports_dir.exists() or not reports_dir.is_dir():
        return []
    dates: list[date] = []
    for entry in reports_dir.iterdir():
        if not entry.is_file():
            continue
        m = _FILENAME_RE.match(entry.name)
        if not m:
            continue
        try:
            dates.append(datetime.strptime(m.group(1), "%Y-%m-%d").date())
        except ValueError:
            continue
    dates.sort(reverse=True)
    return dates
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run pytest tests/intelligence/test_report_schema.py -v`
Expected: all 18 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/project0/intelligence/report.py tests/intelligence/test_report_schema.py
git commit -m "feat(intelligence): DailyReport schema + atomic storage

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Summarizer + Q&A prompt builders

**Goal:** One module holds `SUMMARIZER_SYSTEM_PROMPT` (stable, ~1500 tokens, cached) plus three user-prompt builders: one for generation (`build_user_prompt`), one for chat-turn Q&A (`build_qa_user_prompt`), one for delegated turns (`build_delegated_user_prompt`).

**Files:**
- Create: `src/project0/intelligence/summarizer_prompt.py`
- Test: `tests/intelligence/test_summarizer_prompt_build.py`

- [ ] **Step 1: Write the failing test**

Create `tests/intelligence/test_summarizer_prompt_build.py`:

```python
"""Summarizer + Q&A prompt builder tests. Verifies tweet grouping,
ordering, error rendering, and the date-staleness hints the Q&A prompt
injects so the model knows whether 'today's report' is actually from
today."""
from __future__ import annotations

from datetime import UTC, date, datetime

from project0.envelope import Envelope
from project0.intelligence.source import Tweet
from project0.intelligence.summarizer_prompt import (
    SUMMARIZER_SYSTEM_PROMPT,
    build_delegated_user_prompt,
    build_qa_user_prompt,
    build_user_prompt,
)


def _t(handle: str, tid: str, posted_at: datetime, text: str = "body") -> Tweet:
    return Tweet(
        handle=handle,
        tweet_id=tid,
        url=f"https://x.com/{handle}/status/{tid}",
        text=text,
        posted_at=posted_at,
        reply_count=0,
        like_count=0,
        retweet_count=0,
    )


def test_summarizer_system_prompt_mentions_json_and_schema():
    assert "JSON" in SUMMARIZER_SYSTEM_PROMPT
    assert "news_items" in SUMMARIZER_SYSTEM_PROMPT
    assert "importance" in SUMMARIZER_SYSTEM_PROMPT
    assert "high" in SUMMARIZER_SYSTEM_PROMPT
    assert "suggested_accounts" in SUMMARIZER_SYSTEM_PROMPT


def test_build_user_prompt_groups_by_handle_newest_first():
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    tweets = [
        _t("sama", "s-old", now.replace(hour=1), "old"),
        _t("openai", "o1", now.replace(hour=5), "ship it"),
        _t("sama", "s-new", now.replace(hour=10), "new"),
    ]
    out = build_user_prompt(
        raw_tweets=tweets,
        watchlist_snapshot=["openai", "sama", "anthropicai"],
        errors=[],
        today_local=date(2026, 4, 15),
        user_tz_name="Asia/Shanghai",
    )
    assert "2026-04-15" in out
    assert "Asia/Shanghai" in out
    assert "@openai" in out
    assert "@sama" in out
    # Newest within a handle comes first.
    sama_block_start = out.index("@sama")
    assert out.index("s-new", sama_block_start) < out.index("s-old", sama_block_start)


def test_build_user_prompt_omits_handles_with_no_tweets():
    out = build_user_prompt(
        raw_tweets=[_t("sama", "s1", datetime(2026, 4, 15, 10, tzinfo=UTC))],
        watchlist_snapshot=["sama", "ghost"],
        errors=[],
        today_local=date(2026, 4, 15),
        user_tz_name="UTC",
    )
    assert "@sama" in out
    assert "@ghost" not in out


def test_build_user_prompt_renders_errors():
    out = build_user_prompt(
        raw_tweets=[_t("sama", "s1", datetime(2026, 4, 15, 10, tzinfo=UTC))],
        watchlist_snapshot=["sama", "flaky"],
        errors=[{"handle": "flaky", "error": "HTTP 404"}],
        today_local=date(2026, 4, 15),
        user_tz_name="UTC",
    )
    assert "flaky" in out
    assert "HTTP 404" in out


def test_build_qa_user_prompt_injects_latest_report_and_date_hints():
    latest = {"date": "2026-04-15", "news_items": [{"id": "n1", "headline": "h"}]}
    out = build_qa_user_prompt(
        latest_report=latest,
        current_date_local=date(2026, 4, 15),
        recent_messages=[],
        current_user_message="今天有什么 AI 消息？",
    )
    assert "2026-04-15" in out
    assert "n1" in out
    assert "今天有什么 AI 消息？" in out


def test_build_qa_user_prompt_flags_stale_report():
    latest = {"date": "2026-04-10", "news_items": []}
    out = build_qa_user_prompt(
        latest_report=latest,
        current_date_local=date(2026, 4, 15),
        recent_messages=[],
        current_user_message="news?",
    )
    assert "2026-04-10" in out
    assert "2026-04-15" in out


def test_build_qa_user_prompt_with_no_report():
    out = build_qa_user_prompt(
        latest_report=None,
        current_date_local=date(2026, 4, 15),
        recent_messages=[],
        current_user_message="news?",
    )
    assert "没有" in out or "no report" in out.lower()


def test_build_delegated_user_prompt_includes_query():
    out = build_delegated_user_prompt(
        latest_report={"date": "2026-04-15", "news_items": []},
        current_date_local=date(2026, 4, 15),
        query="帮我查一下 o5 发布的情况",
    )
    assert "o5" in out
    assert "2026-04-15" in out
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/intelligence/test_summarizer_prompt_build.py -v`
Expected: FAIL with `ImportError: cannot import name 'SUMMARIZER_SYSTEM_PROMPT'`.

- [ ] **Step 3: Create `summarizer_prompt.py`**

```python
"""Prompt strings + builders for Intelligence.

Three user-prompt builders share this file because they all share the
same shape and helpers:
  - ``build_user_prompt``: feeds raw tweets into the Opus summarizer
  - ``build_qa_user_prompt``: feeds the latest report into the Sonnet
    Q&A loop
  - ``build_delegated_user_prompt``: feeds a Manager-delegated query
    into the Sonnet Q&A loop

The system prompt is stable and is cached on the Anthropic system block
(see llm/provider.py). The user prompts change every call."""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Any

from project0.envelope import Envelope
from project0.intelligence.source import Tweet


SUMMARIZER_SYSTEM_PROMPT = """You are the Intelligence agent's daily-report summarizer for a Project 0
multi-agent personal assistant. You take a batch of raw tweets from a
watchlist and produce a structured daily report in JSON.

## Your job
1. Cluster tweets by topic. Multiple accounts covering the same story
   become ONE news_item.
2. Rank items by importance to a technically sophisticated user who cares
   about AI, ML, infrastructure, and tech industry news.
3. Write concise simplified-Chinese summaries (2–4 sentences each).
4. Flag accounts referenced in the tweets that are not already on the
   watchlist but look worth following (suggested_accounts).
5. Fill out the stats block using the numbers in the user message.

## Importance rubric
- high:   major model releases, significant industry moves, named hardware
          launches, safety/regulatory events affecting the field, or
          anything the user would regret missing.
- medium: notable technical posts, thoughtful analyses, mid-tier company
          news.
- low:    routine updates, personal takes, minor announcements. Include
          only if the tweet volume on the topic justifies it.

## Source trust heuristics
- First-party announcements (company official accounts) > researchers >
  commentary.
- If a claim appears in only one tweet from an unverified account, mark
  the news_item's summary with "(未经证实)".
- Prefer citing the original source tweet over reposts.

## Hard output rules
- Output ONLY a single JSON object matching the schema below. No prose,
  no markdown code fences, no preamble.
- Every news_item must cite at least one source tweet by URL.
- Chinese summaries only. Keep tweet ``text`` fields in their original
  language.
- If no tweets warrant a news_item, return an empty ``news_items`` array.
  Do NOT invent content.
- ``suggested_accounts`` may be empty. Quality over quantity.

## Schema
{
  "date": "YYYY-MM-DD",                    // fill with the date the user message gives you
  "generated_at": "",                      // leave empty — Python fills this in after your response
  "user_tz": "",                           // leave empty — Python fills this in
  "watchlist_snapshot": [],                // leave empty — Python fills this in
  "news_items": [
    {
      "id": "n1",                          // unique within this report: n1, n2, n3...
      "headline": "",                      // short Chinese headline
      "summary": "",                       // 2-4 Chinese sentences
      "importance": "high" | "medium" | "low",
      "importance_reason": "",             // why this matters, Chinese
      "topics": ["lowercase-hyphenated"],
      "source_tweets": [
        {
          "handle": "",                    // no @
          "url": "",                       // full URL
          "text": "",                      // original language
          "posted_at": ""                  // ISO8601
        }
      ]
    }
  ],
  "suggested_accounts": [
    {
      "handle": "",                        // no @
      "reason": "",                        // Chinese
      "seen_in_items": ["n1"]              // must reference existing news_items ids
    }
  ],
  "stats": {
    "tweets_fetched": 0,                   // leave 0 — Python fills these
    "handles_attempted": 0,
    "handles_succeeded": 0,
    "items_generated": 0,
    "errors": []
  }
}

## Example
Input tweets:
  @sama: "gm, o5-mini is live, 40% lower latency"
  @openai: "Introducing o5-mini: faster reasoning for your apps"
  @some_researcher: "o5-mini's routing trick is neat, explained here..."

Output:
{
  "date": "2026-04-15",
  "generated_at": "",
  "user_tz": "",
  "watchlist_snapshot": [],
  "news_items": [
    {
      "id": "n1",
      "headline": "OpenAI 发布 o5-mini，推理延迟降低 40%",
      "summary": "OpenAI 宣布 o5-mini 正式上线，比前代模型推理延迟降低约 40%。Sam Altman 同步确认发布。",
      "importance": "high",
      "importance_reason": "主流模型迭代，直接影响用户在用的 API",
      "topics": ["ai-models", "openai", "inference"],
      "source_tweets": [
        {"handle": "openai", "url": "https://x.com/openai/status/1", "text": "Introducing o5-mini: faster reasoning for your apps", "posted_at": "2026-04-15T03:00:00Z"},
        {"handle": "sama",   "url": "https://x.com/sama/status/2",   "text": "gm, o5-mini is live, 40% lower latency",                "posted_at": "2026-04-15T03:05:00Z"}
      ]
    }
  ],
  "suggested_accounts": [
    {"handle": "some_researcher", "reason": "在 n1 中对 o5-mini 的路由机制做了解读", "seen_in_items": ["n1"]}
  ],
  "stats": {"tweets_fetched": 0, "handles_attempted": 0, "handles_succeeded": 0, "items_generated": 0, "errors": []}
}
"""


def build_user_prompt(
    *,
    raw_tweets: Sequence[Tweet],
    watchlist_snapshot: Sequence[str],
    errors: Sequence[dict[str, Any]],
    today_local: date,
    user_tz_name: str,
) -> str:
    """Render the user-message payload for the summarizer call. Tweets are
    grouped by handle, newest first. Handles with no tweets are omitted."""
    by_handle: dict[str, list[Tweet]] = defaultdict(list)
    for t in raw_tweets:
        by_handle[t.handle.lstrip("@").lower()].append(t)
    for handle in by_handle:
        by_handle[handle].sort(key=lambda x: x.posted_at, reverse=True)

    lines: list[str] = []
    lines.append(
        f"Today is {today_local.isoformat()} ({user_tz_name}). "
        f"Generate the daily report for this date."
    )
    lines.append("")
    lines.append(
        f"Watchlist snapshot ({len(watchlist_snapshot)} handles): "
        + ", ".join(watchlist_snapshot)
    )
    lines.append(f"Handles attempted: {len(watchlist_snapshot)}")
    lines.append(
        f"Handles succeeded: {len(watchlist_snapshot) - len(errors)}"
    )
    if errors:
        lines.append(
            "Handles failed: "
            + json.dumps(list(errors), ensure_ascii=False)
        )
    lines.append(f"Tweets fetched: {len(raw_tweets)}")
    lines.append("")
    lines.append("Raw tweets follow, grouped by handle, newest first:")
    lines.append("")

    # Stable handle ordering: by watchlist_snapshot order when possible,
    # falling back to alphabetical for any handle not in the snapshot.
    ordered_handles = [h for h in watchlist_snapshot if h in by_handle]
    extras = sorted(h for h in by_handle if h not in set(watchlist_snapshot))
    for h in ordered_handles + extras:
        tweets = by_handle[h]
        lines.append(f"=== @{h} ===")
        for t in tweets:
            lines.append(f"[{t.posted_at.isoformat()}] url={t.url}")
            lines.append(t.text)
            lines.append("")
    return "\n".join(lines)


def build_qa_user_prompt(
    *,
    latest_report: dict[str, Any] | None,
    current_date_local: date,
    recent_messages: Sequence[Envelope],
    current_user_message: str,
) -> str:
    """Build the initial user message for an Intelligence chat turn.
    Eagerly embeds the latest report so the model doesn't need a tool
    call to see it. Flags staleness when the report date != today."""
    lines: list[str] = []
    lines.append(f"Today is {current_date_local.isoformat()}.")
    lines.append("")
    if latest_report is None:
        lines.append(
            "当前没有任何日报文件。如果用户在问新闻内容，请告诉他先让你生成一份"
            "（调用 generate_daily_report 工具）。(no report available)"
        )
    else:
        report_date = latest_report.get("date", "unknown")
        lines.append(f"最新日报日期：{report_date}")
        if report_date != current_date_local.isoformat():
            lines.append(
                f"注意：最新日报是 {report_date} 的，不是今天 "
                f"({current_date_local.isoformat()})。回答用户前请明确提到日期，"
                f"避免把旧闻当成今天的事。"
            )
        lines.append("")
        lines.append("最新日报 JSON（用它回答用户关于「今天」/「最近」的问题）：")
        lines.append(json.dumps(latest_report, ensure_ascii=False, indent=2))

    if recent_messages:
        lines.append("")
        lines.append("最近对话记录：")
        for e in recent_messages:
            who = e.from_agent or e.from_kind
            lines.append(f"  {who}: {e.body}")

    lines.append("")
    lines.append(f"用户刚发的消息：{current_user_message}")
    return "\n".join(lines)


def build_delegated_user_prompt(
    *,
    latest_report: dict[str, Any] | None,
    current_date_local: date,
    query: str,
) -> str:
    """Build the initial user message for a Manager-delegated turn. No
    transcript — the query is assumed self-contained."""
    lines: list[str] = []
    lines.append(f"Today is {current_date_local.isoformat()}.")
    lines.append("")
    lines.append("经理把一个查询转给了你。请基于最新日报作答，"
                 "如果日报覆盖不到或过期，直接说清楚，不要编造。")
    lines.append("")
    if latest_report is None:
        lines.append("当前没有任何日报文件。(no report available)")
    else:
        report_date = latest_report.get("date", "unknown")
        lines.append(f"最新日报日期：{report_date}")
        if report_date != current_date_local.isoformat():
            lines.append(
                f"（最新日报是 {report_date}，今天是 "
                f"{current_date_local.isoformat()}，请在回答中体现日期）"
            )
        lines.append("")
        lines.append("最新日报 JSON：")
        lines.append(json.dumps(latest_report, ensure_ascii=False, indent=2))
    lines.append("")
    lines.append(f"查询：{query}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run pytest tests/intelligence/test_summarizer_prompt_build.py -v`
Expected: all eight tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/project0/intelligence/summarizer_prompt.py tests/intelligence/test_summarizer_prompt_build.py
git commit -m "feat(intelligence): summarizer system prompt + user prompt builders

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Deterministic generation pipeline

**Goal:** The `generate_daily_report` function. Fetches each watchlist handle, runs one Opus summarization call, validates the JSON, atomically writes the file. All failure paths from §6.2 of the spec are exercised.

**Files:**
- Create: `src/project0/intelligence/generate.py`
- Test: `tests/intelligence/test_generate_pipeline.py`

- [ ] **Step 1: Write the failing test**

Create `tests/intelligence/test_generate_pipeline.py`:

```python
"""generate_daily_report pipeline tests. Uses FakeTwitterSource and
FakeProvider to exercise every branch from §6.2 of the spec:
  - happy path
  - partial handle failure (some handles 404, report still written)
  - total handle failure (TwitterSourceError, no report written)
  - malformed LLM JSON (ValueError, no report written)
  - schema-invalid LLM JSON (ValueError, no report written)
  - default date defaults to today in user_tz
  - regeneration overwrites atomically
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.intelligence.fake_source import FakeTwitterSource
from project0.intelligence.generate import generate_daily_report
from project0.intelligence.source import Tweet, TwitterSourceError
from project0.intelligence.watchlist import WatchEntry
from project0.llm.provider import FakeProvider


def _tweet(handle: str, tid: str, hours_ago: int) -> Tweet:
    return Tweet(
        handle=handle,
        tweet_id=tid,
        url=f"https://x.com/{handle}/status/{tid}",
        text=f"tweet {tid} from {handle}",
        posted_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        reply_count=0,
        like_count=0,
        retweet_count=0,
    )


def _valid_llm_json() -> str:
    return json.dumps({
        "date": "2026-04-15",
        "generated_at": "",
        "user_tz": "",
        "watchlist_snapshot": [],
        "news_items": [
            {
                "id": "n1",
                "headline": "头条",
                "summary": "摘要",
                "importance": "high",
                "importance_reason": "原因",
                "topics": ["ai"],
                "source_tweets": [
                    {
                        "handle": "sama",
                        "url": "https://x.com/sama/status/1",
                        "text": "tweet 1 from sama",
                        "posted_at": "2026-04-15T03:00:00Z",
                    }
                ],
            }
        ],
        "suggested_accounts": [],
        "stats": {
            "tweets_fetched": 0,
            "handles_attempted": 0,
            "handles_succeeded": 0,
            "items_generated": 0,
            "errors": [],
        },
    })


@pytest.fixture
def tz() -> ZoneInfo:
    return ZoneInfo("Asia/Shanghai")


@pytest.mark.asyncio
async def test_happy_path_writes_valid_report(tmp_path: Path, tz: ZoneInfo):
    src = FakeTwitterSource(timelines={
        "sama": [_tweet("sama", "1", 2)],
        "openai": [_tweet("openai", "1", 3)],
        "anthropicai": [_tweet("anthropicai", "1", 4)],
    })
    llm = FakeProvider(responses=[_valid_llm_json()])
    watchlist = [
        WatchEntry(handle="sama", tags=(), notes=""),
        WatchEntry(handle="openai", tags=(), notes=""),
        WatchEntry(handle="anthropicai", tags=(), notes=""),
    ]
    target_date = date(2026, 4, 15)

    report = await generate_daily_report(
        date=target_date,
        source=src,
        llm=llm,
        summarizer_model="claude-opus-4-6",
        summarizer_max_tokens=16384,
        watchlist=watchlist,
        reports_dir=tmp_path,
        user_tz=tz,
        timeline_since_hours=24,
        max_tweets_per_handle=50,
    )

    assert report["stats"]["tweets_fetched"] == 3
    assert report["stats"]["handles_attempted"] == 3
    assert report["stats"]["handles_succeeded"] == 3
    assert report["stats"]["errors"] == []
    assert report["watchlist_snapshot"] == ["sama", "openai", "anthropicai"]
    assert report["user_tz"] == "Asia/Shanghai"
    assert "generated_at" in report and report["generated_at"]

    # File exists on disk.
    out_path = tmp_path / "2026-04-15.json"
    assert out_path.exists()
    on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    assert on_disk["news_items"][0]["id"] == "n1"


@pytest.mark.asyncio
async def test_partial_failure_records_errors_and_still_writes(tmp_path: Path, tz: ZoneInfo):
    src = FakeTwitterSource(timelines={
        "sama": [_tweet("sama", "1", 2)],
        "openai": [_tweet("openai", "1", 3)],
        # anthropicai missing → raises TwitterSourceError
    })
    llm = FakeProvider(responses=[_valid_llm_json()])
    watchlist = [
        WatchEntry(handle="sama", tags=(), notes=""),
        WatchEntry(handle="openai", tags=(), notes=""),
        WatchEntry(handle="anthropicai", tags=(), notes=""),
    ]

    report = await generate_daily_report(
        date=date(2026, 4, 15),
        source=src,
        llm=llm,
        summarizer_model="claude-opus-4-6",
        summarizer_max_tokens=16384,
        watchlist=watchlist,
        reports_dir=tmp_path,
        user_tz=tz,
        timeline_since_hours=24,
        max_tweets_per_handle=50,
    )

    assert report["stats"]["handles_succeeded"] == 2
    assert report["stats"]["handles_attempted"] == 3
    assert len(report["stats"]["errors"]) == 1
    assert report["stats"]["errors"][0]["handle"] == "anthropicai"
    assert (tmp_path / "2026-04-15.json").exists()


@pytest.mark.asyncio
async def test_total_failure_raises_and_writes_nothing(tmp_path: Path, tz: ZoneInfo):
    src = FakeTwitterSource(timelines={})  # no handles seeded
    llm = FakeProvider(responses=[_valid_llm_json()])
    watchlist = [
        WatchEntry(handle="sama", tags=(), notes=""),
        WatchEntry(handle="openai", tags=(), notes=""),
    ]

    with pytest.raises(TwitterSourceError, match="all"):
        await generate_daily_report(
            date=date(2026, 4, 15),
            source=src,
            llm=llm,
            summarizer_model="claude-opus-4-6",
            summarizer_max_tokens=16384,
            watchlist=watchlist,
            reports_dir=tmp_path,
            user_tz=tz,
            timeline_since_hours=24,
            max_tweets_per_handle=50,
        )
    # No file written.
    assert list(tmp_path.iterdir()) == []
    # LLM never called — summarization is skipped on total failure.
    assert llm.calls == []


@pytest.mark.asyncio
async def test_malformed_llm_json_raises_value_error(tmp_path: Path, tz: ZoneInfo):
    src = FakeTwitterSource(timelines={"sama": [_tweet("sama", "1", 2)]})
    llm = FakeProvider(responses=["not json at all"])
    watchlist = [WatchEntry(handle="sama", tags=(), notes="")]

    with pytest.raises(ValueError, match="JSON"):
        await generate_daily_report(
            date=date(2026, 4, 15),
            source=src,
            llm=llm,
            summarizer_model="claude-opus-4-6",
            summarizer_max_tokens=16384,
            watchlist=watchlist,
            reports_dir=tmp_path,
            user_tz=tz,
            timeline_since_hours=24,
            max_tweets_per_handle=50,
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_schema_invalid_llm_json_raises_value_error(tmp_path: Path, tz: ZoneInfo):
    src = FakeTwitterSource(timelines={"sama": [_tweet("sama", "1", 2)]})
    bad = json.dumps({"date": "2026-04-15"})  # missing news_items etc.
    llm = FakeProvider(responses=[bad])
    watchlist = [WatchEntry(handle="sama", tags=(), notes="")]

    with pytest.raises(ValueError):
        await generate_daily_report(
            date=date(2026, 4, 15),
            source=src,
            llm=llm,
            summarizer_model="claude-opus-4-6",
            summarizer_max_tokens=16384,
            watchlist=watchlist,
            reports_dir=tmp_path,
            user_tz=tz,
            timeline_since_hours=24,
            max_tweets_per_handle=50,
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_regenerating_same_date_overwrites(tmp_path: Path, tz: ZoneInfo):
    src = FakeTwitterSource(timelines={"sama": [_tweet("sama", "1", 2)]})
    llm = FakeProvider(responses=[_valid_llm_json(), _valid_llm_json()])
    watchlist = [WatchEntry(handle="sama", tags=(), notes="")]

    for _ in range(2):
        await generate_daily_report(
            date=date(2026, 4, 15),
            source=src,
            llm=llm,
            summarizer_model="claude-opus-4-6",
            summarizer_max_tokens=16384,
            watchlist=watchlist,
            reports_dir=tmp_path,
            user_tz=tz,
            timeline_since_hours=24,
            max_tweets_per_handle=50,
        )

    files = sorted(tmp_path.iterdir())
    assert [f.name for f in files] == ["2026-04-15.json"]
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/intelligence/test_generate_pipeline.py -v`
Expected: FAIL with `ImportError: cannot import name 'generate_daily_report'`.

- [ ] **Step 3: Create `generate.py`**

```python
"""Deterministic daily-report generation pipeline.

Shape: fetch every watchlist handle (catching per-handle errors), feed
everything into ONE LLM call via ``llm.complete``, parse + validate the
returned JSON, atomically write the file. This is ordinary Python — not
an agentic loop. Cost is one Opus call per report (~$1) vs 20-50x more
for a per-handle agentic shape."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from project0.intelligence.report import (
    atomic_write_json,
    parse_json_strict,
    validate_report_dict,
)
from project0.intelligence.source import Tweet, TwitterSource, TwitterSourceError
from project0.intelligence.summarizer_prompt import (
    SUMMARIZER_SYSTEM_PROMPT,
    build_user_prompt,
)
from project0.intelligence.watchlist import WatchEntry
from project0.llm.provider import LLMProvider, Msg

log = logging.getLogger(__name__)


async def generate_daily_report(
    *,
    date: date,
    source: TwitterSource,
    llm: LLMProvider,
    summarizer_model: str,  # accepted for interface symmetry; provider has its own model
    summarizer_max_tokens: int,
    watchlist: Sequence[WatchEntry],
    reports_dir: Path,
    user_tz: ZoneInfo,
    timeline_since_hours: int,
    max_tweets_per_handle: int,
) -> dict[str, Any]:
    """Fetch tweets from the watchlist, summarize via one LLM call,
    validate, write. Returns the written report dict.

    Raises:
        TwitterSourceError: if ALL watchlist handles fail to fetch.
        ValueError: if the LLM returns malformed or schema-invalid JSON.
    """
    del summarizer_model  # provider-bound; caller constructs the provider with Opus
    since = datetime.now(tz=user_tz) - timedelta(hours=timeline_since_hours)

    raw_tweets: list[Tweet] = []
    errors: list[dict[str, Any]] = []

    for entry in watchlist:
        try:
            tweets = await source.fetch_user_timeline(
                entry.handle,
                since=since,
                max_results=max_tweets_per_handle,
            )
            raw_tweets.extend(tweets)
        except TwitterSourceError as e:
            errors.append({"handle": entry.handle, "error": str(e)})
            log.warning("generate_daily_report: %s failed: %s", entry.handle, e)

    if not raw_tweets:
        handles_preview = ", ".join(e["handle"] for e in errors[:5])
        if len(errors) > 5:
            handles_preview += "..."
        raise TwitterSourceError(
            f"all {len(watchlist)} fetches failed: {handles_preview}"
        )

    watchlist_snapshot = [e.handle for e in watchlist]
    user_prompt = build_user_prompt(
        raw_tweets=raw_tweets,
        watchlist_snapshot=watchlist_snapshot,
        errors=errors,
        today_local=date,
        user_tz_name=user_tz.key,
    )

    result_text = await llm.complete(
        system=SUMMARIZER_SYSTEM_PROMPT,
        messages=[Msg(role="user", content=user_prompt)],
        max_tokens=summarizer_max_tokens,
    )

    report_dict = parse_json_strict(result_text)

    # Overwrite fields Python controls (LLM is told to leave these blank).
    report_dict["date"] = date.isoformat()
    report_dict["generated_at"] = datetime.now(tz=user_tz).isoformat()
    report_dict["user_tz"] = user_tz.key
    report_dict["watchlist_snapshot"] = watchlist_snapshot
    stats = report_dict.setdefault("stats", {})
    stats["tweets_fetched"] = len(raw_tweets)
    stats["handles_attempted"] = len(watchlist)
    stats["handles_succeeded"] = len(watchlist) - len(errors)
    stats["errors"] = errors
    stats["items_generated"] = len(report_dict.get("news_items", []))

    validate_report_dict(report_dict)

    out_path = reports_dir / f"{date.isoformat()}.json"
    atomic_write_json(out_path, report_dict)
    log.info("wrote daily report to %s", out_path)
    return report_dict
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run pytest tests/intelligence/test_generate_pipeline.py -v`
Expected: all six tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/project0/intelligence/generate.py tests/intelligence/test_generate_pipeline.py
git commit -m "feat(intelligence): deterministic daily report generation pipeline

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Intelligence persona + config loaders + dataclasses

**Goal:** The first half of the `agents/intelligence.py` rewrite — just the loader functions and dataclasses. The `Intelligence` class itself (tool specs, dispatch, handle, run_chat_turn) lands in Task 10/11/12. Splitting this way keeps each task focused.

**Files:**
- Modify: `src/project0/agents/intelligence.py` (full rewrite starts here)
- Test: `tests/agents/test_intelligence_persona_load.py`
- Test: `tests/agents/test_intelligence_config_load.py`

- [ ] **Step 1: Write the failing persona-loader test**

Create `tests/agents/test_intelligence_persona_load.py`:

```python
"""IntelligencePersona parser tests. Five canonical Chinese headers, same
parse-style as Manager/Secretary: exact header match + near-miss detection
with suggestion."""
from __future__ import annotations

from pathlib import Path

import pytest

from project0.agents.intelligence import (
    IntelligencePersona,
    load_intelligence_persona,
)


VALID_PERSONA = """# 情报 — 角色设定
core content 1

# 模式：私聊
dm content 2

# 模式：群聊点名
group content 3

# 模式：被经理委派
delegated content 4

# 模式：工具使用守则
tools content 5
"""


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "intelligence.md"
    p.write_text(content, encoding="utf-8")
    return p


def test_valid_persona_parses(tmp_path: Path):
    p = _write(tmp_path, VALID_PERSONA)
    persona = load_intelligence_persona(p)
    assert isinstance(persona, IntelligencePersona)
    assert "core content 1" in persona.core
    assert "dm content 2" in persona.dm_mode
    assert "group content 3" in persona.group_addressed_mode
    assert "delegated content 4" in persona.delegated_mode
    assert "tools content 5" in persona.tool_use_guide


def test_missing_section_raises(tmp_path: Path):
    # Drop the tool-use section.
    p = _write(
        tmp_path,
        """# 情报 — 角色设定
core

# 模式：私聊
dm

# 模式：群聊点名
group

# 模式：被经理委派
del
""",
    )
    with pytest.raises(ValueError, match="工具使用守则"):
        load_intelligence_persona(p)


def test_near_miss_header_raises_with_canonical_suggestion(tmp_path: Path):
    # Use half-width colon instead of full-width.
    p = _write(
        tmp_path,
        """# 情报 — 角色设定
core

# 模式:私聊
oops

# 模式：群聊点名
g

# 模式：被经理委派
d

# 模式：工具使用守则
t
""",
    )
    with pytest.raises(ValueError, match="私聊"):
        load_intelligence_persona(p)
```

- [ ] **Step 2: Write the failing config-loader test**

Create `tests/agents/test_intelligence_config_load.py`:

```python
"""IntelligenceConfig loader tests. Parses [llm.summarizer], [llm.qa],
[context], [twitter]. Missing keys raise RuntimeError naming the key."""
from __future__ import annotations

from pathlib import Path

import pytest

from project0.agents.intelligence import (
    IntelligenceConfig,
    load_intelligence_config,
)


VALID_TOML = """
[llm.summarizer]
model = "claude-opus-4-6"
max_tokens = 16384

[llm.qa]
model = "claude-sonnet-4-6"
max_tokens = 2048

[context]
transcript_window = 10
max_tool_iterations = 6

[twitter]
timeline_since_hours = 24
max_tweets_per_handle = 50

[[watch]]
handle = "sama"
"""


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "intelligence.toml"
    p.write_text(content, encoding="utf-8")
    return p


def test_valid_toml_parses(tmp_path: Path):
    p = _write(tmp_path, VALID_TOML)
    cfg = load_intelligence_config(p)
    assert isinstance(cfg, IntelligenceConfig)
    assert cfg.summarizer_model == "claude-opus-4-6"
    assert cfg.summarizer_max_tokens == 16384
    assert cfg.qa_model == "claude-sonnet-4-6"
    assert cfg.qa_max_tokens == 2048
    assert cfg.transcript_window == 10
    assert cfg.max_tool_iterations == 6
    assert cfg.timeline_since_hours == 24
    assert cfg.max_tweets_per_handle == 50


def test_missing_key_raises_runtime_error(tmp_path: Path):
    p = _write(tmp_path, """
[llm.summarizer]
model = "claude-opus-4-6"
""")
    with pytest.raises(RuntimeError, match="max_tokens"):
        load_intelligence_config(p)
```

- [ ] **Step 3: Run both tests and confirm they fail**

Run: `uv run pytest tests/agents/test_intelligence_persona_load.py tests/agents/test_intelligence_config_load.py -v`
Expected: FAIL with `ImportError: cannot import name 'IntelligencePersona'` (the current `agents/intelligence.py` only defines `intelligence_stub`).

- [ ] **Step 4: Rewrite `src/project0/agents/intelligence.py` (loaders + dataclasses only)**

Replace the full contents of `src/project0/agents/intelligence.py` with:

```python
"""Intelligence agent — LLM-backed briefing specialist.

6d scope: Twitter/X ingestion, one-Opus-call daily report generation,
shallow Q&A over the latest report via a Sonnet tool-use loop.

Persona has five canonical Chinese sections (mirrors Manager). The
Intelligence class takes TWO LLM providers: one Opus (summarizer) and
one Sonnet (Q&A). The class itself is completed in Tasks 10–12; this
file currently holds loaders and dataclasses only."""
from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# --- persona -----------------------------------------------------------------

@dataclass(frozen=True)
class IntelligencePersona:
    core: str
    dm_mode: str
    group_addressed_mode: str
    delegated_mode: str
    tool_use_guide: str


# Canonical headers use the full-width colon '：' (U+FF1A). The near-miss
# detector normalizes half-width ':' (U+003A) to full-width before comparing,
# so a typo like "# 模式:私聊" is caught with a helpful suggestion instead of
# silently producing a "missing section" error.
_PERSONA_SECTIONS = {
    "core":                 "# 情报 — 角色设定",
    "dm_mode":               "# 模式：私聊",
    "group_addressed_mode":  "# 模式：群聊点名",
    "delegated_mode":        "# 模式：被经理委派",
    "tool_use_guide":        "# 模式：工具使用守则",
}


def _normalize_header(h: str) -> str:
    """Collapse whitespace and normalise colon variants so near-miss
    headers (half-width ':' instead of full-width '：') are detected."""
    return "".join(h.split()).replace(":", "：")


_CANONICAL_HEADERS_NORMALIZED = {
    _normalize_header(h): h for h in _PERSONA_SECTIONS.values()
}


def load_intelligence_persona(path: Path) -> IntelligencePersona:
    """Parse prompts/intelligence.md into its five sections. Each section
    starts with one of the canonical Chinese headers; the header line must
    match exactly (after stripping trailing whitespace). Lines starting
    with '#' that look close to a canonical header but don't match exactly
    raise ValueError — this catches missing-space and colon-mismatch typos
    before they turn into confusing 'missing section' errors."""
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
            current_buf = [stripped]
            continue
        if stripped.startswith("#"):
            normalized = _normalize_header(stripped)
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
            raise ValueError(
                f"persona file {path} is missing section '{header}'"
            )

    return IntelligencePersona(
        core=sections["core"],
        dm_mode=sections["dm_mode"],
        group_addressed_mode=sections["group_addressed_mode"],
        delegated_mode=sections["delegated_mode"],
        tool_use_guide=sections["tool_use_guide"],
    )


# --- config ------------------------------------------------------------------

@dataclass(frozen=True)
class IntelligenceConfig:
    summarizer_model: str
    summarizer_max_tokens: int
    qa_model: str
    qa_max_tokens: int
    transcript_window: int
    max_tool_iterations: int
    timeline_since_hours: int
    max_tweets_per_handle: int


def load_intelligence_config(path: Path) -> IntelligenceConfig:
    """Parse prompts/intelligence.toml. Missing keys raise RuntimeError
    with enough context to locate the offending file and key. [[watch]]
    entries are ignored here — load_watchlist handles those separately."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    def _require(section: str, key: str) -> Any:
        node = data
        for seg in section.split("."):
            if seg not in node:
                raise RuntimeError(
                    f"missing config section [{section}] in {path}"
                )
            node = node[seg]
        if key not in node:
            raise RuntimeError(
                f"missing config key {section}.{key} in {path}"
            )
        return node[key]

    return IntelligenceConfig(
        summarizer_model=str(_require("llm.summarizer", "model")),
        summarizer_max_tokens=int(_require("llm.summarizer", "max_tokens")),
        qa_model=str(_require("llm.qa", "model")),
        qa_max_tokens=int(_require("llm.qa", "max_tokens")),
        transcript_window=int(_require("context", "transcript_window")),
        max_tool_iterations=int(_require("context", "max_tool_iterations")),
        timeline_since_hours=int(_require("twitter", "timeline_since_hours")),
        max_tweets_per_handle=int(_require("twitter", "max_tweets_per_handle")),
    )
```

- [ ] **Step 5: Remove the `intelligence_stub` references that break the registry**

The current `agents/intelligence.py` had `intelligence_stub` which is imported by `agents/registry.py`. Temporarily restore `intelligence_stub` as a no-op at the bottom of `agents/intelligence.py` so the project keeps importing — we'll remove it for real in Task 13 once Intelligence is fully wired. Append to `agents/intelligence.py`:

```python


# --- legacy stub (removed in Task 13 once Intelligence is fully wired) ------
# Kept so registry.py still imports until register_intelligence exists.

from project0.envelope import AgentResult, Envelope  # noqa: E402


async def intelligence_stub(env: Envelope) -> AgentResult:  # pragma: no cover
    return AgentResult(
        reply_text=f"[intelligence-stub] acknowledged: {env.body}",
        delegate_to=None,
        handoff_text=None,
    )
```

- [ ] **Step 6: Run both new tests and confirm they pass**

Run: `uv run pytest tests/agents/test_intelligence_persona_load.py tests/agents/test_intelligence_config_load.py -v`
Expected: all four tests PASS.

- [ ] **Step 7: Run the full test suite to verify nothing else broke**

Run: `uv run pytest -q`
Expected: every test PASSES. The existing registry.py import of `intelligence_stub` still works.

- [ ] **Step 8: Commit**

```bash
git add src/project0/agents/intelligence.py tests/agents/test_intelligence_persona_load.py tests/agents/test_intelligence_config_load.py
git commit -m "feat(intelligence): persona + config loaders + dataclasses

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Intelligence class — skeleton + tool specs + dispatch

**Goal:** The `Intelligence` class constructor, `_build_tool_specs` (four tools), and `_dispatch_tool` (all four tool handlers). The `handle` routing and chat-turn entry points land in Task 11. TDD via a focused dispatch test.

**Files:**
- Modify: `src/project0/agents/intelligence.py` (add class + specs + dispatch)
- Test: `tests/agents/test_intelligence_tool_dispatch.py`

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_intelligence_tool_dispatch.py`:

```python
"""Intelligence._dispatch_tool unit tests. Each tool is exercised in
isolation by constructing a minimal Intelligence with stub LLMs, a
FakeTwitterSource, and a tmp reports_dir. The tool-use loop itself is
tested separately in test_intelligence_class.py (Task 12)."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.agents._tool_loop import TurnState
from project0.agents.intelligence import (
    Intelligence,
    IntelligenceConfig,
    IntelligencePersona,
)
from project0.intelligence.fake_source import FakeTwitterSource
from project0.intelligence.report import atomic_write_json
from project0.intelligence.source import Tweet
from project0.intelligence.watchlist import WatchEntry
from project0.llm.provider import FakeProvider
from project0.llm.tools import ToolCall


def _persona() -> IntelligencePersona:
    return IntelligencePersona(
        core="core",
        dm_mode="dm",
        group_addressed_mode="group",
        delegated_mode="del",
        tool_use_guide="tools",
    )


def _config() -> IntelligenceConfig:
    return IntelligenceConfig(
        summarizer_model="claude-opus-4-6",
        summarizer_max_tokens=16384,
        qa_model="claude-sonnet-4-6",
        qa_max_tokens=2048,
        transcript_window=10,
        max_tool_iterations=6,
        timeline_since_hours=24,
        max_tweets_per_handle=50,
    )


def _tweet(handle: str, tid: str, hours_ago: int) -> Tweet:
    return Tweet(
        handle=handle,
        tweet_id=tid,
        url=f"https://x.com/{handle}/status/{tid}",
        text=f"t{tid}",
        posted_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        reply_count=0,
        like_count=0,
        retweet_count=0,
    )


def _valid_report_dict(date_str: str = "2026-04-15") -> dict:
    return {
        "date": date_str,
        "generated_at": f"{date_str}T08:00:00+08:00",
        "user_tz": "Asia/Shanghai",
        "watchlist_snapshot": ["sama"],
        "news_items": [
            {
                "id": "n1",
                "headline": "h",
                "summary": "s",
                "importance": "high",
                "importance_reason": "r",
                "topics": ["ai"],
                "source_tweets": [
                    {"handle": "sama", "url": "https://x.com/sama/status/1", "text": "t", "posted_at": "2026-04-15T03:00:00Z"},
                ],
            }
        ],
        "suggested_accounts": [],
        "stats": {
            "tweets_fetched": 1,
            "handles_attempted": 1,
            "handles_succeeded": 1,
            "items_generated": 1,
            "errors": [],
        },
    }


def _build_intelligence(tmp_path: Path, *, src: FakeTwitterSource | None = None) -> Intelligence:
    if src is None:
        src = FakeTwitterSource(timelines={"sama": [_tweet("sama", "1", 2)]})
    llm_summarizer = FakeProvider(responses=[json.dumps(_valid_report_dict())])
    llm_qa = FakeProvider(tool_responses=[])
    return Intelligence(
        llm_summarizer=llm_summarizer,
        llm_qa=llm_qa,
        twitter=src,
        messages_store=None,
        persona=_persona(),
        config=_config(),
        watchlist=[WatchEntry(handle="sama", tags=(), notes="")],
        reports_dir=tmp_path,
        user_tz=ZoneInfo("Asia/Shanghai"),
    )


@pytest.mark.asyncio
async def test_generate_daily_report_tool_writes_file(tmp_path: Path):
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="generate_daily_report", input={"date": "2026-04-15"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    data = json.loads(content)
    assert data["path"].endswith("2026-04-15.json")
    assert data["item_count"] == 1
    assert (tmp_path / "2026-04-15.json").exists()


@pytest.mark.asyncio
async def test_generate_daily_report_default_date(tmp_path: Path):
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="generate_daily_report", input={})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    assert (tmp_path / f"{today}.json").exists()


@pytest.mark.asyncio
async def test_generate_daily_report_total_failure_returns_error(tmp_path: Path):
    src = FakeTwitterSource(timelines={})  # empty → total failure
    intel = _build_intelligence(tmp_path, src=src)
    call = ToolCall(id="t1", name="generate_daily_report", input={"date": "2026-04-15"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is True
    assert "all" in content.lower() or "twitter" in content.lower()
    assert not (tmp_path / "2026-04-15.json").exists()


@pytest.mark.asyncio
async def test_get_latest_report_no_reports(tmp_path: Path):
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="get_latest_report", input={})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    assert "no reports" in content.lower()


@pytest.mark.asyncio
async def test_get_latest_report_returns_most_recent(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-14.json", _valid_report_dict("2026-04-14"))
    atomic_write_json(tmp_path / "2026-04-15.json", _valid_report_dict("2026-04-15"))
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="get_latest_report", input={})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    data = json.loads(content)
    assert data["date"] == "2026-04-15"


@pytest.mark.asyncio
async def test_get_report_by_date(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-14.json", _valid_report_dict("2026-04-14"))
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="get_report", input={"date": "2026-04-14"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    data = json.loads(content)
    assert data["date"] == "2026-04-14"


@pytest.mark.asyncio
async def test_get_report_missing_date_returns_not_found(tmp_path: Path):
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="get_report", input={"date": "2020-01-01"})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    assert "no report" in content.lower()


@pytest.mark.asyncio
async def test_list_reports_returns_sorted_desc(tmp_path: Path):
    for d in ["2026-04-13", "2026-04-15", "2026-04-14"]:
        atomic_write_json(tmp_path / f"{d}.json", _valid_report_dict(d))
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="list_reports", input={"limit": 5})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is False
    data = json.loads(content)
    assert [e["date"] for e in data] == ["2026-04-15", "2026-04-14", "2026-04-13"]


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(tmp_path: Path):
    intel = _build_intelligence(tmp_path)
    call = ToolCall(id="t1", name="nonexistent_tool", input={})
    content, is_err = await intel._dispatch_tool(call, TurnState())
    assert is_err is True
    assert "unknown" in content.lower()
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/agents/test_intelligence_tool_dispatch.py -v`
Expected: FAIL with `ImportError: cannot import name 'Intelligence'`.

- [ ] **Step 3: Add the Intelligence class to `agents/intelligence.py`**

Insert the following section into `src/project0/agents/intelligence.py`, immediately before the legacy `intelligence_stub` block at the bottom:

```python
# --- tool input schemas ------------------------------------------------------

_GENERATE_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {
            "type": "string",
            "description": "YYYY-MM-DD; defaults to today in user_tz",
        },
    },
    "required": [],
}

_GET_LATEST_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}

_GET_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {"type": "string", "description": "YYYY-MM-DD"},
    },
    "required": ["date"],
}

_LIST_REPORTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "minimum": 1, "maximum": 30},
    },
    "required": [],
}


# --- Intelligence class -----------------------------------------------------

from datetime import date, datetime  # noqa: E402
from typing import TYPE_CHECKING  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from project0.agents._tool_loop import TurnState  # noqa: E402
from project0.envelope import AgentResult, Envelope  # noqa: E402
from project0.intelligence.generate import generate_daily_report  # noqa: E402
from project0.intelligence.report import (  # noqa: E402
    list_report_dates,
    read_report,
)
from project0.intelligence.source import TwitterSource, TwitterSourceError  # noqa: E402
from project0.intelligence.watchlist import WatchEntry  # noqa: E402
from project0.llm.tools import ToolCall, ToolSpec  # noqa: E402

if TYPE_CHECKING:
    from project0.llm.provider import LLMProvider
    from project0.store import MessagesStore


import json  # noqa: E402


class Intelligence:
    """LLM-backed briefing specialist. Two LLM providers: Opus for the
    deterministic summarization pipeline, Sonnet for the agentic Q&A
    loop. Four tools: generate_daily_report, get_latest_report,
    get_report, list_reports. Never delegates."""

    def __init__(
        self,
        *,
        llm_summarizer: "LLMProvider",
        llm_qa: "LLMProvider",
        twitter: TwitterSource,
        messages_store: "MessagesStore | None",
        persona: IntelligencePersona,
        config: IntelligenceConfig,
        watchlist: list[WatchEntry],
        reports_dir: Path,
        user_tz: ZoneInfo,
    ) -> None:
        self._llm_summarizer = llm_summarizer
        self._llm_qa = llm_qa
        self._twitter = twitter
        self._messages = messages_store
        self._persona = persona
        self._config = config
        self._watchlist = watchlist
        self._reports_dir = reports_dir
        self._user_tz = user_tz
        self._tool_specs = self._build_tool_specs()

    def _build_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="generate_daily_report",
                description=(
                    "Fetch tweets from the watchlist and write a new daily "
                    "report JSON file. Use only when the user clearly asked "
                    "to generate a new report."
                ),
                input_schema=_GENERATE_REPORT_SCHEMA,
            ),
            ToolSpec(
                name="get_latest_report",
                description=(
                    "Read the most recent daily report. DO NOT CALL this "
                    "when today's report is already injected into your "
                    "context — only call it as a fallback."
                ),
                input_schema=_GET_LATEST_REPORT_SCHEMA,
            ),
            ToolSpec(
                name="get_report",
                description="Read a specific past daily report by date.",
                input_schema=_GET_REPORT_SCHEMA,
            ),
            ToolSpec(
                name="list_reports",
                description=(
                    "List available report dates (most recent first)."
                ),
                input_schema=_LIST_REPORTS_SCHEMA,
            ),
        ]

    async def _dispatch_tool(
        self,
        call: ToolCall,
        turn_state: TurnState,
    ) -> tuple[str, bool]:
        """Execute one tool call. Intelligence never delegates, so
        ``turn_state`` is accepted for interface symmetry but not mutated."""
        del turn_state  # unused; Intelligence never delegates
        try:
            return await self._dispatch_tool_inner(call)
        except TwitterSourceError as e:
            log.warning("twitter error in tool %s: %s", call.name, e)
            return f"twitter source error: {e}", True
        except (KeyError, ValueError, TypeError) as e:
            log.warning("input error in tool %s: %s", call.name, e)
            return f"invalid input for {call.name}: {e}", True

    async def _dispatch_tool_inner(
        self,
        call: ToolCall,
    ) -> tuple[str, bool]:
        name = call.name
        inp = call.input

        if name == "generate_daily_report":
            date_str = inp.get("date")
            target_date = (
                date.fromisoformat(date_str)
                if date_str
                else datetime.now(tz=self._user_tz).date()
            )
            report = await generate_daily_report(
                date=target_date,
                source=self._twitter,
                llm=self._llm_summarizer,
                summarizer_model=self._config.summarizer_model,
                summarizer_max_tokens=self._config.summarizer_max_tokens,
                watchlist=self._watchlist,
                reports_dir=self._reports_dir,
                user_tz=self._user_tz,
                timeline_since_hours=self._config.timeline_since_hours,
                max_tweets_per_handle=self._config.max_tweets_per_handle,
            )
            return json.dumps({
                "path": str(self._reports_dir / f"{target_date}.json"),
                "item_count": len(report.get("news_items", [])),
                "tweets_fetched": report["stats"]["tweets_fetched"],
                "handles_failed": len(report["stats"]["errors"]),
            }, ensure_ascii=False), False

        if name == "get_latest_report":
            dates = list_report_dates(self._reports_dir)
            if not dates:
                return "no reports available", False
            latest = read_report(self._reports_dir / f"{dates[0]}.json")
            return json.dumps(latest, ensure_ascii=False), False

        if name == "get_report":
            target = date.fromisoformat(inp["date"])
            path = self._reports_dir / f"{target}.json"
            if not path.exists():
                return f"no report for {target}", False
            report = read_report(path)
            return json.dumps(report, ensure_ascii=False), False

        if name == "list_reports":
            limit = int(inp.get("limit", 7))
            dates = list_report_dates(self._reports_dir)[:limit]
            results = []
            for d in dates:
                rep = read_report(self._reports_dir / f"{d}.json")
                results.append({
                    "date": d.isoformat(),
                    "item_count": len(rep.get("news_items", [])),
                })
            return json.dumps(results, ensure_ascii=False), False

        return f"unknown tool: {name}", True
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run pytest tests/agents/test_intelligence_tool_dispatch.py -v`
Expected: all nine tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/project0/agents/intelligence.py tests/agents/test_intelligence_tool_dispatch.py
git commit -m "feat(intelligence): Intelligence class skeleton + tool dispatch

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Intelligence chat turn + handle routing

**Goal:** Add `handle`, `_run_chat_turn`, `_run_delegated_turn`, and `_finalize_loop` to the `Intelligence` class. The shared `run_agentic_loop` helper drives the tool-use iteration; Intelligence simply wraps it with report-context injection and a trivial finalizer (no pulse logic, no delegation).

**Files:**
- Modify: `src/project0/agents/intelligence.py` (add class methods)
- Test: `tests/agents/test_intelligence_class.py`

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_intelligence_class.py`:

```python
"""Intelligence agentic-turn tests. Drives Intelligence.handle end-to-end
using FakeProvider scripted tool_responses. Covers:
  - DM no-report content question → plain text reply
  - DM generation request → tool call succeeds → ack text
  - DM with latest report → plain text reply from context
  - DM 'yesterday' → get_report tool call → reply
  - delegated turn (default_manager routing)
  - LLM error → returns None
  - iteration overflow → LLMProviderError
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.agents.intelligence import (
    Intelligence,
    IntelligenceConfig,
    IntelligencePersona,
)
from project0.envelope import Envelope
from project0.intelligence.fake_source import FakeTwitterSource
from project0.intelligence.report import atomic_write_json
from project0.intelligence.source import Tweet
from project0.intelligence.watchlist import WatchEntry
from project0.llm.provider import FakeProvider, LLMProviderError
from project0.llm.tools import ToolCall, ToolUseResult


def _persona() -> IntelligencePersona:
    return IntelligencePersona(
        core="core",
        dm_mode="dm",
        group_addressed_mode="group",
        delegated_mode="del",
        tool_use_guide="tools",
    )


def _config(max_iter: int = 6) -> IntelligenceConfig:
    return IntelligenceConfig(
        summarizer_model="claude-opus-4-6",
        summarizer_max_tokens=16384,
        qa_model="claude-sonnet-4-6",
        qa_max_tokens=2048,
        transcript_window=10,
        max_tool_iterations=max_iter,
        timeline_since_hours=24,
        max_tweets_per_handle=50,
    )


def _tweet(handle: str, tid: str, hours_ago: int) -> Tweet:
    return Tweet(
        handle=handle, tweet_id=tid, url=f"https://x.com/{handle}/status/{tid}",
        text=f"t{tid}",
        posted_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        reply_count=0, like_count=0, retweet_count=0,
    )


def _valid_report_dict(date_str: str = "2026-04-15") -> dict:
    return {
        "date": date_str,
        "generated_at": f"{date_str}T08:00:00+08:00",
        "user_tz": "Asia/Shanghai",
        "watchlist_snapshot": ["sama"],
        "news_items": [
            {
                "id": "n1", "headline": "h", "summary": "s",
                "importance": "high", "importance_reason": "r",
                "topics": ["ai"],
                "source_tweets": [{"handle": "sama", "url": "https://x.com/sama/status/1", "text": "t", "posted_at": "2026-04-15T03:00:00Z"}],
            }
        ],
        "suggested_accounts": [],
        "stats": {
            "tweets_fetched": 1, "handles_attempted": 1, "handles_succeeded": 1,
            "items_generated": 1, "errors": [],
        },
    }


class _StubMessagesStore:
    """Minimal MessagesStore stand-in returning an empty transcript."""
    def recent_for_chat(self, *, chat_id: int, limit: int) -> list[Envelope]:
        return []


def _build_intelligence(
    tmp_path: Path,
    *,
    qa_tool_responses: list[ToolUseResult],
    summarizer_responses: list[str] | None = None,
) -> Intelligence:
    src = FakeTwitterSource(timelines={"sama": [_tweet("sama", "1", 2)]})
    llm_summarizer = FakeProvider(responses=summarizer_responses or [json.dumps(_valid_report_dict())])
    llm_qa = FakeProvider(tool_responses=qa_tool_responses)
    return Intelligence(
        llm_summarizer=llm_summarizer,
        llm_qa=llm_qa,
        twitter=src,
        messages_store=_StubMessagesStore(),
        persona=_persona(),
        config=_config(),
        watchlist=[WatchEntry(handle="sama", tags=(), notes="")],
        reports_dir=tmp_path,
        user_tz=ZoneInfo("Asia/Shanghai"),
    )


def _dm_envelope(body: str) -> Envelope:
    return Envelope(
        source="telegram_dm",
        from_kind="user",
        from_agent=None,
        to_agent="intelligence",
        routing_reason="direct_dm",
        telegram_chat_id=42,
        telegram_msg_id=1,
        received_by_bot="intelligence",
        body=body,
        payload=None,
        mentions=[],
    )


@pytest.mark.asyncio
async def test_dm_no_report_content_question_replies_plain_text(tmp_path: Path):
    intel = _build_intelligence(
        tmp_path,
        qa_tool_responses=[
            ToolUseResult(kind="text", text="目前还没有日报，要我现在生成一份吗？", tool_calls=[], stop_reason="end_turn"),
        ],
    )
    result = await intel.handle(_dm_envelope("今天有什么 AI 消息？"))
    assert result is not None
    assert result.reply_text and "日报" in result.reply_text
    assert result.delegate_to is None


@pytest.mark.asyncio
async def test_dm_generation_request_runs_tool_then_acks(tmp_path: Path):
    intel = _build_intelligence(
        tmp_path,
        qa_tool_responses=[
            ToolUseResult(
                kind="tool_use", text=None,
                tool_calls=[ToolCall(id="t1", name="generate_daily_report", input={"date": "2026-04-15"})],
                stop_reason="tool_use",
            ),
            ToolUseResult(kind="text", text="好，今天的日报写好了。", tool_calls=[], stop_reason="end_turn"),
        ],
    )
    result = await intel.handle(_dm_envelope("生成今天的报告"))
    assert result is not None
    assert result.reply_text and "日报" in result.reply_text
    assert (tmp_path / "2026-04-15.json").exists()


@pytest.mark.asyncio
async def test_dm_with_latest_report_replies_from_context(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-15.json", _valid_report_dict("2026-04-15"))
    intel = _build_intelligence(
        tmp_path,
        qa_tool_responses=[
            ToolUseResult(kind="text", text="今天最要紧的是 n1。", tool_calls=[], stop_reason="end_turn"),
        ],
    )
    result = await intel.handle(_dm_envelope("今天有什么？"))
    assert result is not None
    assert "n1" in result.reply_text
    # Should not have called the summarizer provider.
    assert intel._llm_summarizer.calls == []


@pytest.mark.asyncio
async def test_dm_yesterday_triggers_get_report(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-14.json", _valid_report_dict("2026-04-14"))
    atomic_write_json(tmp_path / "2026-04-15.json", _valid_report_dict("2026-04-15"))
    intel = _build_intelligence(
        tmp_path,
        qa_tool_responses=[
            ToolUseResult(
                kind="tool_use", text=None,
                tool_calls=[ToolCall(id="t1", name="get_report", input={"date": "2026-04-14"})],
                stop_reason="tool_use",
            ),
            ToolUseResult(kind="text", text="昨天的重点是...", tool_calls=[], stop_reason="end_turn"),
        ],
    )
    result = await intel.handle(_dm_envelope("昨天有什么？"))
    assert result is not None
    assert result.reply_text.startswith("昨天的重点")


@pytest.mark.asyncio
async def test_delegated_turn_routes_through_delegated_mode(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-15.json", _valid_report_dict("2026-04-15"))
    intel = _build_intelligence(
        tmp_path,
        qa_tool_responses=[
            ToolUseResult(kind="text", text="经理交代的事我看过日报了，n1 条和你问的相关。", tool_calls=[], stop_reason="end_turn"),
        ],
    )
    env = Envelope(
        source="telegram_dm",
        from_kind="agent",
        from_agent="manager",
        to_agent="intelligence",
        routing_reason="default_manager",
        telegram_chat_id=42,
        telegram_msg_id=2,
        received_by_bot="manager",
        body="(delegated)",
        payload={"kind": "query", "query": "o5 的发布怎么样？"},
        mentions=[],
    )
    result = await intel.handle(env)
    assert result is not None
    assert "n1" in result.reply_text


@pytest.mark.asyncio
async def test_llm_error_returns_none(tmp_path: Path):
    intel = _build_intelligence(tmp_path, qa_tool_responses=[])  # exhausted → LLMProviderError
    result = await intel.handle(_dm_envelope("hi"))
    assert result is None


@pytest.mark.asyncio
async def test_iteration_overflow_raises(tmp_path: Path):
    atomic_write_json(tmp_path / "2026-04-15.json", _valid_report_dict("2026-04-15"))
    # Force the model to keep calling tools forever → exceeds max_tool_iterations.
    infinite = [
        ToolUseResult(
            kind="tool_use", text=None,
            tool_calls=[ToolCall(id=f"t{i}", name="list_reports", input={"limit": 1})],
            stop_reason="tool_use",
        )
        for i in range(20)
    ]
    intel = Intelligence(
        llm_summarizer=FakeProvider(responses=[]),
        llm_qa=FakeProvider(tool_responses=infinite),
        twitter=FakeTwitterSource(timelines={}),
        messages_store=_StubMessagesStore(),
        persona=_persona(),
        config=_config(max_iter=3),
        watchlist=[],
        reports_dir=tmp_path,
        user_tz=ZoneInfo("Asia/Shanghai"),
    )
    with pytest.raises(LLMProviderError, match="max_iterations"):
        await intel.handle(_dm_envelope("hi"))


@pytest.mark.asyncio
async def test_unknown_routing_reason_returns_none(tmp_path: Path):
    intel = _build_intelligence(tmp_path, qa_tool_responses=[])
    env = Envelope(
        source="telegram_group",
        from_kind="user",
        from_agent=None,
        to_agent="intelligence",
        routing_reason="listener_observation",
        telegram_chat_id=42,
        telegram_msg_id=1,
        received_by_bot="intelligence",
        body="random group chatter",
        payload=None,
        mentions=[],
    )
    result = await intel.handle(env)
    assert result is None
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/agents/test_intelligence_class.py -v`
Expected: FAIL with `AttributeError: 'Intelligence' object has no attribute 'handle'` (or the method exists but the wrapping logic is missing).

- [ ] **Step 3: Add `handle`, `_run_chat_turn`, `_run_delegated_turn`, `_finalize_loop`, `_try_read_latest_report` to `Intelligence`**

Append the following methods to the `Intelligence` class in `src/project0/agents/intelligence.py` (after `_dispatch_tool_inner`, before the legacy `intelligence_stub` block):

```python
    async def handle(self, env: Envelope) -> AgentResult | None:
        reason = env.routing_reason
        if reason == "direct_dm":
            return await self._run_chat_turn(env, self._persona.dm_mode)
        if reason in ("mention", "focus"):
            return await self._run_chat_turn(env, self._persona.group_addressed_mode)
        if reason == "default_manager":
            return await self._run_delegated_turn(env)
        log.debug("intelligence: ignoring routing_reason=%s", reason)
        return None

    def _try_read_latest_report(self) -> dict[str, Any] | None:
        dates = list_report_dates(self._reports_dir)
        if not dates:
            return None
        try:
            return read_report(self._reports_dir / f"{dates[0]}.json")
        except (ValueError, OSError) as e:
            log.warning(
                "intelligence: failed to read latest report %s: %s",
                dates[0], e,
            )
            return None

    def _recent_messages(self, chat_id: int | None) -> list[Envelope]:
        if chat_id is None or self._messages is None:
            return []
        return self._messages.recent_for_chat(
            chat_id=chat_id, limit=self._config.transcript_window
        )

    async def _run_chat_turn(
        self, env: Envelope, mode_section: str
    ) -> AgentResult | None:
        from project0.intelligence.summarizer_prompt import build_qa_user_prompt

        latest = self._try_read_latest_report()
        transcript = self._recent_messages(env.telegram_chat_id)
        system = (
            self._persona.core
            + "\n\n" + mode_section
            + "\n\n" + self._persona.tool_use_guide
        )
        initial_user_text = build_qa_user_prompt(
            latest_report=latest,
            current_date_local=datetime.now(tz=self._user_tz).date(),
            recent_messages=transcript,
            current_user_message=env.body,
        )
        return await self._run_loop(
            system=system,
            initial_user_text=initial_user_text,
        )

    async def _run_delegated_turn(self, env: Envelope) -> AgentResult | None:
        from project0.intelligence.summarizer_prompt import build_delegated_user_prompt

        payload = env.payload or {}
        query = payload.get("query") or env.body or ""
        latest = self._try_read_latest_report()
        system = (
            self._persona.core
            + "\n\n" + self._persona.delegated_mode
            + "\n\n" + self._persona.tool_use_guide
        )
        initial_user_text = build_delegated_user_prompt(
            latest_report=latest,
            current_date_local=datetime.now(tz=self._user_tz).date(),
            query=query,
        )
        return await self._run_loop(
            system=system,
            initial_user_text=initial_user_text,
        )

    async def _run_loop(
        self,
        *,
        system: str,
        initial_user_text: str,
    ) -> AgentResult | None:
        from project0.agents._tool_loop import run_agentic_loop

        loop = await run_agentic_loop(
            llm=self._llm_qa,
            system=system,
            initial_user_text=initial_user_text,
            tools=self._tool_specs,
            dispatch_tool=self._dispatch_tool,
            max_iterations=self._config.max_tool_iterations,
            max_tokens=self._config.qa_max_tokens,
        )
        if loop.errored:
            return None
        # Intelligence never delegates — ignore turn_state.
        return AgentResult(
            reply_text=loop.final_text or "",
            delegate_to=None,
            handoff_text=None,
        )
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run pytest tests/agents/test_intelligence_class.py -v`
Expected: all eight tests PASS.

- [ ] **Step 5: Run the full test suite to catch regressions**

Run: `uv run pytest -q`
Expected: every test PASSES.

- [ ] **Step 6: Commit**

```bash
git add src/project0/agents/intelligence.py tests/agents/test_intelligence_class.py
git commit -m "feat(intelligence): chat turn + delegated turn + handle routing

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: `register_intelligence` + remove stub

**Goal:** Add `register_intelligence` to `agents/registry.py` symmetric with `register_manager`, drop the `intelligence_stub` import and pre-population of `AGENT_REGISTRY["intelligence"]`, and delete the stub function from `agents/intelligence.py`.

**Files:**
- Modify: `src/project0/agents/registry.py`
- Modify: `src/project0/agents/intelligence.py` (remove stub)
- Test: `tests/agents/test_register_intelligence.py`
- Modify: `tests/test_agents.py` (update stale assertions)

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_register_intelligence.py`:

```python
"""register_intelligence tests. Parallel to test_register_manager.py."""
from __future__ import annotations

import pytest

from project0.agents.registry import AGENT_REGISTRY, AGENT_SPECS, register_intelligence
from project0.envelope import AgentResult, Envelope


@pytest.mark.asyncio
async def test_register_intelligence_installs_handler():
    async def fake_handle(env: Envelope) -> AgentResult | None:
        return AgentResult(reply_text="ok", delegate_to=None, handoff_text=None)

    register_intelligence(fake_handle)
    assert "intelligence" in AGENT_REGISTRY


@pytest.mark.asyncio
async def test_register_intelligence_adapter_surfaces_none_as_placeholder():
    async def null_handle(env: Envelope) -> AgentResult | None:
        return None

    register_intelligence(null_handle)
    env = Envelope(
        source="telegram_dm",
        from_kind="user",
        from_agent=None,
        to_agent="intelligence",
        routing_reason="direct_dm",
        telegram_chat_id=1,
        telegram_msg_id=1,
        received_by_bot="intelligence",
        body="hi",
        payload=None,
        mentions=[],
    )
    result = await AGENT_REGISTRY["intelligence"](env)
    assert isinstance(result, AgentResult)
    assert result.reply_text  # non-empty placeholder


def test_intelligence_token_env_key_unchanged():
    assert AGENT_SPECS["intelligence"].token_env_key == "TELEGRAM_BOT_TOKEN_INTELLIGENCE"
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/agents/test_register_intelligence.py -v`
Expected: FAIL with `ImportError: cannot import name 'register_intelligence'`.

- [ ] **Step 3: Update `registry.py`**

Replace the full contents of `src/project0/agents/registry.py` with:

```python
"""Central registry of agents, their metadata, and their listener roles.

Two dicts:
  - AGENT_REGISTRY: routing targets (@mention, focus, default_manager,
    direct_dm, manager_delegation). The orchestrator dispatches an envelope
    to exactly one entry here.
  - LISTENER_REGISTRY: passive observers. After the focus target is
    dispatched, the orchestrator fans out a listener_observation envelope
    to every entry here whose name is not already the focus target.

Manager, Secretary, and Intelligence are class instances with dependencies,
installed via ``register_manager`` / ``register_secretary`` /
``register_intelligence`` from main.py at startup."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from project0.envelope import AgentResult, Envelope

AgentFn = Callable[[Envelope], Awaitable[AgentResult]]
AgentOptionalFn = Callable[[Envelope], Awaitable[AgentResult | None]]
ListenerFn = Callable[[Envelope], Awaitable[AgentResult | None]]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    token_env_key: str


AGENT_REGISTRY: dict[str, AgentFn] = {
    # "manager" installed by register_manager(...) in main.py.
    # "secretary" installed by register_secretary(...) in main.py.
    # "intelligence" installed by register_intelligence(...) in main.py.
}

LISTENER_REGISTRY: dict[str, ListenerFn] = {
    # "secretary" installed by register_secretary(...) in main.py.
}

# Raw, *un-adapted* optional-return handlers used by ``handle_pulse``. The
# AGENT_REGISTRY adapters convert None → a fail-visible fallback reply so
# that a silent chat turn still produces something the user can see. For
# pulses, None means "nothing urgent, stay silent" — that must propagate
# unchanged, so pulse dispatch bypasses the adapter via this registry.
PULSE_REGISTRY: dict[str, ListenerFn] = {
    # "manager" installed by register_manager(...) in main.py.
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
    """Install Manager's ``handle`` into AGENT_REGISTRY + PULSE_REGISTRY."""

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
    PULSE_REGISTRY["manager"] = handle


def register_secretary(handle: ListenerFn) -> None:
    """Install Secretary's ``handle`` into both registries."""

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


def register_intelligence(handle: AgentOptionalFn) -> None:
    """Install Intelligence's ``handle`` into AGENT_REGISTRY. Adapts the
    ``AgentResult | None`` return type to the ``AgentResult`` expected by
    AGENT_REGISTRY by surfacing a fail-visible placeholder if handle()
    returns None (which happens on LLM errors or unhandled routing
    reasons)."""

    async def agent_adapter(env: Envelope) -> AgentResult:
        result = await handle(env)
        if result is None:
            return AgentResult(
                reply_text="(情报暂时不在状态...)",
                delegate_to=None,
                handoff_text=None,
            )
        return result

    AGENT_REGISTRY["intelligence"] = agent_adapter
```

- [ ] **Step 4: Remove the legacy stub from `agents/intelligence.py`**

Delete the trailing block in `src/project0/agents/intelligence.py`:

```python
# --- legacy stub (removed in Task 13 once Intelligence is fully wired) ------
# Kept so registry.py still imports until register_intelligence exists.

from project0.envelope import AgentResult, Envelope  # noqa: E402


async def intelligence_stub(env: Envelope) -> AgentResult:  # pragma: no cover
    return AgentResult(
        reply_text=f"[intelligence-stub] acknowledged: {env.body}",
        delegate_to=None,
        handoff_text=None,
    )
```

(Also remove the duplicate `from project0.envelope import AgentResult, Envelope` import line. The `Intelligence` class body at the top of the file already imported these via its own block — keep that one.)

- [ ] **Step 5: Update `tests/test_agents.py` to reflect the new registry shape**

Open `tests/test_agents.py` and find the assertion that uses the old `intelligence` entry pre-populated in `AGENT_REGISTRY` (typically `assert "intelligence" in AGENT_REGISTRY` at import time). Remove that assertion or replace it with a note that intelligence is installed at runtime, matching how Manager is handled in the same file.

Specifically, edit the test near line 42 that references `intelligence_stub`. Change:

```python
    # manager is installed at runtime via register_manager(); not at import time.
    ...
    # intelligence is still a stub at import time (changes in 6d).
    assert AGENT_REGISTRY["intelligence"] is intelligence_stub  # or similar
```

to:

```python
    # manager, secretary, and intelligence are all installed at runtime via
    # their respective register_*() functions in main.py. They are NOT present
    # in AGENT_REGISTRY at import time.
    ...
```

and drop any `from project0.agents.intelligence import intelligence_stub` line at the top of the file. Keep the token-env-key assertions (`AGENT_SPECS["intelligence"].token_env_key == "TELEGRAM_BOT_TOKEN_INTELLIGENCE"`) — they still hold.

- [ ] **Step 6: Run the new test and confirm it passes**

Run: `uv run pytest tests/agents/test_register_intelligence.py -v`
Expected: all three tests PASS.

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest -q`
Expected: every test PASSES. If `tests/test_agents.py` still imports `intelligence_stub`, fix that now and re-run.

- [ ] **Step 8: Commit**

```bash
git add src/project0/agents/registry.py src/project0/agents/intelligence.py tests/agents/test_register_intelligence.py tests/test_agents.py
git commit -m "feat(registry): register_intelligence + drop intelligence_stub

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Seed prompt files + gitignore

**Goal:** Create `prompts/intelligence.md` and `prompts/intelligence.toml` with Chinese persona + runtime config + a small seed watchlist. Add `data/intelligence/reports/` to `.gitignore`.

**Files:**
- Create: `prompts/intelligence.md`
- Create: `prompts/intelligence.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Create `prompts/intelligence.md`**

```markdown
# 情报 — 角色设定

你是 Project 0 多智能体系统里的情报官（Intelligence）。你的专业是从 Twitter/X 抓取并整理科技、AI、机器学习和行业动向，每天生成一份结构化的日报，供用户快速了解世界发生了什么。

你说话简洁克制，不虚张声势。陈述任何事实都必须有推文链接支持。不知道就说不知道，不编造。用户问你「今天有什么」时，你先看已经加载进上下文的最新日报,直接回答；日报覆盖不到的话直白告诉他「日报里没有这条」。

你从来不窥探别的 Agent 的私有记忆，不看 Manager 的原始对话或者 Supervisor 的审计日志。你的视野就是 Twitter/X + 自己的日报文件。

你的首要目标是帮用户低成本地掌握正在发生的事，不是输出海量信息。

# 模式：私聊

用户在私聊里直接找你，有两种典型情况：

1. **他让你生成新日报**（「生成今天的报告」「给我来份今天的简报」「run daily report」之类）。这时你调用 `generate_daily_report` 工具，工具跑完后简短地告诉他：日报写好了，抓了多少条、总结了几条要闻、哪几个账号没抓到。然后可以顺手问一句要不要看重点。

2. **他在问日报里的内容**。最新日报已经注入到你本轮的上下文里，不用再调用 `get_latest_report`。直接用里面的 `news_items` 和 `source_tweets` 作答。
   - 如果日报的 `date` 不是今天，明确告诉他你看的是哪一天的日报。
   - 如果还没有任何日报，告诉他没有，顺便提议生成一份。
   - 用户问到比最新日报更早的日期（「昨天」「上周」），调用 `get_report` 或 `list_reports` 去翻历史。

# 模式：群聊点名

你被群里 @ 了，或者轮到你做群里的焦点对象。回复口吻比私聊正式一点，不要放飞。除非用户明确要求，否则不要在群聊里触发 `generate_daily_report`（那是一个耗时半分钟以上的操作，群聊不是它的舞台）。

问答路径跟私聊一样：先看上下文里的日报，引用时贴出 `source_tweets[].url`。

# 模式：被经理委派

经理通过 `delegate_to_intelligence` 工具把一个查询转给你。查询本身应该是自包含的（经理的人设要求他这么做）。你基于最新日报作答：

- 日报里有相关条目：引用 `source_tweets[].url`，简要回答。
- 日报里没有：直白说「最新的日报没覆盖到这个」，不要瞎编。
- 日报过期：告诉对方日报是哪天的，再回答。

被委派的场景**不要**再生成新日报（那是用户主动行为；经理自己不能帮用户做这个决定）。

# 模式：工具使用守则

- **不要调用 `get_latest_report`**。最新日报已经在你的初始上下文里了，再调用一次就是浪费一次工具调用轮次。
- `generate_daily_report` **只有在用户明确要求生成**时才调用。模糊的问题（「今天怎么样」）默认是问内容，不是要生成。
- 引用 `news_items` 里的内容时必须同时带上 `source_tweets[].url`，不要只说「听说 X」。
- 用户问「昨天」「上周」这类老日期时，用 `get_report(date=...)` 或 `list_reports` 去翻；如果对应日期的日报不存在，直接说没有。
- 生成日报后立刻停止调用工具，输出一条简短的中文回执。不要连续调用两个大动作。
- 任何时候最新日报里的 `news_items` 是空的，直接告诉用户「这一天没啥值得报的」，不要硬编故事填版面。
```

- [ ] **Step 2: Create `prompts/intelligence.toml`**

```toml
# Intelligence agent config (6d). See docs/superpowers/specs/2026-04-15-intelligence-agent-design.md
# for scope and rationale.

[llm.summarizer]
# Opus is used for the one-shot deterministic daily report synthesis.
# Quality-sensitive clustering + importance ranking; single call per report.
model      = "claude-opus-4-6"
max_tokens = 16384

[llm.qa]
# Sonnet drives the agentic Q&A tool-use loop. Cheaper per-call; the model
# reads a pre-built report rather than synthesizing one.
model      = "claude-sonnet-4-6"
max_tokens = 2048

[context]
transcript_window   = 10
max_tool_iterations = 6

[twitter]
timeline_since_hours  = 24
max_tweets_per_handle = 50

# --- Seed watchlist (hand-curated for 6d) -----------------------------------
#
# Add/remove handles by editing this file and restarting the process. Dynamic
# follows via chat are deferred to 6h (feedback loop). Good seeds are
# first-party company accounts + a handful of trusted researchers.

[[watch]]
handle = "openai"
tags   = ["ai-labs", "first-party"]
notes  = "OpenAI official"

[[watch]]
handle = "anthropicai"
tags   = ["ai-labs", "first-party"]
notes  = "Anthropic official"

[[watch]]
handle = "googledeepmind"
tags   = ["ai-labs", "first-party"]
notes  = "Google DeepMind official"

[[watch]]
handle = "sama"
tags   = ["ai-labs", "executive"]
notes  = "Sam Altman"

[[watch]]
handle = "demishassabis"
tags   = ["ai-labs", "executive"]
notes  = "Demis Hassabis"

[[watch]]
handle = "karpathy"
tags   = ["researcher"]
notes  = "Andrej Karpathy"

[[watch]]
handle = "ylecun"
tags   = ["researcher"]
notes  = "Yann LeCun"

[[watch]]
handle = "jeffdean"
tags   = ["researcher"]
notes  = "Jeff Dean"

[[watch]]
handle = "ilyasut"
tags   = ["researcher"]
notes  = "Ilya Sutskever"

[[watch]]
handle = "arankomatsuzaki"
tags   = ["researcher", "aggregator"]
notes  = "Aran Komatsuzaki — paper aggregator"

[[watch]]
handle = "_akhaliq"
tags   = ["aggregator"]
notes  = "AK — arxiv daily summaries"

[[watch]]
handle = "huggingface"
tags   = ["ai-labs", "first-party"]
notes  = "Hugging Face official"

[[watch]]
handle = "mistralai"
tags   = ["ai-labs", "first-party"]
notes  = "Mistral AI official"

[[watch]]
handle = "xai"
tags   = ["ai-labs", "first-party"]
notes  = "xAI official"

[[watch]]
handle = "alibaba_qwen"
tags   = ["ai-labs", "first-party"]
notes  = "Qwen team"

[[watch]]
handle = "deepseek_ai"
tags   = ["ai-labs", "first-party"]
notes  = "DeepSeek"

[[watch]]
handle = "lmsysorg"
tags   = ["benchmark"]
notes  = "LMSYS Arena"

[[watch]]
handle = "swyx"
tags   = ["commentary"]
notes  = "swyx — industry commentary"

[[watch]]
handle = "simonw"
tags   = ["commentary"]
notes  = "Simon Willison"

[[watch]]
handle = "latentspacepod"
tags   = ["podcast", "commentary"]
notes  = "Latent Space podcast"
```

- [ ] **Step 3: Update `.gitignore`**

Add (if not already present) to the project root `.gitignore`:

```
# Intelligence agent runtime data (6d)
data/intelligence/reports/
```

- [ ] **Step 4: Verify the persona and config parse**

Run:
```bash
uv run python -c "from pathlib import Path; from project0.agents.intelligence import load_intelligence_persona, load_intelligence_config; from project0.intelligence.watchlist import load_watchlist; p = load_intelligence_persona(Path('prompts/intelligence.md')); c = load_intelligence_config(Path('prompts/intelligence.toml')); w = load_watchlist(Path('prompts/intelligence.toml')); print('persona OK:', len(p.core), 'chars in core'); print('config OK:', c.summarizer_model); print('watchlist OK:', len(w), 'handles')"
```
Expected:
```
persona OK: <some number> chars in core
config OK: claude-opus-4-6
watchlist OK: 20 handles
```

- [ ] **Step 5: Commit**

```bash
git add prompts/intelligence.md prompts/intelligence.toml .gitignore
git commit -m "prompts(intelligence): persona + config + seed watchlist

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Composition root wiring in `main.py`

**Goal:** Wire Intelligence into `main.py`: load its persona, config, and watchlist, construct two Anthropic providers (Opus summarizer + Sonnet QA), construct the `TwitterApiIoSource`, construct `Intelligence`, call `register_intelligence`. Ensure the bot poller fans out `TELEGRAM_BOT_TOKEN_INTELLIGENCE` automatically (it already does — `AGENT_SPECS` is unchanged).

**Files:**
- Modify: `src/project0/main.py`
- Modify: `.env.example` (or equivalent) to document the new env var
- Test: Manual verification via the full test suite

- [ ] **Step 1: Read the current `_run` function to find the right insertion point**

Open `src/project0/main.py`. Manager is wired around lines 115–127 (after Google Calendar, before the pulse scheduler section). Intelligence should be wired **after Manager** and **before the `AGENT_SPECS` token sanity check** (currently around line 137).

- [ ] **Step 2: Add the Intelligence wiring block**

Insert the following after the existing Manager wiring block (`register_manager(manager.handle)` line) and before `# Pulse scheduler entries for Manager.`:

```python
    # --- 6d: Intelligence agent -------------------------------------------
    from project0.agents.intelligence import (
        Intelligence,
        load_intelligence_config,
        load_intelligence_persona,
    )
    from project0.agents.registry import register_intelligence
    from project0.intelligence.twitterapi_io import TwitterApiIoSource
    from project0.intelligence.watchlist import load_watchlist

    intelligence_persona = load_intelligence_persona(Path("prompts/intelligence.md"))
    intelligence_cfg = load_intelligence_config(Path("prompts/intelligence.toml"))
    intelligence_watchlist = load_watchlist(Path("prompts/intelligence.toml"))

    twitterapi_key = os.environ.get("TWITTERAPI_IO_API_KEY", "").strip()
    if not twitterapi_key:
        raise RuntimeError(
            "TWITTERAPI_IO_API_KEY not set in environment — required by Intelligence agent (6d)"
        )
    twitter_source = TwitterApiIoSource(api_key=twitterapi_key)

    reports_dir = Path("data/intelligence/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Intelligence uses two LLM providers: Opus for the one-shot summarizer,
    # Sonnet for the agentic Q&A tool-use loop. Both talk to Anthropic
    # directly and do not go through _build_llm_provider (which is driven
    # by env vars and returns a single provider).
    intelligence_llm_summarizer = AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model=intelligence_cfg.summarizer_model,
    )
    intelligence_llm_qa = AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model=intelligence_cfg.qa_model,
    )

    intelligence = Intelligence(
        llm_summarizer=intelligence_llm_summarizer,
        llm_qa=intelligence_llm_qa,
        twitter=twitter_source,
        messages_store=store.messages(),
        persona=intelligence_persona,
        config=intelligence_cfg,
        watchlist=intelligence_watchlist,
        reports_dir=reports_dir,
        user_tz=settings.user_tz,
    )
    register_intelligence(intelligence.handle)
    log.info(
        "intelligence registered (summarizer=%s, qa=%s, watchlist=%d)",
        intelligence_cfg.summarizer_model,
        intelligence_cfg.qa_model,
        len(intelligence_watchlist),
    )
```

- [ ] **Step 3: Run the full test suite to verify composition-root code still imports cleanly**

Run: `uv run pytest -q`
Expected: every test PASSES. The wiring block lives inside `_run` and is only hit at actual startup, so unit tests don't care about the missing env var.

- [ ] **Step 4: Do a dry-run startup check (optional, requires real env vars)**

If you have `ANTHROPIC_API_KEY`, `TWITTERAPI_IO_API_KEY`, and the bot tokens set, run:
```bash
uv run python -m project0.main
```
Expected: log output shows `intelligence registered (...)` alongside manager and secretary, then the bot pollers start. Cancel with `Ctrl+C` once you see the polling logs. If `TWITTERAPI_IO_API_KEY` is missing, startup fails with a clear `RuntimeError`.

- [ ] **Step 5: Document the new env var**

If there's an `.env.example` or equivalent file at the project root, add:

```
# twitterapi.io API key for Intelligence agent daily reports (6d)
TWITTERAPI_IO_API_KEY=
```

If there's no `.env.example`, create a one-line mention in `README.md` or add the variable to whatever the project uses for env-var documentation. Skip this step if the project has no such documentation file.

- [ ] **Step 6: Commit**

```bash
git add src/project0/main.py .env.example README.md
git commit -m "feat(main): wire Intelligence agent into composition root

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

(Only include files that actually changed in the commit.)

---

## Task 15: Final integration — full test sweep + manual smoke

**Goal:** Confirm every test in the project passes after 6d lands, and run one manual smoke via the live twitterapi.io test to verify the real HTTP integration works.

**Files:** None created/modified — this is a verification task.

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest -v`
Expected: every test PASSES. Count: the 6d plan adds ~11 new test files covering source types, fake source, twitterapi_io (mocked), watchlist, report schema, generate pipeline, summarizer prompts, Intelligence persona, Intelligence config, Intelligence tool dispatch, Intelligence class, register_intelligence, and the shared tool loop. Existing Manager/Secretary/pulse/orchestrator tests all remain green.

- [ ] **Step 2: Confirm no stray references to `intelligence_stub` remain**

Run: `uv run python -c "from project0.agents import intelligence; print(hasattr(intelligence, 'intelligence_stub'))"`
Expected: `False`.

- [ ] **Step 3: Run the optional twitterapi.io live smoke test**

If you have `TWITTERAPI_IO_API_KEY` in your environment:

Run: `TWITTERAPI_IO_API_KEY=<your-key> uv run pytest tests/intelligence/test_twitterapi_io_live.py -v`
Expected: one test PASSES, fetching recent tweets from `@sama`. If it fails with HTTP 4xx, check the API key and twitterapi.io's current endpoint shape (the path or field names in `twitterapi_io.py` may have drifted).

- [ ] **Step 4: Manual end-to-end generation smoke**

With `TWITTERAPI_IO_API_KEY` and `ANTHROPIC_API_KEY` set, run a one-off script that drives `generate_daily_report` directly without touching Telegram:

```bash
uv run python -c "
import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from project0.agents.intelligence import load_intelligence_config
from project0.config import load_settings
from project0.intelligence.generate import generate_daily_report
from project0.intelligence.twitterapi_io import TwitterApiIoSource
from project0.intelligence.watchlist import load_watchlist
from project0.llm.provider import AnthropicProvider

async def main():
    settings = load_settings()
    cfg = load_intelligence_config(Path('prompts/intelligence.toml'))
    watchlist = load_watchlist(Path('prompts/intelligence.toml'))
    import os
    twitter = TwitterApiIoSource(api_key=os.environ['TWITTERAPI_IO_API_KEY'])
    llm = AnthropicProvider(api_key=settings.anthropic_api_key, model=cfg.summarizer_model)
    reports_dir = Path('data/intelligence/reports')
    reports_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = await generate_daily_report(
            date=datetime.now(settings.user_tz).date(),
            source=twitter,
            llm=llm,
            summarizer_model=cfg.summarizer_model,
            summarizer_max_tokens=cfg.summarizer_max_tokens,
            watchlist=watchlist,
            reports_dir=reports_dir,
            user_tz=settings.user_tz,
            timeline_since_hours=cfg.timeline_since_hours,
            max_tweets_per_handle=cfg.max_tweets_per_handle,
        )
        print(f'OK: {len(report[\"news_items\"])} items, {report[\"stats\"][\"tweets_fetched\"]} tweets')
    finally:
        await twitter.aclose()

asyncio.run(main())
"
```

Expected:
- Log output showing each handle being fetched
- One Opus call (slow — 30–90 seconds)
- `OK: <N> items, <M> tweets` printed at the end
- A new file at `data/intelligence/reports/YYYY-MM-DD.json`

Run `cat data/intelligence/reports/YYYY-MM-DD.json | jq '.news_items[] | {headline, importance}'` to sanity-check the output.

- [ ] **Step 5: Manual Telegram smoke (optional)**

Start the bot with `uv run python -m project0.main`, then DM `@<your-intelligence-bot>` on Telegram:
1. Send `生成今天的报告` → expect a short ack after 30–90 seconds.
2. Send `今天最重要的是什么？` → expect a text reply citing a news item URL from the just-generated report.
3. Send `昨天有什么` → if yesterday's file doesn't exist, expect `最新日报是 YYYY-MM-DD...` or similar graceful response.

Stop the process with `Ctrl+C` when done.

- [ ] **Step 6: No commit needed**

Task 15 is verification-only. If any of the smoke tests reveal a bug, fix it in a fresh commit referencing the failing task/file; otherwise this task ends with a clean working tree.

---

## Done

All 15 tasks complete.

**What landed:**

- `src/project0/intelligence/` package: `source.py`, `fake_source.py`, `twitterapi_io.py`, `watchlist.py`, `report.py`, `summarizer_prompt.py`, `generate.py`
- `src/project0/agents/_tool_loop.py` — shared agentic tool-use loop used by Manager and Intelligence
- `src/project0/agents/intelligence.py` — full rewrite: `IntelligencePersona`, `IntelligenceConfig`, `Intelligence` class with four tools, chat and delegated turn entry points
- `src/project0/agents/manager.py` — refactored to use the shared tool loop
- `src/project0/agents/registry.py` — `register_intelligence` symmetric with `register_manager`; `intelligence_stub` removed
- `src/project0/main.py` — composition-root wiring for Intelligence with two Anthropic providers (Opus summarizer + Sonnet Q&A)
- `prompts/intelligence.md` — five-section Chinese persona
- `prompts/intelligence.toml` — LLM + context + twitter config + 20-handle seed watchlist
- 11 new test files covering every module plus an optional live smoke test gated on `TWITTERAPI_IO_API_KEY`

**What was deferred (by design):**

- Pulse-driven daily reports (6g)
- Ad-hoc watch pulses (6g)
- Email / webpage delivery surface (6e)
- Cross-report retrieval, 7-day topic memory, deep topic chat, follow-up web search, extended thinking on the summarizer (6f)
- Dynamic follow/unfollow tooling, feedback loop, per-entry preference learning (6h)

**Cost ceiling (measured estimate):** ≤$35/month at once-daily cadence. Twitter fetch ~$5/month, Opus summarizer ~$30/month. Q&A is cheap per-turn.

**Spec:** `docs/superpowers/specs/2026-04-15-intelligence-agent-design.md`

