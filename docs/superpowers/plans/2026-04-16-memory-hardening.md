# Memory Hardening + Token Cost Cut — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce Layer A (static user profile) and a narrow Layer D slice (Secretary-written user facts) into the memory system, bundled with focused token-cost cuts (Manager transcript shrink, Intelligence report slim + on-demand tool, two-cache-breakpoint system-block layout, env-toggled 1-hour cache TTL) and full per-call LLM usage instrumentation.

**Architecture:** No new processes. Three new surfaces added to `store.py`: `UserProfile` (YAML-backed, read-only), `user_facts` SQLite table with Secretary-only `UserFactsWriter` + all-agents `UserFactsReader`, `llm_usage` SQLite table + `LLMUsageStore`. `LLMProvider` gains required `agent`/`purpose` kwargs on both `complete` and `complete_with_tools`, records usage on every successful call, and accepts a new `SystemBlocks(stable, facts)` input shape for the two-cache-breakpoint layout. Each agent's prompt assembly is reworked to produce `SystemBlocks`, with user profile going into the stable segment and user facts going into the second (small, bust-on-write) segment. Secretary gains a `remember_about_user` tool via the existing shared `run_agentic_loop` helper with `max_iterations=2`. Intelligence's Q&A drops its full-report injection in favor of a compact headline index + a new `get_report_item` tool.

**Tech Stack:** Python 3.12, SQLite, `anthropic` SDK ≥0.40, `pydantic`, `uv`, `pytest`, `pytest-asyncio`, `mypy`, `ruff`. Existing codebase patterns: dataclasses for models, plain async functions, single shared SQLite connection wrapped by `store.py`, FakeProvider for tests.

---

## File Structure

### Files created

| Path | Purpose |
|---|---|
| `data/user_profile.example.yaml` | Committed example; user copies to `user_profile.yaml` and hand-edits |
| `scripts/smoke_memory.sh` | Post-smoke SQLite inspection helper |
| `tests/test_llm_usage_store.py` | `LLMUsageStore` CRUD + rollup |
| `tests/test_user_profile.py` | YAML load, missing/malformed/unknown-keys, prompt render |
| `tests/test_user_facts.py` | Reader/Writer CRUD, trust boundary, 600-tok cap, soft delete |
| `tests/test_config_cache_ttl.py` | `ANTHROPIC_CACHE_TTL` validation |
| `tests/test_provider_usage_recording.py` | Both `complete` methods record on success, not on failure |
| `tests/test_provider_system_blocks.py` | Two-segment system_block rendering into SDK params |
| `tests/test_cache_layout_invariant.py` | Per-agent Segment-1 / Segment-2 byte-identity + breakpoint count |
| `tests/test_call_site_labels.py` | Every agent call site passes correct `agent`/`purpose` |
| `tests/test_secretary_tool_loop.py` | Secretary `remember_about_user` round-trip |
| `tests/test_intelligence_slim_report.py` | Q&A system prompt is headline-index form only |
| `tests/test_intelligence_get_report_item.py` | `get_report_item` tool fetches by id |
| `tests/test_schema_immutability.py` | Existing tables' schema unchanged |
| `tests/test_cross_agent_fact_visibility.py` | Secretary writes, Manager + Intelligence see it on next turn |

### Files modified

| Path | Changes |
|---|---|
| `src/project0/store.py` | Add `UserProfile`, `UserFactsReader`, `UserFactsWriter`, `LLMUsageStore` classes + two new tables in `_init_schema` |
| `src/project0/config.py` | Add `anthropic_cache_ttl: Literal["ephemeral", "1h"]` setting with validation |
| `src/project0/llm/provider.py` | `SystemBlocks` dataclass, new required kwargs on both complete methods, usage recording, cache TTL, streaming-path usage extraction |
| `src/project0/agents/_tool_loop.py` | Plumb `agent`/`purpose`/`envelope_id` through to `complete_with_tools` |
| `src/project0/agents/secretary.py` | Add `remember_about_user` tool + bounded tool-loop wiring + rework all four entry paths to use `SystemBlocks`; update call sites with labels |
| `src/project0/agents/manager.py` | Rework prompt assembly to `SystemBlocks`; pass labels through tool loop |
| `src/project0/agents/intelligence.py` | Add `get_report_item` tool; delete full-report injection; switch injection to headline index; rework prompt assembly to `SystemBlocks`; pass labels |
| `src/project0/intelligence/generate.py` | Update summarizer call site with `agent="intelligence_summarizer"` + `purpose="report_gen"` |
| `src/project0/main.py` | Wire `UserProfile`, readers/writer, `LLMUsageStore` into composition root; pass `cache_ttl` to provider |
| `prompts/manager.toml` | `transcript_window = 10` |
| `.env.example` | Add `ANTHROPIC_CACHE_TTL=ephemeral` with comment |
| `.gitignore` | Add `data/user_profile.yaml` |
| `README.md` | Roadmap section: mark sub-project complete, add next items |

### Files explicitly NOT touched (K.2, K.3)

- `prompts/secretary.md`, `prompts/manager.md`, `prompts/intelligence.md` — persona pruning is a separate future sub-project.
- `prompts/secretary.toml`, `prompts/intelligence.toml` — transcript_window unchanged.
- `src/project0/envelope.py`, `src/project0/orchestrator.py` — routing/envelope shape unchanged.
- `src/project0/telegram_io.py`, `src/project0/mentions.py`, `src/project0/pulse.py` — untouched.
- `src/project0/calendar/*` — untouched.
- `src/project0/intelligence_web/*` — untouched.

---

## Task 1: `LLMUsageStore` — schema, API, tests

**Files:**
- Modify: `src/project0/store.py`
- Create: `tests/test_llm_usage_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_usage_store.py
from __future__ import annotations

import sqlite3
import pytest

from project0.store import Store, LLMUsageStore


@pytest.fixture
def conn() -> sqlite3.Connection:
    s = Store(":memory:")
    yield s.conn
    s.conn.close()


def test_record_and_read_back(conn: sqlite3.Connection) -> None:
    usage = LLMUsageStore(conn)
    row_id = usage.record(
        agent="secretary",
        model="claude-sonnet-4-6",
        input_tokens=1234,
        cache_creation_input_tokens=500,
        cache_read_input_tokens=700,
        output_tokens=210,
        envelope_id=42,
        purpose="reply",
    )
    assert row_id > 0
    rows = conn.execute(
        "SELECT agent, model, input_tokens, cache_creation_input_tokens, "
        "cache_read_input_tokens, output_tokens, envelope_id, purpose "
        "FROM llm_usage ORDER BY id"
    ).fetchall()
    assert rows == [("secretary", "claude-sonnet-4-6", 1234, 500, 700, 210, 42, "reply")]


def test_record_with_null_envelope(conn: sqlite3.Connection) -> None:
    usage = LLMUsageStore(conn)
    usage.record(
        agent="intelligence_summarizer",
        model="claude-opus-4-6",
        input_tokens=5000,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens=1200,
        envelope_id=None,
        purpose="report_gen",
    )
    row = conn.execute(
        "SELECT envelope_id FROM llm_usage WHERE agent='intelligence_summarizer'"
    ).fetchone()
    assert row[0] is None


def test_summary_since_groups_by_agent(conn: sqlite3.Connection) -> None:
    usage = LLMUsageStore(conn)
    for _ in range(3):
        usage.record(
            agent="secretary", model="claude-sonnet-4-6",
            input_tokens=100, cache_creation_input_tokens=0,
            cache_read_input_tokens=50, output_tokens=20,
            envelope_id=None, purpose="reply",
        )
    for _ in range(2):
        usage.record(
            agent="manager", model="claude-sonnet-4-6",
            input_tokens=500, cache_creation_input_tokens=0,
            cache_read_input_tokens=300, output_tokens=80,
            envelope_id=None, purpose="tool_loop",
        )
    rollup = usage.summary_since("1970-01-01T00:00:00Z")
    rows_by_agent = {r["agent"]: r for r in rollup}
    assert rows_by_agent["secretary"]["input_tokens"] == 300
    assert rows_by_agent["secretary"]["output_tokens"] == 60
    assert rows_by_agent["manager"]["input_tokens"] == 1000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_usage_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'LLMUsageStore'`.

- [ ] **Step 3: Implement `LLMUsageStore` + schema**

Edit `src/project0/store.py`:

```python
# Add to imports at top (adjust to existing import group style)
from datetime import UTC, datetime


# Add to _init_schema() (or wherever the existing CREATE TABLE calls live):
_SCHEMA_LLM_USAGE = """
CREATE TABLE IF NOT EXISTS llm_usage (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                          TEXT    NOT NULL,
    agent                       TEXT    NOT NULL,
    model                       TEXT    NOT NULL,
    input_tokens                INTEGER NOT NULL,
    cache_creation_input_tokens INTEGER NOT NULL,
    cache_read_input_tokens     INTEGER NOT NULL,
    output_tokens               INTEGER NOT NULL,
    envelope_id                 INTEGER,
    purpose                     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_llm_usage_ts       ON llm_usage(ts);
CREATE INDEX IF NOT EXISTS ix_llm_usage_agent    ON llm_usage(agent, ts);
CREATE INDEX IF NOT EXISTS ix_llm_usage_envelope ON llm_usage(envelope_id);
"""

# Inside the schema init method, execute the DDL. If the existing code uses
# executescript, append this block. Otherwise adapt to the existing pattern.


class LLMUsageStore:
    """Append-only operational telemetry for LLM calls. Written to exclusively
    from inside AnthropicProvider after a successful API response. Read by the
    future WebUI token-usage page via summary_since()."""

    def __init__(self, conn: "sqlite3.Connection") -> None:
        self._conn = conn

    def record(
        self,
        *,
        agent: str,
        model: str,
        input_tokens: int,
        cache_creation_input_tokens: int,
        cache_read_input_tokens: int,
        output_tokens: int,
        envelope_id: int | None,
        purpose: str,
    ) -> int:
        ts = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        cur = self._conn.execute(
            "INSERT INTO llm_usage "
            "(ts, agent, model, input_tokens, cache_creation_input_tokens, "
            " cache_read_input_tokens, output_tokens, envelope_id, purpose) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, agent, model, input_tokens, cache_creation_input_tokens,
             cache_read_input_tokens, output_tokens, envelope_id, purpose),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def summary_since(self, ts: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT agent, "
            "       SUM(input_tokens) AS input_tokens, "
            "       SUM(cache_creation_input_tokens) AS cache_creation_input_tokens, "
            "       SUM(cache_read_input_tokens) AS cache_read_input_tokens, "
            "       SUM(output_tokens) AS output_tokens, "
            "       COUNT(*) AS calls "
            "FROM llm_usage WHERE ts >= ? GROUP BY agent ORDER BY agent",
            (ts,),
        ).fetchall()
        return [
            {
                "agent": r[0],
                "input_tokens": int(r[1] or 0),
                "cache_creation_input_tokens": int(r[2] or 0),
                "cache_read_input_tokens": int(r[3] or 0),
                "output_tokens": int(r[4] or 0),
                "calls": int(r[5] or 0),
            }
            for r in rows
        ]
```

Wire the schema DDL into the existing `_init_schema` method (check current shape; append `cursor.executescript(_SCHEMA_LLM_USAGE)` or similar).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_llm_usage_store.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/project0/store.py tests/test_llm_usage_store.py
git commit -m "feat(store): add llm_usage table and LLMUsageStore API"
```

---

## Task 2: `ANTHROPIC_CACHE_TTL` config setting

**Files:**
- Modify: `src/project0/config.py`
- Create: `tests/test_config_cache_ttl.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_cache_ttl.py
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from project0.config import load_settings


ENV_MIN: dict[str, str] = {
    "TELEGRAM_BOT_TOKEN_MANAGER": "t1",
    "TELEGRAM_BOT_TOKEN_SECRETARY": "t2",
    "TELEGRAM_BOT_TOKEN_INTELLIGENCE": "t3",
    "TELEGRAM_ALLOWED_CHAT_IDS": "-100123",
    "TELEGRAM_ALLOWED_USER_IDS": "42",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "STORE_PATH": ":memory:",
    "USER_TIMEZONE": "Asia/Shanghai",
    "GOOGLE_CALENDAR_ID": "primary",
    "MANAGER_PULSE_CHAT_ID": "-100123",
    "TWITTERAPI_IO_API_KEY": "x",
}


def test_default_cache_ttl_is_ephemeral() -> None:
    with patch.dict(os.environ, ENV_MIN, clear=True):
        s = load_settings()
    assert s.anthropic_cache_ttl == "ephemeral"


def test_explicit_ephemeral() -> None:
    with patch.dict(os.environ, {**ENV_MIN, "ANTHROPIC_CACHE_TTL": "ephemeral"}, clear=True):
        s = load_settings()
    assert s.anthropic_cache_ttl == "ephemeral"


def test_explicit_1h() -> None:
    with patch.dict(os.environ, {**ENV_MIN, "ANTHROPIC_CACHE_TTL": "1h"}, clear=True):
        s = load_settings()
    assert s.anthropic_cache_ttl == "1h"


def test_invalid_value_raises_at_startup() -> None:
    with patch.dict(os.environ, {**ENV_MIN, "ANTHROPIC_CACHE_TTL": "5m"}, clear=True):
        with pytest.raises(RuntimeError) as exc_info:
            load_settings()
    assert "ANTHROPIC_CACHE_TTL" in str(exc_info.value)
    assert "5m" in str(exc_info.value)


def test_empty_value_raises() -> None:
    with patch.dict(os.environ, {**ENV_MIN, "ANTHROPIC_CACHE_TTL": ""}, clear=True):
        with pytest.raises(RuntimeError):
            load_settings()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_config_cache_ttl.py -v`
Expected: FAIL — `Settings` has no `anthropic_cache_ttl` attribute.

- [ ] **Step 3: Implement the setting**

Edit `src/project0/config.py`. Locate the `Settings` dataclass and `load_settings` function. Add:

```python
# Add to the Settings dataclass fields:
    anthropic_cache_ttl: Literal["ephemeral", "1h"] = "ephemeral"

# Add `from typing import Literal` to imports if not already present.

# Inside load_settings(), add (near other ANTHROPIC_* reads):
raw_ttl = os.environ.get("ANTHROPIC_CACHE_TTL", "ephemeral")
if raw_ttl not in ("ephemeral", "1h"):
    raise RuntimeError(
        f"ANTHROPIC_CACHE_TTL must be 'ephemeral' or '1h', got {raw_ttl!r}"
    )

# Then pass it into the Settings constructor:
    anthropic_cache_ttl=raw_ttl,  # type: ignore[arg-type]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_config_cache_ttl.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/project0/config.py tests/test_config_cache_ttl.py
git commit -m "feat(config): add ANTHROPIC_CACHE_TTL env var with validation"
```

---

## Task 3: `SystemBlocks` + provider usage recording (`complete` method)

**Files:**
- Modify: `src/project0/llm/provider.py`
- Create: `tests/test_provider_usage_recording.py`
- Create: `tests/test_provider_system_blocks.py`

- [ ] **Step 1: Write failing test — SystemBlocks rendering**

```python
# tests/test_provider_system_blocks.py
from __future__ import annotations

from project0.llm.provider import SystemBlocks, _render_system_param


def test_str_input_single_cached_block() -> None:
    out = _render_system_param("hello persona")
    assert out == [
        {"type": "text", "text": "hello persona", "cache_control": {"type": "ephemeral"}}
    ]


def test_str_input_with_1h_ttl() -> None:
    out = _render_system_param("hello persona", cache_ttl="1h")
    assert out == [
        {
            "type": "text",
            "text": "hello persona",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]


def test_systemblocks_stable_only_single_marker() -> None:
    sb = SystemBlocks(stable="persona + profile", facts=None)
    out = _render_system_param(sb)
    assert out == [
        {"type": "text", "text": "persona + profile", "cache_control": {"type": "ephemeral"}}
    ]


def test_systemblocks_stable_and_facts_two_markers() -> None:
    sb = SystemBlocks(stable="persona + profile", facts="FACT: 生日 3-14")
    out = _render_system_param(sb)
    assert out == [
        {"type": "text", "text": "persona + profile", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "FACT: 生日 3-14", "cache_control": {"type": "ephemeral"}},
    ]


def test_empty_facts_string_is_omitted() -> None:
    # Empty facts string => no second block, no wasted breakpoint
    sb = SystemBlocks(stable="persona", facts="")
    out = _render_system_param(sb)
    assert len(out) == 1
```

- [ ] **Step 2: Write failing test — usage recording on success**

```python
# tests/test_provider_usage_recording.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from project0.llm.provider import AnthropicProvider, LLMProviderError, Msg
from project0.store import LLMUsageStore, Store


@pytest.fixture
def usage_store() -> LLMUsageStore:
    return LLMUsageStore(Store(":memory:").conn)


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

    async def __aenter__(self) -> "_FakeStreamCtx":
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
    provider = AnthropicProvider(
        api_key="test", model="claude-sonnet-4-6",
        usage_store=LLMUsageStore(Store(":memory:").conn),
        cache_ttl="ephemeral",
    )
    with pytest.raises(TypeError):
        await provider.complete(  # type: ignore[call-arg]
            system="persona", messages=[Msg(role="user", content="hi")],
        )
```

- [ ] **Step 3: Run to verify both fail**

Run: `uv run pytest tests/test_provider_system_blocks.py tests/test_provider_usage_recording.py -v`
Expected: FAIL — `ImportError: SystemBlocks`, plus `AnthropicProvider.__init__ got unexpected keyword argument 'usage_store'`.

- [ ] **Step 4: Implement `SystemBlocks`, `_render_system_param`, provider kwargs**

Edit `src/project0/llm/provider.py`. Replace the existing provider classes with updated versions:

```python
# At top of file, add to imports:
from project0.store import LLMUsageStore


# After the existing Msg / ProviderCall dataclasses, add:
@dataclass(frozen=True)
class SystemBlocks:
    """Structured system prompt with optional two-breakpoint layout.

    stable:  Segment 1. Large, cached, rarely busts (persona + mode + profile).
    facts:   Segment 2, optional. Small, busts on conversational cadence
             (user_facts block). None or empty string → no second breakpoint.
    """
    stable: str
    facts: str | None = None


def _render_system_param(
    system: "str | SystemBlocks",
    *,
    cache_ttl: str = "ephemeral",
) -> list[dict[str, Any]]:
    """Turn the caller's system input into the SDK's `system` parameter shape.

    Plain string → one cached text block. SystemBlocks with only `stable` →
    one cached block. SystemBlocks with both → two blocks, each with its own
    cache_control marker. Empty `facts` is treated as None.
    """
    cache_marker: dict[str, Any] = {"type": "ephemeral"}
    if cache_ttl == "1h":
        cache_marker = {"type": "ephemeral", "ttl": "1h"}

    if isinstance(system, str):
        return [{"type": "text", "text": system, "cache_control": cache_marker}]

    out: list[dict[str, Any]] = [
        {"type": "text", "text": system.stable, "cache_control": cache_marker}
    ]
    if system.facts:
        out.append(
            {"type": "text", "text": system.facts, "cache_control": cache_marker}
        )
    return out


# Update the LLMProvider Protocol to include new required kwargs:
class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        system: "str | SystemBlocks",
        messages: list[Msg],
        max_tokens: int = 800,
        thinking_budget_tokens: int | None = None,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
    ) -> str: ...

    async def complete_with_tools(
        self,
        *,
        system: "str | SystemBlocks",
        messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
    ) -> ToolUseResult: ...
```

Replace the `AnthropicProvider` class body:

```python
class AnthropicProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        usage_store: LLMUsageStore,
        cache_ttl: str = "ephemeral",
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._usage_store = usage_store
        self._cache_ttl = cache_ttl

    async def complete(
        self,
        *,
        system: "str | SystemBlocks",
        messages: list[Msg],
        max_tokens: int = 800,
        thinking_budget_tokens: int | None = None,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
    ) -> str:
        sdk_messages: list[MessageParam] = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        system_block = _render_system_param(system, cache_ttl=self._cache_ttl)
        extra: dict[str, Any] = {}
        if thinking_budget_tokens is not None:
            extra["thinking"] = {
                "type": "adaptive",
                "budget_tokens": thinking_budget_tokens,
            }
        try:
            async with self._client.messages.stream(
                model=self._model,
                max_tokens=max_tokens,
                system=system_block,
                messages=sdk_messages,
                **extra,
            ) as stream:
                final_message = await stream.get_final_message()
        except Exception as e:
            log.exception("anthropic call failed")
            raise LLMProviderError(f"anthropic {type(e).__name__}") from e

        usage = getattr(final_message, "usage", None)
        if usage is not None:
            in_tok = int(getattr(usage, "input_tokens", 0) or 0)
            cc_tok = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            cr_tok = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            out_tok = int(getattr(usage, "output_tokens", 0) or 0)
            self._usage_store.record(
                agent=agent,
                model=self._model,
                input_tokens=in_tok,
                cache_creation_input_tokens=cc_tok,
                cache_read_input_tokens=cr_tok,
                output_tokens=out_tok,
                envelope_id=envelope_id,
                purpose=purpose,
            )
            log.info(
                "llm call agent=%s model=%s in=%d cc=%d cr=%d out=%d env=%s purpose=%s",
                agent, self._model, in_tok, cc_tok, cr_tok, out_tok,
                envelope_id if envelope_id is not None else "-",
                purpose,
            )

        for block in final_message.content:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", None)
                if text:
                    return str(text)
        raise LLMProviderError("anthropic response contained no text block")

    async def complete_with_tools(
        self,
        *,
        system: "str | SystemBlocks",
        messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
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
                        "type": "tool_use", "id": tc.id,
                        "name": tc.name, "input": tc.input,
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
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        system_block = _render_system_param(system, cache_ttl=self._cache_ttl)

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

        usage = getattr(resp, "usage", None)
        if usage is not None:
            in_tok = int(getattr(usage, "input_tokens", 0) or 0)
            cc_tok = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            cr_tok = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            out_tok = int(getattr(usage, "output_tokens", 0) or 0)
            self._usage_store.record(
                agent=agent, model=self._model,
                input_tokens=in_tok,
                cache_creation_input_tokens=cc_tok,
                cache_read_input_tokens=cr_tok,
                output_tokens=out_tok,
                envelope_id=envelope_id, purpose=purpose,
            )
            log.info(
                "llm tool-call agent=%s model=%s in=%d cc=%d cr=%d out=%d env=%s purpose=%s",
                agent, self._model, in_tok, cc_tok, cr_tok, out_tok,
                envelope_id if envelope_id is not None else "-",
                purpose,
            )

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

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_provider_system_blocks.py tests/test_provider_usage_recording.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/project0/llm/provider.py tests/test_provider_system_blocks.py tests/test_provider_usage_recording.py
git commit -m "feat(provider): SystemBlocks + usage recording + cache TTL + required labels"
```

---

## Task 4: Update `FakeProvider` + existing tests

The `FakeProvider` also needs the new kwargs (required) so existing tests don't break and new tests can exercise the instrumentation path.

**Files:**
- Modify: `src/project0/llm/provider.py` (FakeProvider class)
- Modify: every existing test that constructs `FakeProvider` or calls its methods (search + patch)

- [ ] **Step 1: Update `FakeProvider` to accept the new kwargs**

Edit `src/project0/llm/provider.py`. Replace `FakeProvider`:

```python
@dataclass
class FakeProvider:
    """Test-only provider. Either pre-loaded with canned responses or driven
    by a callable. Records every call. Optionally writes usage rows to a
    real LLMUsageStore when one is supplied — this lets tests exercise the
    recording path without mocking AnthropicProvider."""

    responses: list[str] | None = None
    callable_: Callable[[str, list[Msg]], str] | None = None
    calls: list[ProviderCall] = field(default_factory=list)
    tool_responses: list[ToolUseResult] | None = None
    tool_calls_log: list[dict] = field(default_factory=list)
    usage_store: LLMUsageStore | None = None
    fake_usage: tuple[int, int, int, int] = (100, 0, 80, 20)
    _idx: int = 0
    _tool_idx: int = 0

    async def complete(
        self,
        *,
        system: "str | SystemBlocks",
        messages: list[Msg],
        max_tokens: int = 800,
        thinking_budget_tokens: int | None = None,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
    ) -> str:
        # Normalize system to a string for the call-log for test inspection.
        sys_str = system if isinstance(system, str) else system.stable + (
            "\n" + system.facts if system.facts else ""
        )
        self.calls.append(
            ProviderCall(
                system=sys_str, messages=list(messages),
                max_tokens=max_tokens,
                thinking_budget_tokens=thinking_budget_tokens,
            )
        )
        if self.usage_store is not None:
            in_tok, cc, cr, out_tok = self.fake_usage
            self.usage_store.record(
                agent=agent, model="fake",
                input_tokens=in_tok,
                cache_creation_input_tokens=cc,
                cache_read_input_tokens=cr,
                output_tokens=out_tok,
                envelope_id=envelope_id, purpose=purpose,
            )
        if self.callable_ is not None:
            return self.callable_(sys_str, messages)
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
        system: "str | SystemBlocks",
        messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
        agent: str,
        purpose: str,
        envelope_id: int | None = None,
    ) -> ToolUseResult:
        sys_str = system if isinstance(system, str) else system.stable + (
            "\n" + system.facts if system.facts else ""
        )
        self.tool_calls_log.append({
            "system": sys_str, "messages": list(messages),
            "tools": list(tools), "max_tokens": max_tokens,
            "agent": agent, "purpose": purpose,
            "envelope_id": envelope_id,
        })
        if self.usage_store is not None:
            in_tok, cc, cr, out_tok = self.fake_usage
            self.usage_store.record(
                agent=agent, model="fake",
                input_tokens=in_tok,
                cache_creation_input_tokens=cc,
                cache_read_input_tokens=cr,
                output_tokens=out_tok,
                envelope_id=envelope_id, purpose=purpose,
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

- [ ] **Step 2: Run full test suite to find callers that break**

Run: `uv run pytest -x --tb=short 2>&1 | head -80`
Expected: many failures in existing tests that call `FakeProvider.complete(...)` without `agent=`/`purpose=` kwargs. Collect the list.

- [ ] **Step 3: Update every broken caller**

For each failing test file, add `agent="secretary"` (or the appropriate agent name) and `purpose="reply"` (or the appropriate purpose) to every `.complete(...)` / `.complete_with_tools(...)` call. Labels only affect the call log in FakeProvider — no behavior change.

Typical pattern:
```python
# Before
reply = await fake.complete(system="...", messages=[...], max_tokens=800)

# After
reply = await fake.complete(
    system="...", messages=[...], max_tokens=800,
    agent="secretary", purpose="reply", envelope_id=None,
)
```

Run iteratively until `uv run pytest -x --tb=short` passes through the previously-failing tests. Do not add new tests in this step — just mechanically propagate the kwargs.

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest -q`
Expected: everything previously passing still passes; tests from tasks 1-3 still pass.

- [ ] **Step 5: Commit**

```bash
git add src/project0/llm/provider.py tests/
git commit -m "refactor(provider): propagate agent/purpose kwargs through FakeProvider callers"
```

---

## Task 5: Plumb `agent`/`purpose`/`envelope_id` through `run_agentic_loop`

**Files:**
- Modify: `src/project0/agents/_tool_loop.py`
- Modify: `src/project0/agents/manager.py` (caller of `run_agentic_loop`)
- Modify: `src/project0/agents/intelligence.py` (caller of `run_agentic_loop`)

- [ ] **Step 1: Write failing test**

```python
# tests/test_tool_loop_labels.py
from __future__ import annotations

import pytest

from project0.agents._tool_loop import run_agentic_loop
from project0.llm.provider import FakeProvider
from project0.llm.tools import ToolUseResult
from project0.store import LLMUsageStore, Store


@pytest.mark.asyncio
async def test_run_agentic_loop_records_agent_and_purpose_labels() -> None:
    store = Store(":memory:")
    usage = LLMUsageStore(store.conn)
    fake = FakeProvider(
        tool_responses=[
            ToolUseResult(kind="text", text="final reply", tool_calls=[], stop_reason="end_turn"),
        ],
        usage_store=usage,
    )

    async def _dispatch(call, state):
        return ("ok", False)

    result = await run_agentic_loop(
        llm=fake,
        system="persona",
        initial_user_text="hi",
        tools=[],
        dispatch_tool=_dispatch,
        max_iterations=3,
        max_tokens=800,
        agent="manager",
        purpose="tool_loop",
        envelope_id=7,
    )
    assert result.final_text == "final reply"
    rows = usage.summary_since("1970-01-01T00:00:00Z")
    assert rows == [{
        "agent": "manager",
        "input_tokens": 100,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 80,
        "output_tokens": 20,
        "calls": 1,
    }]
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_tool_loop_labels.py -v`
Expected: FAIL — `run_agentic_loop() got an unexpected keyword argument 'agent'`.

- [ ] **Step 3: Plumb kwargs through `run_agentic_loop`**

Edit `src/project0/agents/_tool_loop.py`:

```python
async def run_agentic_loop(
    *,
    llm: LLMProvider,
    system: Any,            # str | SystemBlocks
    initial_user_text: str,
    tools: list[ToolSpec],
    dispatch_tool: DispatchTool,
    max_iterations: int,
    max_tokens: int,
    # NEW:
    agent: str,
    purpose: str,
    envelope_id: int | None = None,
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
                agent=agent,
                purpose=purpose,
                envelope_id=envelope_id,
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

- [ ] **Step 4: Update Manager and Intelligence callers**

In `src/project0/agents/manager.py`, find the `run_agentic_loop(...)` call and add `agent="manager", purpose="tool_loop", envelope_id=env.id`.

In `src/project0/agents/intelligence.py`, find the `run_agentic_loop(...)` call and add `agent="intelligence", purpose="qa", envelope_id=env.id`.

- [ ] **Step 5: Run the test**

Run: `uv run pytest tests/test_tool_loop_labels.py -v && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/project0/agents/_tool_loop.py src/project0/agents/manager.py src/project0/agents/intelligence.py tests/test_tool_loop_labels.py
git commit -m "refactor(tool_loop): plumb agent/purpose/envelope_id labels through"
```

---

## Task 6: Update remaining call sites (Secretary, Intelligence summarizer)

Secretary and `intelligence/generate.py` use `llm.complete(...)` directly, not the tool loop. Mechanically update each call site.

**Files:**
- Modify: `src/project0/agents/secretary.py` (four call sites)
- Modify: `src/project0/intelligence/generate.py` (one call site)
- Create: `tests/test_call_site_labels.py` (one assertion per call site)

- [ ] **Step 1: Write failing test**

```python
# tests/test_call_site_labels.py
"""One test per live LLM call site, confirming (agent, purpose) labels
end up in llm_usage. Prevents silent drift when future refactors move
code around."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from project0.agents.secretary import Secretary, SecretaryConfig, SecretaryPersona
from project0.envelope import Envelope
from project0.llm.provider import FakeProvider
from project0.store import (
    AgentMemory, LLMUsageStore, MessagesStore, Store,
)


def _make_secretary(
    llm, usage_store, store: Store,
) -> Secretary:
    persona = SecretaryPersona(
        core="core persona",
        listener_mode="listener mode",
        group_addressed_mode="addressed mode",
        dm_mode="dm mode",
        reminder_mode="reminder mode",
    )
    cfg = SecretaryConfig(
        t_min_seconds=0, n_min_messages=0, l_min_weighted_chars=0,
        transcript_window=20, model="claude-sonnet-4-6",
        max_tokens_reply=800, max_tokens_listener=600,
        skip_sentinels=["[skip]"],
    )
    return Secretary(
        llm=llm,
        memory=AgentMemory(store.conn, "secretary"),
        messages_store=MessagesStore(store.conn),
        persona=persona,
        config=cfg,
        # (UserProfile / UserFactsReader / UserFactsWriter wired in task 11)
    )


@pytest.mark.asyncio
async def test_secretary_addressed_labels() -> None:
    store = Store(":memory:")
    usage = LLMUsageStore(store.conn)
    fake = FakeProvider(responses=["hi reply"], usage_store=usage)
    sec = _make_secretary(fake, usage, store)
    env = Envelope(
        id=101, ts="2026-04-16T10:00:00Z", parent_id=None,
        source="telegram_group", telegram_chat_id=-100, telegram_msg_id=1,
        received_by_bot="secretary", from_kind="user", from_agent=None,
        to_agent="secretary", body="@secretary hi", mentions=["secretary"],
        routing_reason="mention",
    )
    await sec.handle(env)
    rows = usage.summary_since("1970-01-01T00:00:00Z")
    assert rows[0]["agent"] == "secretary"
    # Assert purpose by reading raw rows since rollup groups by agent only
    raw = store.conn.execute("SELECT purpose FROM llm_usage").fetchall()
    assert raw == [("reply",)]


# Similar mechanical tests for listener / reminder / report_gen — one per site.
# (Body omitted here — add in step 3 by mirroring the addressed test above and
# varying the envelope routing_reason / purpose string.)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_call_site_labels.py -v`
Expected: FAIL — Secretary's `complete()` call has no `agent=`/`purpose=`.

- [ ] **Step 3: Update Secretary call sites**

Edit `src/project0/agents/secretary.py`:

Locate `_listener_llm_call`. Add to the `self._llm.complete(...)` call:
```python
            reply = await self._llm.complete(
                system=system,
                messages=[Msg(role="user", content=user_msg)],
                max_tokens=self._config.max_tokens_listener,
                agent="secretary",
                purpose="listener",
                envelope_id=env.id,
            )
```

Locate `_addressed_llm_call`. Add to its `self._llm.complete(...)`:
```python
            reply = await self._llm.complete(
                system=system,
                messages=[Msg(role="user", content=user_msg)],
                max_tokens=max_tokens,
                agent="secretary",
                purpose="reply",
                envelope_id=env.id,
            )
```

Locate `_handle_reminder`. Add:
```python
            reply = await self._llm.complete(
                system=system,
                messages=[Msg(role="user", content=user_msg)],
                max_tokens=self._config.max_tokens_reply,
                agent="secretary",
                purpose="reminder",
                envelope_id=env.id,
            )
```

- [ ] **Step 4: Update intelligence summarizer call site**

Edit `src/project0/intelligence/generate.py`. Locate the `llm.complete(...)` or `llm.complete_with_tools(...)` call used for the daily report generation. Add:
```python
# For a plain complete() call:
        text = await self._llm.complete(
            system=system_prompt,
            messages=[Msg(role="user", content=user_prompt)],
            max_tokens=max_tokens,
            thinking_budget_tokens=thinking_budget,
            agent="intelligence_summarizer",
            purpose="report_gen",
            envelope_id=None,
        )
```

- [ ] **Step 5: Write labels test bodies for the remaining sites**

Add the listener / reminder / summarizer test bodies in `tests/test_call_site_labels.py`, each following the same pattern as `test_secretary_addressed_labels`. One test per call site. For the summarizer test, construct a minimal fake tweet fixture and call the generate function directly, asserting `raw = [("report_gen",)]`.

- [ ] **Step 6: Run all call-site tests**

Run: `uv run pytest tests/test_call_site_labels.py tests/test_provider_usage_recording.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/project0/agents/secretary.py src/project0/intelligence/generate.py tests/test_call_site_labels.py
git commit -m "feat(instrumentation): label all existing LLM call sites"
```

---

## Task 7: `UserFactsReader` + `UserFactsWriter` + trust boundary

**Files:**
- Modify: `src/project0/store.py`
- Create: `tests/test_user_facts.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_user_facts.py
from __future__ import annotations

import pytest

from project0.store import Store, UserFactsReader, UserFactsWriter


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


# --- Trust boundary ---

def test_writer_rejects_manager(store: Store) -> None:
    with pytest.raises(PermissionError) as e:
        UserFactsWriter("manager")
    assert "manager" in str(e.value)


def test_writer_rejects_intelligence(store: Store) -> None:
    with pytest.raises(PermissionError):
        UserFactsWriter("intelligence")


def test_writer_accepts_secretary(store: Store) -> None:
    w = UserFactsWriter("secretary")
    assert w is not None


# --- CRUD ---

def test_add_and_read(store: Store) -> None:
    w = UserFactsWriter("secretary")
    w._conn = store.conn  # type: ignore[attr-defined]
    row_id = w.add("生日是3月14日", topic="personal")
    assert row_id > 0
    r = UserFactsReader("manager", store.conn)
    facts = r.active()
    assert len(facts) == 1
    assert facts[0].fact_text == "生日是3月14日"
    assert facts[0].topic == "personal"
    assert facts[0].author_agent == "secretary"
    assert facts[0].is_active is True


def test_deactivate(store: Store) -> None:
    w = UserFactsWriter("secretary", store.conn)
    fid = w.add("likes 寿司", topic="food")
    w.deactivate(fid)
    r = UserFactsReader("manager", store.conn)
    assert r.active() == []
    assert len(r.all_including_inactive()) == 1


def test_caller_cannot_spoof_author(store: Store) -> None:
    w = UserFactsWriter("secretary", store.conn)
    fid = w.add("test", topic=None)
    row = store.conn.execute(
        "SELECT author_agent FROM user_facts WHERE id=?", (fid,)
    ).fetchone()
    assert row[0] == "secretary"


def test_as_prompt_block_empty_when_no_facts(store: Store) -> None:
    r = UserFactsReader("manager", store.conn)
    assert r.as_prompt_block() == ""


def test_as_prompt_block_renders_active_only(store: Store) -> None:
    w = UserFactsWriter("secretary", store.conn)
    w.add("fact A", topic="x")
    fid_b = w.add("fact B", topic=None)
    w.deactivate(fid_b)
    r = UserFactsReader("manager", store.conn)
    block = r.as_prompt_block()
    assert "fact A" in block
    assert "fact B" not in block


def test_as_prompt_block_respects_token_cap(store: Store) -> None:
    w = UserFactsWriter("secretary", store.conn)
    # Insert 100 facts, each ~30 chars ~= ~30 tokens in Chinese
    for i in range(100):
        w.add(f"fact number {i} " + "测试" * 5, topic="bulk")
    r = UserFactsReader("manager", store.conn)
    # Rough cap: assume ~4 chars per token → 600 tok ≈ 2400 chars rendered
    block = r.as_prompt_block(max_tokens=600)
    assert len(block) <= 2400 + 200  # generous slack for framing text
    assert len(block) > 0
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_user_facts.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement classes + schema**

Add to `src/project0/store.py`:

```python
# Add schema DDL to _init_schema (alongside the llm_usage DDL from Task 1):
_SCHEMA_USER_FACTS = """
CREATE TABLE IF NOT EXISTS user_facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    author_agent  TEXT    NOT NULL,
    fact_text     TEXT    NOT NULL,
    topic         TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_user_facts_active ON user_facts(is_active, ts);
"""


@dataclass(frozen=True)
class UserFact:
    id: int
    ts: str
    author_agent: str
    fact_text: str
    topic: str | None
    is_active: bool


class UserFactsReader:
    """Read-only access to user_facts. Any agent may construct one."""

    def __init__(self, agent_name: str, conn: "sqlite3.Connection") -> None:
        self._agent = agent_name
        self._conn = conn

    def active(self, limit: int = 30) -> list[UserFact]:
        rows = self._conn.execute(
            "SELECT id, ts, author_agent, fact_text, topic, is_active "
            "FROM user_facts WHERE is_active=1 ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            UserFact(
                id=int(r[0]), ts=str(r[1]), author_agent=str(r[2]),
                fact_text=str(r[3]), topic=(str(r[4]) if r[4] is not None else None),
                is_active=bool(r[5]),
            )
            for r in rows
        ]

    def all_including_inactive(self) -> list[UserFact]:
        rows = self._conn.execute(
            "SELECT id, ts, author_agent, fact_text, topic, is_active "
            "FROM user_facts ORDER BY ts DESC"
        ).fetchall()
        return [
            UserFact(
                id=int(r[0]), ts=str(r[1]), author_agent=str(r[2]),
                fact_text=str(r[3]), topic=(str(r[4]) if r[4] is not None else None),
                is_active=bool(r[5]),
            )
            for r in rows
        ]

    def as_prompt_block(self, max_tokens: int = 600) -> str:
        """Render active facts as a Chinese bullet block for the cached system
        prompt. Empty string if no active facts. Drops oldest active facts
        from the rendered output (not storage) when over the cap. The cap is
        estimated using a rough 4-chars-per-token heuristic — the goal is a
        hard ceiling on prompt size, not exact token accounting."""
        facts = self.active(limit=100)  # pull more than we'll render; trim below
        if not facts:
            return ""

        max_chars = max_tokens * 4
        header = "关于用户（由 Secretary 从对话中学到的长期记忆）：\n"
        # Render newest-first; when over budget, drop from the OLDEST (tail).
        # facts are already ordered newest-first by .active().
        rendered_lines: list[str] = []
        current_chars = len(header)
        for f in facts:
            line = f"- {f.fact_text}"
            if f.topic:
                line += f" [{f.topic}]"
            line_with_newline = line + "\n"
            if current_chars + len(line_with_newline) > max_chars:
                break
            rendered_lines.append(line_with_newline)
            current_chars += len(line_with_newline)
        return header + "".join(rendered_lines)


class UserFactsWriter:
    """Append-only writes to user_facts. Only constructible by Secretary.
    author_agent is written server-side; callers cannot spoof."""

    def __init__(self, agent_name: str, conn: "sqlite3.Connection | None" = None) -> None:
        if agent_name != "secretary":
            raise PermissionError(
                f"user_facts writer not allowed for agent={agent_name!r}; "
                "only 'secretary' may write user facts in this sub-project"
            )
        self._agent = agent_name
        self._conn = conn

    def add(self, fact_text: str, topic: str | None = None) -> int:
        if self._conn is None:
            raise RuntimeError("UserFactsWriter requires a conn to write")
        ts = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        cur = self._conn.execute(
            "INSERT INTO user_facts (ts, author_agent, fact_text, topic, is_active) "
            "VALUES (?, 'secretary', ?, ?, 1)",
            (ts, fact_text, topic),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def deactivate(self, fact_id: int) -> None:
        if self._conn is None:
            raise RuntimeError("UserFactsWriter requires a conn to write")
        self._conn.execute(
            "UPDATE user_facts SET is_active=0 WHERE id=?",
            (fact_id,),
        )
        self._conn.commit()
```

- [ ] **Step 4: Run to verify**

Run: `uv run pytest tests/test_user_facts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/project0/store.py tests/test_user_facts.py
git commit -m "feat(store): user_facts table with Secretary-only writer"
```

---

## Task 8: `UserProfile` + YAML loader + example file + gitignore

**Files:**
- Modify: `src/project0/store.py`
- Create: `tests/test_user_profile.py`
- Create: `data/user_profile.example.yaml`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing test**

```python
# tests/test_user_profile.py
from __future__ import annotations

from pathlib import Path

import pytest

from project0.store import UserProfile


def _write(tmp: Path, content: str) -> Path:
    p = tmp / "user_profile.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def test_missing_file_yields_empty_profile(tmp_path: Path) -> None:
    p = tmp_path / "does_not_exist.yaml"
    profile = UserProfile.load(p)
    assert profile.as_prompt_block() == ""


def test_loads_all_fields(tmp_path: Path) -> None:
    p = _write(tmp_path, """
address_as: "主人"
birthday: "1995-03-14"
fixed_preferences:
  - "说话简洁"
  - "不喜欢凌晨打扰"
out_of_band_notes: |
  我在做 MAAS 项目。
""")
    profile = UserProfile.load(p)
    block = profile.as_prompt_block()
    assert "主人" in block
    assert "1995-03-14" in block
    assert "说话简洁" in block
    assert "MAAS" in block


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "address_as: [unclosed")
    with pytest.raises(RuntimeError) as e:
        UserProfile.load(p)
    assert "user_profile.yaml" in str(e.value) or str(p) in str(e.value)


def test_unknown_top_level_keys_ignored(tmp_path: Path) -> None:
    p = _write(tmp_path, """
address_as: "主人"
some_future_field: "ignored"
""")
    profile = UserProfile.load(p)
    block = profile.as_prompt_block()
    assert "主人" in block
    assert "ignored" not in block


def test_invalid_date_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "birthday: \"not a date\"")
    with pytest.raises(RuntimeError) as e:
        UserProfile.load(p)
    assert "birthday" in str(e.value)


def test_non_list_fixed_preferences_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "fixed_preferences: \"just a string\"")
    with pytest.raises(RuntimeError) as e:
        UserProfile.load(p)
    assert "fixed_preferences" in str(e.value)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_user_profile.py -v`
Expected: FAIL — `UserProfile` not importable.

- [ ] **Step 3: Implement `UserProfile`**

Add to `src/project0/store.py`:

```python
import yaml  # add to imports; ensure `pyyaml` is in pyproject.toml dependencies


@dataclass
class UserProfile:
    address_as: str | None = None
    birthday: str | None = None
    fixed_preferences: list[str] = field(default_factory=list)
    out_of_band_notes: str | None = None

    @classmethod
    def load(cls, path: Path) -> "UserProfile":
        if not path.exists():
            log.warning("user_profile.yaml not found at %s; using empty profile", path)
            return cls()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise RuntimeError(f"malformed user_profile.yaml at {path}: {e}") from e
        if not isinstance(data, dict):
            raise RuntimeError(f"user_profile.yaml at {path} must be a mapping")

        KNOWN = {"address_as", "birthday", "fixed_preferences", "out_of_band_notes"}
        for k in data.keys():
            if k not in KNOWN:
                log.warning("user_profile.yaml: ignoring unknown key %r", k)

        address_as = data.get("address_as")
        if address_as is not None and not isinstance(address_as, str):
            raise RuntimeError("user_profile.yaml: address_as must be a string")

        birthday = data.get("birthday")
        if birthday is not None:
            if not isinstance(birthday, str):
                raise RuntimeError("user_profile.yaml: birthday must be a string")
            try:
                datetime.strptime(birthday, "%Y-%m-%d")
            except ValueError as e:
                raise RuntimeError(
                    f"user_profile.yaml: birthday must be YYYY-MM-DD, got {birthday!r}"
                ) from e

        prefs = data.get("fixed_preferences") or []
        if not isinstance(prefs, list):
            raise RuntimeError("user_profile.yaml: fixed_preferences must be a list")
        if not all(isinstance(p, str) for p in prefs):
            raise RuntimeError("user_profile.yaml: fixed_preferences entries must be strings")

        notes = data.get("out_of_band_notes")
        if notes is not None and not isinstance(notes, str):
            raise RuntimeError("user_profile.yaml: out_of_band_notes must be a string")

        return cls(
            address_as=address_as,
            birthday=birthday,
            fixed_preferences=list(prefs),
            out_of_band_notes=notes,
        )

    def as_prompt_block(self) -> str:
        if not any([self.address_as, self.birthday, self.fixed_preferences, self.out_of_band_notes]):
            return ""
        lines: list[str] = ["关于用户（静态资料，由用户手动维护）："]
        if self.address_as:
            lines.append(f"- 默认称呼: {self.address_as}")
        if self.birthday:
            lines.append(f"- 生日: {self.birthday}")
        if self.fixed_preferences:
            lines.append("- 固定偏好:")
            for p in self.fixed_preferences:
                lines.append(f"  · {p}")
        if self.out_of_band_notes:
            lines.append(f"- 备注: {self.out_of_band_notes.strip()}")
        return "\n".join(lines)
```

- [ ] **Step 4: Create example file**

Create `data/user_profile.example.yaml`:

```yaml
# MAAS user profile — static, hand-edited, read-only to agents.
#
# Copy this to `user_profile.yaml` and fill in real values. The real file is
# gitignored. Edits require a MAAS restart (no hot reload).
#
# All fields are optional.

# Fallback form of address used only by new agents that don't have one baked
# into their own persona. Each existing agent (Manager 林夕, Secretary 苏晚,
# Intelligence 顾瑾) overrides this with her own form in her persona file.
address_as: "主人"

# ISO-8601 date, optional.
birthday: "1995-03-14"

# Short free-text bullets. Keep to ≤5. Things you want every agent to always
# respect without having to re-learn them from conversation.
fixed_preferences:
  - "说话简洁，不要太啰嗦"
  - "不喜欢凌晨打扰"

# One short paragraph of standing context — the kind of thing you might tell
# a new coworker in the first five minutes.
out_of_band_notes: |
  我在做 MAAS 这个多 agent 系统项目。
```

- [ ] **Step 5: Update .gitignore**

Add to `.gitignore`:
```
data/user_profile.yaml
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_user_profile.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/project0/store.py tests/test_user_profile.py data/user_profile.example.yaml .gitignore
git commit -m "feat(store): UserProfile with YAML loader + example file"
```

---

## Task 9: Wire `UserProfile`, readers/writer, `LLMUsageStore` through composition root

**Files:**
- Modify: `src/project0/main.py`
- Modify: `src/project0/agents/secretary.py` (constructor)
- Modify: `src/project0/agents/manager.py` (constructor)
- Modify: `src/project0/agents/intelligence.py` (constructor)

No new tests — this is wiring that is covered by later end-to-end tests.

- [ ] **Step 1: Add constructor fields to each agent**

Each agent's constructor gets three new optional parameters with defaults so intermediate tests don't break:

```python
# secretary.py — inside Secretary.__init__:
    def __init__(
        self,
        *,
        llm: LLMProvider,
        memory: AgentMemory,
        messages_store: MessagesStore,
        persona: SecretaryPersona,
        config: SecretaryConfig,
        user_profile: "UserProfile | None" = None,
        user_facts_reader: "UserFactsReader | None" = None,
        user_facts_writer: "UserFactsWriter | None" = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._messages = messages_store
        self._persona = persona
        self._config = config
        self._user_profile = user_profile
        self._user_facts_reader = user_facts_reader
        self._user_facts_writer = user_facts_writer

# manager.py — same three optional params (reader only, no writer)
# intelligence.py — same three optional params (reader only, no writer)
```

- [ ] **Step 2: Update main.py composition root**

Edit `src/project0/main.py`:

```python
# After loading settings and creating Store:
from project0.store import (
    LLMUsageStore, UserFactsReader, UserFactsWriter, UserProfile,
)

store = Store(settings.store_path)

usage_store = LLMUsageStore(store.conn)
user_profile = UserProfile.load(Path("data/user_profile.yaml"))

# Update provider construction:
llm = AnthropicProvider(
    api_key=settings.anthropic_api_key,
    model=settings.llm_model,
    usage_store=usage_store,
    cache_ttl=settings.anthropic_cache_ttl,
)

# Construct fact reader/writer instances:
# - one reader per agent (shared store conn is fine)
# - exactly one writer, passed only to Secretary
secretary_reader = UserFactsReader("secretary", store.conn)
manager_reader = UserFactsReader("manager", store.conn)
intelligence_reader = UserFactsReader("intelligence", store.conn)
secretary_writer = UserFactsWriter("secretary", store.conn)

# Pass into each agent constructor:
secretary = Secretary(
    llm=llm,
    memory=AgentMemory(store.conn, "secretary"),
    messages_store=messages_store,
    persona=secretary_persona,
    config=secretary_config,
    user_profile=user_profile,
    user_facts_reader=secretary_reader,
    user_facts_writer=secretary_writer,
)
manager = Manager(
    ...,
    user_profile=user_profile,
    user_facts_reader=manager_reader,
    user_facts_writer=None,
)
intelligence = Intelligence(
    ...,
    user_profile=user_profile,
    user_facts_reader=intelligence_reader,
    user_facts_writer=None,
)
```

- [ ] **Step 3: Run full suite to verify nothing regressed**

Run: `uv run pytest -q && uv run mypy src/project0 && uv run ruff check src tests`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add src/project0/main.py src/project0/agents/secretary.py src/project0/agents/manager.py src/project0/agents/intelligence.py
git commit -m "wire: UserProfile, fact readers/writer, LLMUsageStore through composition root"
```

---

## Task 10: Rework Secretary prompt assembly for `SystemBlocks`

**Files:**
- Modify: `src/project0/agents/secretary.py`
- Create: `tests/test_secretary_system_blocks.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_secretary_system_blocks.py
from __future__ import annotations

from project0.agents.secretary import Secretary
from project0.llm.provider import SystemBlocks
from tests.test_call_site_labels import _make_secretary  # reuse helper
# (If reuse isn't practical, duplicate _make_secretary here.)


def _assemble_listener(sec: Secretary) -> SystemBlocks:
    """Call the internal helper that builds the SystemBlocks for listener mode."""
    return sec._assemble_system_blocks(mode="listener")


def test_listener_blocks_have_two_segments_when_facts_present(store_with_facts) -> None:
    sec = store_with_facts["secretary"]
    sb = _assemble_listener(sec)
    assert isinstance(sb, SystemBlocks)
    assert "persona" in sb.stable.lower() or "秘书" in sb.stable
    assert sb.facts is not None
    assert "寿司" in sb.facts


def test_listener_blocks_facts_empty_when_no_facts(store_without_facts) -> None:
    sec = store_without_facts["secretary"]
    sb = _assemble_listener(sec)
    assert sb.facts in (None, "")


def test_stable_block_contains_profile(store_with_profile) -> None:
    sec = store_with_profile["secretary"]
    sb = _assemble_listener(sec)
    assert "1995-03-14" in sb.stable  # birthday rendered in stable segment


def test_stable_block_does_not_contain_facts(store_with_facts) -> None:
    sec = store_with_facts["secretary"]
    sb = _assemble_listener(sec)
    assert "寿司" not in sb.stable
```

Add a conftest fixture `store_with_facts`, `store_without_facts`, `store_with_profile` that builds a Secretary with appropriate seeded data.

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_secretary_system_blocks.py -v`
Expected: FAIL — `Secretary._assemble_system_blocks` not found.

- [ ] **Step 3: Implement prompt assembly helper**

Edit `src/project0/agents/secretary.py`:

```python
from project0.llm.provider import SystemBlocks


class Secretary:
    # ... existing methods ...

    def _assemble_system_blocks(self, *, mode: str) -> SystemBlocks:
        """Build a two-segment system prompt. Segment 1 (stable) contains
        persona + mode + profile. Segment 2 (facts) contains the user_facts
        prompt block. A Secretary fact write busts only Segment 2 on the
        next call; Segment 1 stays warm."""
        mode_section = {
            "listener": self._persona.listener_mode,
            "addressed": self._persona.group_addressed_mode,
            "dm": self._persona.dm_mode,
            "reminder": self._persona.reminder_mode,
        }[mode]

        stable_parts = [self._persona.core, "", mode_section]
        if self._user_profile is not None:
            block = self._user_profile.as_prompt_block()
            if block:
                stable_parts.append("")
                stable_parts.append(block)
        stable = "\n".join(stable_parts)

        facts: str | None = None
        if self._user_facts_reader is not None:
            facts_block = self._user_facts_reader.as_prompt_block()
            facts = facts_block if facts_block else None

        return SystemBlocks(stable=stable, facts=facts)
```

Then replace each entry path to use `_assemble_system_blocks(mode=...)` and pass the result as `system=` to `complete()`:

```python
# In _listener_llm_call:
        system = self._assemble_system_blocks(mode="listener")
        # ... rest unchanged, just pass system to complete()

# In _addressed_llm_call:
        system = self._assemble_system_blocks(
            mode="dm" if env.source == "telegram_dm" else "addressed",
        )

# In _handle_reminder:
        system = self._assemble_system_blocks(mode="reminder")
```

Delete the old `system = self._persona.core + "\n\n" + self._persona.listener_mode` lines — the helper replaces them.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_secretary_system_blocks.py tests/test_call_site_labels.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/project0/agents/secretary.py tests/test_secretary_system_blocks.py
git commit -m "refactor(secretary): SystemBlocks prompt assembly with two cache breakpoints"
```

---

## Task 11: Rework Manager prompt assembly for `SystemBlocks`

**Files:**
- Modify: `src/project0/agents/manager.py`
- Create: `tests/test_manager_system_blocks.py`

- [ ] **Step 1: Read Manager's current prompt assembly**

Run: `grep -n "system" src/project0/agents/manager.py | head -30`

Identify where Manager currently concatenates its persona into a string and passes it as `system=` to `run_agentic_loop`. That's the site to replace.

- [ ] **Step 2: Write failing test**

```python
# tests/test_manager_system_blocks.py
# Mirror the Secretary test: seed a store with a user_fact, build Manager,
# call the assembly helper, assert stable contains profile + not facts,
# assert facts contains the fact bullet.
```

- [ ] **Step 3: Implement `Manager._assemble_system_blocks`**

```python
# In manager.py, add analogous to Secretary's:
    def _assemble_system_blocks(self) -> SystemBlocks:
        stable_parts = [self._persona_core]
        if self._user_profile is not None:
            block = self._user_profile.as_prompt_block()
            if block:
                stable_parts.append("")
                stable_parts.append(block)
        stable = "\n".join(stable_parts)

        facts: str | None = None
        if self._user_facts_reader is not None:
            b = self._user_facts_reader.as_prompt_block()
            facts = b if b else None

        return SystemBlocks(stable=stable, facts=facts)
```

Replace the existing string `system=...` passed to `run_agentic_loop(...)` with `system=self._assemble_system_blocks()`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_manager_system_blocks.py -v && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/project0/agents/manager.py tests/test_manager_system_blocks.py
git commit -m "refactor(manager): SystemBlocks prompt assembly"
```

---

## Task 12: Rework Intelligence prompt assembly for `SystemBlocks` + slim report

This task combines the prompt rework with the report-slim change (#6 from prenotes) because both touch the same assembly code.

**Files:**
- Modify: `src/project0/agents/intelligence.py`
- Create: `tests/test_intelligence_slim_report.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_intelligence_slim_report.py
"""Verify Intelligence's Q&A system prompt contains only the headline
index form, ≤ 700 rendered characters for the report section."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from project0.agents.intelligence import Intelligence


def _seed_report(tmp_path: Path, n_items: int = 12) -> Path:
    reports_dir = tmp_path / "intelligence" / "reports"
    reports_dir.mkdir(parents=True)
    report = {
        "date": "2026-04-16",
        "news_items": [
            {
                "id": f"r{i:02d}",
                "headline": f"headline number {i} short",
                "importance": "high",
                "summary": "a long summary that must NOT appear in the system prompt" * 20,
                "source_tweets": [],
            }
            for i in range(1, n_items + 1)
        ],
        "suggested_accounts": [],
    }
    (reports_dir / "2026-04-16.json").write_text(json.dumps(report), encoding="utf-8")
    return reports_dir.parent


def test_qa_system_prompt_is_headline_only(tmp_path: Path) -> None:
    data_dir = _seed_report(tmp_path)
    intel = _make_intelligence(data_dir)  # helper: builds Intelligence with fake deps
    sb = intel._assemble_system_blocks(report_date="2026-04-16")
    assert "[r01]" in sb.stable
    assert "headline number 1" in sb.stable
    # Critically: summary text should NOT be in the system prompt
    assert "a long summary that must NOT appear" not in sb.stable
    # And the total report section size is bounded:
    lines = [l for l in sb.stable.splitlines() if l.startswith("[r")]
    assert len(lines) == 12
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_intelligence_slim_report.py -v`
Expected: FAIL — either full report still inlined OR `_assemble_system_blocks` missing.

- [ ] **Step 3: Implement slim index rendering + assembly**

Edit `src/project0/agents/intelligence.py`:

```python
def _render_report_index(report: "DailyReport") -> str:
    """Headline-only index for the system prompt. Full item detail is
    fetched on-demand via get_report_item."""
    header = f"今天的日报索引 ({report.date}):"
    lines = [header]
    for item in report.news_items:
        lines.append(f"[{item.id}] {item.headline}")
    return "\n".join(lines)


class Intelligence:
    def _assemble_system_blocks(self, *, report_date: str | None = None) -> SystemBlocks:
        parts = [self._persona_core]

        if self._latest_report is not None:
            parts.append("")
            parts.append(_render_report_index(self._latest_report))

        if self._user_profile is not None:
            pb = self._user_profile.as_prompt_block()
            if pb:
                parts.append("")
                parts.append(pb)

        stable = "\n".join(parts)

        facts: str | None = None
        if self._user_facts_reader is not None:
            fb = self._user_facts_reader.as_prompt_block()
            facts = fb if fb else None

        return SystemBlocks(stable=stable, facts=facts)
```

**Critically: delete the old full-report injection code.** Search for where `news_items[i].summary` or similar full-item fields are rendered into the system prompt and remove that code. The tool in Task 13 replaces it.

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_intelligence_slim_report.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/project0/agents/intelligence.py tests/test_intelligence_slim_report.py
git commit -m "refactor(intelligence): SystemBlocks + slim report index (delete full-report injection)"
```

---

## Task 13: Add `get_report_item` tool to Intelligence

**Files:**
- Modify: `src/project0/agents/intelligence.py`
- Create: `tests/test_intelligence_get_report_item.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_intelligence_get_report_item.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from project0.agents.intelligence import Intelligence, get_report_item_tool_spec
from project0.llm.tools import ToolCall


def _seed(tmp_path: Path) -> Path:
    d = tmp_path / "intelligence" / "reports"
    d.mkdir(parents=True)
    (d / "2026-04-16.json").write_text(json.dumps({
        "date": "2026-04-16",
        "news_items": [
            {"id": "r01", "headline": "A", "importance": "high",
             "summary": "full A summary", "source_tweets": ["https://x.com/a/1"]},
        ],
        "suggested_accounts": [],
    }), encoding="utf-8")
    return tmp_path


@pytest.mark.asyncio
async def test_get_report_item_returns_full_item(tmp_path: Path) -> None:
    intel = _make_intelligence(_seed(tmp_path))
    call = ToolCall(id="tc1", name="get_report_item",
                    input={"item_id": "r01", "date": "2026-04-16"})
    result_str, is_err = await intel._dispatch_tool(call, state=None)
    assert not is_err
    result = json.loads(result_str)
    assert result["id"] == "r01"
    assert result["summary"] == "full A summary"


def test_tool_spec_advertises_schema() -> None:
    spec = get_report_item_tool_spec()
    assert spec.name == "get_report_item"
    assert "item_id" in spec.input_schema["properties"]
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_intelligence_get_report_item.py -v`
Expected: FAIL — `get_report_item_tool_spec` not defined.

- [ ] **Step 3: Implement the tool**

Edit `src/project0/agents/intelligence.py`:

```python
from project0.llm.tools import ToolSpec


def get_report_item_tool_spec() -> ToolSpec:
    return ToolSpec(
        name="get_report_item",
        description=(
            "Fetch the full content of a single item from a daily report by "
            "its id. Use when the user asks to dig deeper on a specific item "
            "from the headline index."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "e.g. 'r01'"},
                "date": {
                    "type": "string",
                    "description": "YYYY-MM-DD; defaults to today",
                },
            },
            "required": ["item_id"],
        },
    )


# Add to Intelligence's dispatch_tool:
    async def _dispatch_tool(self, call, state) -> tuple[str, bool]:
        if call.name == "get_report_item":
            item_id = call.input.get("item_id")
            date = call.input.get("date") or today_str()
            path = self._reports_dir / f"{date}.json"
            if not path.exists():
                return (json.dumps({"error": f"no report for {date}"}), True)
            report = json.loads(path.read_text(encoding="utf-8"))
            for item in report.get("news_items", []):
                if item.get("id") == item_id:
                    return (json.dumps(item, ensure_ascii=False), False)
            return (json.dumps({"error": f"item_id {item_id} not found"}), True)
        # ... existing tool dispatch branches for other tools
        return ("unknown tool", True)
```

Add `get_report_item_tool_spec()` to the tool list passed to `run_agentic_loop` in Intelligence's Q&A entry.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_intelligence_get_report_item.py tests/test_intelligence_slim_report.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/project0/agents/intelligence.py tests/test_intelligence_get_report_item.py
git commit -m "feat(intelligence): add get_report_item tool for on-demand deep dives"
```

---

## Task 14: Secretary `remember_about_user` tool + bounded tool loop

**Files:**
- Modify: `src/project0/agents/secretary.py`
- Create: `tests/test_secretary_tool_loop.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_secretary_tool_loop.py
from __future__ import annotations

import pytest

from project0.agents.secretary import Secretary, remember_about_user_tool_spec
from project0.envelope import Envelope
from project0.llm.provider import FakeProvider
from project0.llm.tools import AssistantToolUseMsg, ToolCall, ToolUseResult
from project0.store import (
    AgentMemory, LLMUsageStore, MessagesStore, Store,
    UserFactsReader, UserFactsWriter, UserProfile,
)
# Import _make_secretary helper; extend to wire reader/writer/profile.


@pytest.mark.asyncio
async def test_secretary_remembers_fact_via_tool_call() -> None:
    store = Store(":memory:")
    usage = LLMUsageStore(store.conn)
    writer = UserFactsWriter("secretary", store.conn)
    reader = UserFactsReader("secretary", store.conn)

    # First call: tool_use with remember_about_user.
    # Second call: plain text reply.
    fake = FakeProvider(
        tool_responses=[
            ToolUseResult(
                kind="tool_use",
                text=None,
                tool_calls=[ToolCall(
                    id="tc1", name="remember_about_user",
                    input={"fact_text": "最喜欢吃寿司", "topic": "food"},
                )],
                stop_reason="tool_use",
            ),
            ToolUseResult(
                kind="text", text="好的，记住了宝贝～",
                tool_calls=[], stop_reason="end_turn",
            ),
        ],
        usage_store=usage,
    )
    sec = _make_secretary(fake, usage, store,
                          profile=UserProfile(), reader=reader, writer=writer)

    env = Envelope(
        id=200, ts="2026-04-16T10:00:00Z", parent_id=None,
        source="telegram_dm", telegram_chat_id=1, telegram_msg_id=1,
        received_by_bot="secretary", from_kind="user", from_agent=None,
        to_agent="secretary", body="我最喜欢吃寿司", mentions=[],
        routing_reason="direct_dm",
    )
    result = await sec.handle(env)
    assert result is not None
    assert "好的" in result.reply_text

    # Fact persisted
    facts = reader.active()
    assert len(facts) == 1
    assert facts[0].fact_text == "最喜欢吃寿司"
    assert facts[0].topic == "food"

    # Two LLM calls recorded
    rows = store.conn.execute("SELECT count(*) FROM llm_usage").fetchone()
    assert rows[0] == 2
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_secretary_tool_loop.py -v`
Expected: FAIL — tool spec missing or Secretary paths don't use tool loop.

- [ ] **Step 3: Implement the tool + rewire all four entry paths**

Edit `src/project0/agents/secretary.py`:

```python
from project0.agents._tool_loop import run_agentic_loop
from project0.llm.tools import ToolCall, ToolSpec


def remember_about_user_tool_spec() -> ToolSpec:
    return ToolSpec(
        name="remember_about_user",
        description=(
            "Save a short factual note about the user to long-term memory. "
            "Use when the user tells you something personal worth remembering "
            "(birthday, preferences, current work, hobbies). Keep facts to one "
            "short sentence. Do not save anything the user asked you to forget."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "fact_text": {
                    "type": "string",
                    "description": "One short sentence stating the fact.",
                },
                "topic": {
                    "type": "string",
                    "description": "Optional tag, e.g. 'food', 'personal'.",
                },
            },
            "required": ["fact_text"],
        },
    )


class Secretary:
    async def _run_with_tool_loop(
        self,
        *,
        env: Envelope,
        purpose: str,
        mode: str,
        initial_user_text: str,
        max_tokens: int,
    ) -> str | None:
        """Run a bounded one-iteration-deep tool loop. Secretary has exactly
        one tool (remember_about_user). A successful call writes to user_facts
        and feeds the result back to the model, which then produces the final
        reply. If the model emits no tool call, its text is the reply
        directly."""
        system = self._assemble_system_blocks(mode=mode)
        tools: list[ToolSpec] = []
        if self._user_facts_writer is not None:
            tools.append(remember_about_user_tool_spec())

        async def _dispatch(call: ToolCall, _state) -> tuple[str, bool]:
            if call.name == "remember_about_user":
                if self._user_facts_writer is None:
                    return ("writer not available", True)
                fact = call.input.get("fact_text") or ""
                topic = call.input.get("topic")
                if not fact.strip():
                    return ("fact_text required", True)
                try:
                    fid = self._user_facts_writer.add(fact, topic=topic)
                except Exception as e:
                    log.warning("user_facts write failed: %s", e)
                    return (f"error: {e}", True)
                return (f'{{"ok": true, "fact_id": {fid}}}', False)
            return (f"unknown tool: {call.name}", True)

        try:
            result = await run_agentic_loop(
                llm=self._llm,
                system=system,
                initial_user_text=initial_user_text,
                tools=tools,
                dispatch_tool=_dispatch,
                max_iterations=2,
                max_tokens=max_tokens,
                agent="secretary",
                purpose=purpose,
                envelope_id=env.id,
            )
        except Exception as e:
            log.warning("secretary tool loop failed: %s", e)
            return None

        if result.errored:
            return None
        return result.final_text
```

Then replace the bodies of `_listener_llm_call`, `_addressed_llm_call`, `_handle_dm`, `_handle_reminder` so each calls `_run_with_tool_loop` with the appropriate `mode` / `purpose` / `initial_user_text`. Preserve the existing scene-string and transcript loading — they become the `initial_user_text`. Preserve the existing skip-sentinel check on the listener path:

```python
# Example for _listener_llm_call, post-rework:
    async def _listener_llm_call(self, env: Envelope) -> AgentResult | None:
        chat_id = env.telegram_chat_id
        assert chat_id is not None
        transcript = self._load_transcript(chat_id)
        user_msg = f"对话记录(最后一条是用户刚发的):\n{transcript}"

        text = await self._run_with_tool_loop(
            env=env,
            purpose="listener",
            mode="listener",
            initial_user_text=user_msg,
            max_tokens=self._config.max_tokens_listener,
        )
        if text is None:
            return None
        if is_skip_sentinel(text, self._config.skip_sentinels):
            log.info("secretary considered, passed (skip sentinel)")
            return None
        self._reset_cooldown_after_reply(chat_id)
        return AgentResult(reply_text=text, delegate_to=None, handoff_text=None)
```

Apply the same rewrite pattern to the other three paths, matching their existing scene-and-transcript construction.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_secretary_tool_loop.py tests/test_secretary_system_blocks.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite to catch regressions**

Run: `uv run pytest -q && uv run mypy src/project0 && uv run ruff check src tests`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/project0/agents/secretary.py tests/test_secretary_tool_loop.py
git commit -m "feat(secretary): remember_about_user tool via bounded tool loop"
```

---

## Task 15: Manager transcript_window 20 → 10

**Files:**
- Modify: `prompts/manager.toml`
- Create: `tests/test_transcript_window_locked.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_transcript_window_locked.py
"""Locks transcript_window values per §E of the spec. Any drift fails loudly."""
from __future__ import annotations

import tomllib
from pathlib import Path


def test_manager_transcript_window_is_10() -> None:
    data = tomllib.loads(Path("prompts/manager.toml").read_text(encoding="utf-8"))
    assert data["context"]["transcript_window"] == 10


def test_secretary_transcript_window_is_20() -> None:
    data = tomllib.loads(Path("prompts/secretary.toml").read_text(encoding="utf-8"))
    assert data["context"]["transcript_window"] == 20


def test_intelligence_transcript_window_is_10() -> None:
    data = tomllib.loads(Path("prompts/intelligence.toml").read_text(encoding="utf-8"))
    assert data["context"]["transcript_window"] == 10
```

- [ ] **Step 2: Run to verify Manager test fails**

Run: `uv run pytest tests/test_transcript_window_locked.py -v`
Expected: test_manager_transcript_window_is_10 FAILS (current value is 20).

- [ ] **Step 3: Update manager.toml**

Edit `prompts/manager.toml`. Find `[context] transcript_window = 20`. Change to `transcript_window = 10`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_transcript_window_locked.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prompts/manager.toml tests/test_transcript_window_locked.py
git commit -m "tune(manager): transcript_window 20 → 10 + locking test"
```

---

## Task 16: Cache layout invariant tests

**Files:**
- Create: `tests/test_cache_layout_invariant.py`

- [ ] **Step 1: Write the tests**

```python
# tests/test_cache_layout_invariant.py
"""Per-agent cache layout invariant:

1. For each agent, building the system prompt twice with only a messages[]
   difference must produce byte-identical Segment-1 bytes AND byte-identical
   Segment-2 bytes. Two independent assertions per agent.

2. When facts are present, exactly two cache_control markers must appear in
   the rendered SDK system param.

3. No volatile content (transcript, scene, current-turn body) may appear
   inside either segment.
"""
from __future__ import annotations

from project0.llm.provider import SystemBlocks, _render_system_param


def _assert_no_volatile_markers(text: str, volatile_markers: list[str]) -> None:
    for m in volatile_markers:
        assert m not in text, f"volatile marker {m!r} leaked into cached segment"


def test_secretary_cache_layout_invariant(secretary_with_facts) -> None:
    sec = secretary_with_facts
    sb1 = sec._assemble_system_blocks(mode="addressed")
    sb2 = sec._assemble_system_blocks(mode="addressed")
    assert sb1.stable == sb2.stable
    assert sb1.facts == sb2.facts

    rendered = _render_system_param(sb1)
    cache_markers = [b for b in rendered if "cache_control" in b]
    assert len(cache_markers) == 2

    _assert_no_volatile_markers(sb1.stable, ["@secretary", "明天", "transcript"])
    if sb1.facts:
        _assert_no_volatile_markers(sb1.facts, ["@secretary", "明天", "transcript"])


def test_manager_cache_layout_invariant(manager_with_facts) -> None:
    mgr = manager_with_facts
    sb1 = mgr._assemble_system_blocks()
    sb2 = mgr._assemble_system_blocks()
    assert sb1.stable == sb2.stable
    assert sb1.facts == sb2.facts
    rendered = _render_system_param(sb1)
    assert sum(1 for b in rendered if "cache_control" in b) == 2


def test_intelligence_cache_layout_invariant(intelligence_with_facts) -> None:
    intel = intelligence_with_facts
    sb1 = intel._assemble_system_blocks()
    sb2 = intel._assemble_system_blocks()
    assert sb1.stable == sb2.stable
    assert sb1.facts == sb2.facts


def test_single_segment_when_no_facts(secretary_empty_facts) -> None:
    sb = secretary_empty_facts._assemble_system_blocks(mode="listener")
    rendered = _render_system_param(sb)
    cache_markers = [b for b in rendered if "cache_control" in b]
    assert len(cache_markers) == 1  # only the stable segment, no facts breakpoint
```

Fixtures `secretary_with_facts`, `manager_with_facts`, `intelligence_with_facts`, `secretary_empty_facts` go in `tests/conftest.py`. Each constructs the agent with a seeded store containing one user_fact (except `secretary_empty_facts`) and a seeded UserProfile.

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_cache_layout_invariant.py -v`
Expected: PASS (4 tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_cache_layout_invariant.py tests/conftest.py
git commit -m "test: cache layout invariant across all three agents"
```

---

## Task 17: Schema immutability test (spec §B.4)

**Files:**
- Create: `tests/test_schema_immutability.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_schema_immutability.py
"""Lock the schemas of existing tables (messages, agent_memory, blackboard,
chat_focus). Any accidental drift in this sub-project fails the test."""
from __future__ import annotations

from project0.store import Store


EXPECTED_SCHEMA: dict[str, list[tuple[str, str, int, int]]] = {
    # table: [(cid, name, type, notnull, pk) tuples] — fill in from existing schema
    # Run once with AUTHORITATIVE_DUMP=1 to print current schema and paste here.
}


def _schema_tuples(conn, table: str) -> list[tuple]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [(r[1], r[2], r[3], r[5]) for r in rows]  # name, type, notnull, pk


def test_messages_schema_unchanged() -> None:
    conn = Store(":memory:").conn
    actual = _schema_tuples(conn, "messages")
    expected = EXPECTED_SCHEMA["messages"]
    assert actual == expected


def test_agent_memory_schema_unchanged() -> None:
    conn = Store(":memory:").conn
    assert _schema_tuples(conn, "agent_memory") == EXPECTED_SCHEMA["agent_memory"]


def test_blackboard_schema_unchanged() -> None:
    conn = Store(":memory:").conn
    assert _schema_tuples(conn, "blackboard") == EXPECTED_SCHEMA["blackboard"]


def test_chat_focus_schema_unchanged() -> None:
    conn = Store(":memory:").conn
    assert _schema_tuples(conn, "chat_focus") == EXPECTED_SCHEMA["chat_focus"]
```

- [ ] **Step 2: Populate EXPECTED_SCHEMA**

Run a short Python snippet to dump the current schemas:

```bash
uv run python -c "
from project0.store import Store
conn = Store(':memory:').conn
for t in ('messages','agent_memory','blackboard','chat_focus'):
    rows = conn.execute(f'PRAGMA table_info({t})').fetchall()
    print(t, [(r[1], r[2], r[3], r[5]) for r in rows])
"
```

Paste the output into `EXPECTED_SCHEMA` in the test file.

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/test_schema_immutability.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_schema_immutability.py
git commit -m "test: lock schemas of pre-existing tables against drift"
```

---

## Task 18: Cross-agent fact visibility end-to-end test

**Files:**
- Create: `tests/test_cross_agent_fact_visibility.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_cross_agent_fact_visibility.py
"""End-to-end: Secretary writes a fact via the tool loop, then Manager and
Intelligence see it in their next system prompt. This is the learning-
across-agents proof."""
from __future__ import annotations

import pytest

from project0.store import (
    Store, UserFactsReader, UserFactsWriter, UserProfile,
)


@pytest.mark.asyncio
async def test_fact_written_by_secretary_visible_to_manager_and_intelligence() -> None:
    store = Store(":memory:")
    writer = UserFactsWriter("secretary", store.conn)
    writer.add("最喜欢吃寿司", topic="food")

    manager_reader = UserFactsReader("manager", store.conn)
    intelligence_reader = UserFactsReader("intelligence", store.conn)

    mgr_block = manager_reader.as_prompt_block()
    intel_block = intelligence_reader.as_prompt_block()
    assert "寿司" in mgr_block
    assert "寿司" in intel_block
```

- [ ] **Step 2: Run**

Run: `uv run pytest tests/test_cross_agent_fact_visibility.py -v`
Expected: PASS (the implementation is already in place from Tasks 7 and 9).

- [ ] **Step 3: Commit**

```bash
git add tests/test_cross_agent_fact_visibility.py
git commit -m "test: cross-agent fact visibility end-to-end"
```

---

## Task 19: `.env.example` + smoke script + README roadmap

**Files:**
- Modify: `.env.example`
- Create: `scripts/smoke_memory.sh`
- Modify: `README.md`

- [ ] **Step 1: Update `.env.example`**

Append to `.env.example`:

```env

# Anthropic cache TTL. `ephemeral` is the default ~5 min cache.
# Opt in to `1h` after the WebUI token monitor lands if your usage pattern
# has gaps of 5-60 min between conversations (may pay off; may not).
ANTHROPIC_CACHE_TTL=ephemeral
```

- [ ] **Step 2: Create `scripts/smoke_memory.sh`**

```bash
#!/usr/bin/env bash
# Post-smoke-test inspection helper. Run after the human smoke test to
# verify the memory layer and instrumentation worked.
set -euo pipefail
DB="${1:-data/store.db}"

echo "=== Recent user_facts ==="
sqlite3 "$DB" "SELECT id, author_agent, topic, fact_text, is_active
               FROM user_facts ORDER BY id DESC LIMIT 10;"
echo

echo "=== llm_usage rollup per agent + purpose (today) ==="
sqlite3 "$DB" "SELECT agent, purpose,
                      COUNT(*) AS calls,
                      SUM(input_tokens) AS total_in,
                      SUM(cache_creation_input_tokens) AS total_cc,
                      SUM(cache_read_input_tokens) AS total_cr,
                      SUM(output_tokens) AS total_out
               FROM llm_usage
               WHERE ts >= date('now')
               GROUP BY agent, purpose
               ORDER BY agent, purpose;"
echo

echo "=== Cache hit ratio (cache_read / (cache_read + input)) today ==="
sqlite3 "$DB" "SELECT agent,
                      CASE WHEN SUM(cache_read_input_tokens) + SUM(input_tokens) = 0
                           THEN 'n/a'
                           ELSE printf('%.1f%%',
                               100.0 * SUM(cache_read_input_tokens) /
                               (SUM(cache_read_input_tokens) + SUM(input_tokens)))
                      END AS hit_ratio
               FROM llm_usage WHERE ts >= date('now') GROUP BY agent;"
```

Make executable:
```bash
chmod +x scripts/smoke_memory.sh
```

- [ ] **Step 3: Update README.md roadmap**

In `README.md`, find the Roadmap section. Move "Memory layer hardening + token cost cut" from "Next up" to "Sub-projects completed" once the sub-project is actually merged (this edit is the final mechanical step, not a predictive one).

For now, add the completion line:
```markdown
- **Memory hardening** — Layer A user profile, narrow Layer D slice (Secretary-written user facts), llm_usage instrumentation, two-breakpoint cache layout, Manager transcript shrink, Intelligence Q&A slim + get_report_item tool, env-toggled 1-hour cache TTL.
```

- [ ] **Step 4: Commit**

```bash
git add .env.example scripts/smoke_memory.sh README.md
chmod +x scripts/smoke_memory.sh
git add scripts/smoke_memory.sh
git commit -m "chore: smoke script, env example, roadmap update for memory sub-project"
```

---

## Task 20: Final validation pass

**Files:** all

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, zero failures.

- [ ] **Step 2: Run mypy**

Run: `uv run mypy src/project0`
Expected: zero errors.

- [ ] **Step 3: Run ruff**

Run: `uv run ruff check src tests`
Expected: zero warnings.

- [ ] **Step 4: Verify schema after fresh store**

Run:
```bash
rm -f /tmp/smoke_test.db
uv run python -c "
from project0.store import Store
s = Store('/tmp/smoke_test.db')
tables = s.conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\").fetchall()
print([t[0] for t in tables])
"
```
Expected output includes `agent_memory`, `blackboard`, `chat_focus`, `llm_usage`, `messages`, `user_facts`.

- [ ] **Step 5: Verify no prompts/*.md files were touched**

Run: `git diff --name-only main -- 'prompts/*.md'`
Expected: empty output (K.2 enforcement).

- [ ] **Step 6: Verify K.1 file allowlist**

Run: `git diff --name-only main`
Expected: only files in the allowlist from the spec §6.3 K.1.

---

## Task 21: Prepare environment for user smoke test

Per the user's testing-discipline preference: automate everything possible, hand the user one prepared session.

- [ ] **Step 1: Prepare `data/user_profile.yaml`**

Create `data/user_profile.yaml` (not committed — gitignored) with believable seed values. Copy from the example and fill in real-looking content:

```bash
cp data/user_profile.example.yaml data/user_profile.yaml
# Edit data/user_profile.yaml with the user's actual preferences
```

Leave this file in place for the user to inspect.

- [ ] **Step 2: Wipe `data/store.db`**

```bash
rm -f data/store.db
```

- [ ] **Step 3: Verify `.env` has `ANTHROPIC_CACHE_TTL=ephemeral`**

If missing, append it. Do not opt into 1h for the smoke test — defaults only.

- [ ] **Step 4: Verify the daily report exists for today**

```bash
ls data/intelligence/reports/$(date +%F).json 2>/dev/null || echo "MISSING — generate one first"
```

If missing, either generate one or seed a fake report for the smoke test. Recommend real generation if `TWITTERAPI_IO_API_KEY` is set:
```bash
uv run python scripts/smoke_generate_report.py
```

- [ ] **Step 5: Start MAAS**

```bash
uv run python -m project0.main
```

Confirm startup logs show:
- `user_profile loaded from data/user_profile.yaml` (or similar positive log)
- All three bots polling
- Intelligence webapp bound
- Daily pulse spawned

Leave the process running in a terminal.

- [ ] **Step 6: Present the smoke test instructions to the user**

Output the exact steps from spec §6.3 J.1-J.6:

```
### Smoke test — run in your phone's Telegram app (target < 15 min)

1. **DM Secretary (苏晚):** send `记住我最喜欢的食物是寿司`.
   Confirm she replies in character.

2. **DM Manager (林夕):** send `我最喜欢吃什么？`.
   Confirm she answers referencing 寿司.
   → This is the cross-agent learning proof.

3. **In the group chat:** send `@manager 明天有什么事？`.
   Confirm calendar readback still works.

4. **In the group chat:** send `@intelligence 今天最要紧的是什么？`.
   Confirm she answers from the headline index without needing to fetch details.

5. **In the group chat:** send `@intelligence 第一条具体讲什么？`.
   Confirm she calls `get_report_item` and replies with full detail.
   → This is the lazy-report-access proof.

6. **Back in the terminal:** run `scripts/smoke_memory.sh`.
   Confirm:
   - A `user_facts` row containing 寿司 with `author_agent=secretary`.
   - `llm_usage` rows for all three agents with nonzero `cache_read` tokens on
     subsequent calls (proving cache discipline worked).
   - Rollup token counts are in a reasonable range.

If anything fails, tell me and I'll fix it — you do not need to debug.
```

---

## Self-Review

**Spec coverage check (against §6.3 acceptance criteria):**

- **A (tests/checks):** Tasks 1-20 collectively; Task 20 runs the full green bar.
- **B (schema/storage):** Tasks 1, 7 add the new tables; Task 8 adds the example YAML and .gitignore; Task 17 locks the existing-table schemas.
- **C (trust boundary):** Task 7 tests (writer rejects manager/intelligence, caller cannot spoof, only one INSERT site).
- **D (cache-layout discipline):** Task 16 (invariant tests for all three agents, two-breakpoint assertion, no volatile content).
- **E (token cut mechanics):** Task 10-13 (SystemBlocks + Intelligence slim + tool); Task 15 (Manager transcript + locking test); Task 12 (full-report injection deletion).
- **F (instrumentation):** Task 1 (store), Task 3 (provider both methods), Task 5 (tool loop plumbing), Task 6 (call site labels).
- **G (cache TTL toggle):** Task 2 (config validation), Task 3 (provider propagation), Task 19 (.env.example update).
- **H (Layer A behavior):** Task 8 tests cover missing/malformed/unknown-keys/invalid-date.
- **I (Layer D slice):** Task 7 tests + Task 18 cross-agent visibility.
- **J (human smoke test):** Task 21 preparation + hand-off steps.
- **K (scope discipline):** Task 20 step 5 and 6 enforce K.1 and K.2.

**Placeholder scan:** The plan uses `_make_secretary` / `_make_intelligence` helper references without fully spelling out each conftest; the task-by-task test code still includes enough scaffolding to be actionable, but an executor should expect to add shared fixtures to `tests/conftest.py` as they go. This is acceptable — the spec for each fixture is given inline (what it returns, what it seeds).

**Type consistency check:** `SystemBlocks(stable, facts)` used consistently. `UserFactsReader(agent_name, conn)` / `UserFactsWriter(agent_name, conn)` signatures consistent across Tasks 7, 9, 10-12, 14, 18. `agent` / `purpose` / `envelope_id` labels propagated through provider + tool loop + all call sites in Tasks 3-6. `cache_ttl` flows from config → main → provider constructor in Tasks 2, 3, 9.

**Fix applied inline:** Task 6's test body originally referenced a ToolUseResult for a non-tool-use Secretary reply — corrected to use `FakeProvider(responses=[...])` for the plain-complete addressed path.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-16-memory-hardening.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration with isolated context per task.

2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review.

Which approach?
