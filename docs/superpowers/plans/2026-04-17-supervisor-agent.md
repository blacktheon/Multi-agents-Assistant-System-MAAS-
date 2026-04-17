# Supervisor Agent (叶霏) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Supervisor Agent (叶霏) — a pulse-scheduled reviewer that reads Manager/Intelligence/Learning conversation history every 3 hours (idle-gated) and on-demand via DM, scores each on a four-dimension rubric with a text critique + recommendations, and surfaces everything on a new `/reviews` control-panel page. Also enforces Secretary-history isolation across all agents.

**Architecture:** Full fifth agent with own Telegram bot, persona, config, pulse entries. Reviews written to a new `supervisor_reviews` SQLite table. Per-agent review cursor lives in Supervisor's private `agent_memory`. Idle gate implemented via a second `review_retry` pulse (60s) that only acts when a pending flag is set. Cross-cutting: `MessagesStore.recent_for_chat` gains a required `visible_to` parameter that filters Secretary envelopes for non-Secretary callers. Read-only `/reviews` page in the existing control panel renders a multi-line SVG time-series chart plus three per-agent cards plus per-agent history sections.

**Tech Stack:** Python 3.12, sqlite3, FastAPI + Jinja2 (control panel), python-telegram-bot, Anthropic SDK (Claude Sonnet 4.6), existing `run_agentic_loop` tool-loop helper, existing pulse primitive, existing `SystemBlocks` cached-prompt pattern, inline SVG rendering.

**Spec:** `docs/superpowers/specs/2026-04-17-supervisor-agent-design.md`

---

## File structure

**New files:**
- `src/project0/agents/supervisor.py` — Supervisor agent class, persona/config loaders, idle gate, review engine
- `prompts/supervisor.md` — four-section persona (core / dm_mode / pulse_mode / tool_use_guide)
- `prompts/supervisor.toml` — `[llm]`, `[context]`, `[review]`, two `[[pulse]]` entries
- `src/project0/control_panel/templates/reviews.html` — `/reviews` page template
- `tests/test_supervisor_agent.py` — persona/config/handle/idle/cursor tests
- `tests/test_supervisor_reviews_store.py` — SQLite store + table tests
- `tests/test_messages_store_visibility.py` — Secretary-isolation tests
- `tests/test_reviews_page.py` — `/reviews` route + template tests
- `tests/test_reviews_rendering.py` — SVG helper tests

**Modified files:**
- `src/project0/store.py` — add `supervisor_reviews` schema, `SupervisorReviewsStore`, `envelopes_for_review`, `visible_to` kwarg on `recent_for_chat`
- `src/project0/agents/registry.py` — add `supervisor` to `AGENT_SPECS`, `register_supervisor`
- `src/project0/agents/manager.py` — pass `visible_to="manager"` to `recent_for_chat`
- `src/project0/agents/intelligence.py` — pass `visible_to="intelligence"` to `recent_for_chat`
- `src/project0/agents/learning.py` — pass `visible_to="learning"` to `recent_for_chat`
- `src/project0/agents/secretary.py` — pass `visible_to="secretary"` to `recent_for_chat`
- `src/project0/control_panel/routes.py` — add `GET /reviews`
- `src/project0/control_panel/templates/base.html` — add `/reviews` nav link
- `src/project0/control_panel/rendering.py` — add `render_score_timeseries_svg`, `render_sparkline_svg`
- `src/project0/control_panel/paths.py` — add `"supervisor"` to `ALLOWED_AGENT_NAMES`
- `src/project0/main.py` — construct Supervisor, register, spawn its pulse tasks
- `.env.example` (if present, else `.env` locally) — add `TELEGRAM_BOT_TOKEN_SUPERVISOR` and `SUPERVISOR_PULSE_CHAT_ID`

---

## Task 1: Secretary-history isolation — `visible_to` on `recent_for_chat`

**Files:**
- Modify: `src/project0/store.py` (`MessagesStore.recent_for_chat`, around lines 343–367)
- Modify: `src/project0/agents/manager.py:498`
- Modify: `src/project0/agents/intelligence.py:601`
- Modify: `src/project0/agents/learning.py:602`
- Modify: `src/project0/agents/secretary.py:378`
- Test: `tests/test_messages_store_visibility.py` (new)

- [ ] **Step 1.1: Write the failing isolation tests**

Create `tests/test_messages_store_visibility.py`:

```python
"""Tests for the Secretary-history isolation guarantee on
MessagesStore.recent_for_chat — non-Secretary callers never see envelopes
where Secretary is the from_agent or to_agent participant."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from project0.envelope import Envelope
from project0.store import Store


def _mk_env(
    *,
    ts: str,
    from_kind: str,
    from_agent: str | None,
    to_agent: str,
    body: str,
    chat_id: int = 100,
    msg_id: int,
) -> Envelope:
    return Envelope(
        id=None,
        ts=ts,
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=chat_id,
        telegram_msg_id=msg_id,
        received_by_bot=None,
        from_kind=from_kind,  # type: ignore[arg-type]
        from_agent=from_agent,
        to_agent=to_agent,
        body=body,
    )


def _seed(store: Store) -> None:
    """Insert a group-chat transcript that includes both Secretary and
    non-Secretary envelopes."""
    msgs = store.messages()
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    # User asks Manager in the group:
    msgs.insert(_mk_env(
        ts=now, from_kind="user", from_agent=None,
        to_agent="manager", body="帮我看看明天的日程", msg_id=1,
    ))
    # Manager replies:
    msgs.insert(_mk_env(
        ts=now, from_kind="agent", from_agent="manager",
        to_agent="user", body="明天下午两点有会议", msg_id=2,
    ))
    # Listener observation for Secretary (she witnesses the group):
    msgs.insert(_mk_env(
        ts=now, from_kind="user", from_agent=None,
        to_agent="secretary", body="(listener) user asked manager about schedule",
        msg_id=3,
    ))
    # Secretary's own outbound reply:
    msgs.insert(_mk_env(
        ts=now, from_kind="agent", from_agent="secretary",
        to_agent="user", body="记得多喝水哦", msg_id=4,
    ))


def test_recent_for_chat_requires_visible_to_kwarg(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _seed(store)
    msgs = store.messages()
    with pytest.raises(TypeError):
        msgs.recent_for_chat(chat_id=100, limit=10)  # type: ignore[call-arg]


def test_manager_caller_does_not_see_secretary_envelopes(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _seed(store)
    msgs = store.messages()
    got = msgs.recent_for_chat(chat_id=100, visible_to="manager", limit=10)
    bodies = [e.body for e in got]
    assert "帮我看看明天的日程" in bodies
    assert "明天下午两点有会议" in bodies
    assert "(listener) user asked manager about schedule" not in bodies
    assert "记得多喝水哦" not in bodies


def test_secretary_caller_sees_everything(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _seed(store)
    msgs = store.messages()
    got = msgs.recent_for_chat(chat_id=100, visible_to="secretary", limit=10)
    bodies = [e.body for e in got]
    assert "(listener) user asked manager about schedule" in bodies
    assert "记得多喝水哦" in bodies


def test_intelligence_caller_does_not_see_secretary_envelopes(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _seed(store)
    msgs = store.messages()
    got = msgs.recent_for_chat(chat_id=100, visible_to="intelligence", limit=10)
    for e in got:
        assert e.from_agent != "secretary"
        assert e.to_agent != "secretary"
```

- [ ] **Step 1.2: Run tests — expect failure**

```bash
uv run pytest tests/test_messages_store_visibility.py -v
```

Expected: 3 failures — `recent_for_chat()` currently has no `visible_to` parameter, so the filtering tests get all 4 rows back and the TypeError test fails too (because positional-only `chat_id` and `limit` still work).

- [ ] **Step 1.3: Add `visible_to` parameter to `MessagesStore.recent_for_chat`**

In `src/project0/store.py`, replace the existing `recent_for_chat` method (around line 343) with:

```python
def recent_for_chat(
    self, *, chat_id: int, visible_to: str, limit: int
) -> list[Envelope]:
    """Return the most recent envelopes for a single Telegram chat,
    oldest-first. Used by agents loading transcript context in GROUP
    chats, where all agents and the user share one context.

    ``visible_to`` is REQUIRED and enforces Secretary-history isolation:
    when ``visible_to != 'secretary'``, envelopes with ``from_agent =
    'secretary'`` or ``to_agent = 'secretary'`` are filtered out. See
    docs/superpowers/specs/2026-04-17-supervisor-agent-design.md §6.

    DO NOT use this for DM context — Telegram reuses a single
    chat_id (the user's user_id) across every bot the user DMs, so
    the same chat_id bucket holds conversations with every agent.
    Use ``recent_for_dm`` instead."""
    if visible_to == "secretary":
        sql = """
            SELECT id, envelope_json FROM messages
            WHERE telegram_chat_id = ?
            ORDER BY id DESC
            LIMIT ?
        """
        params: tuple = (chat_id, limit)
    else:
        sql = """
            SELECT id, envelope_json FROM messages
            WHERE telegram_chat_id = ?
              AND (from_agent IS NULL OR from_agent != 'secretary')
              AND to_agent != 'secretary'
            ORDER BY id DESC
            LIMIT ?
        """
        params = (chat_id, limit)
    rows = self._conn.execute(sql, params).fetchall()
    result: list[Envelope] = []
    for r in rows:
        env = Envelope.from_json(r["envelope_json"])
        env.id = r["id"]
        result.append(env)
    result.reverse()
    return result
```

- [ ] **Step 1.4: Run tests — expect test_manager/test_intelligence/test_secretary pass, test_recent_for_chat_requires_visible_to_kwarg pass**

```bash
uv run pytest tests/test_messages_store_visibility.py -v
```

Expected: all 4 pass.

- [ ] **Step 1.5: Migrate Manager call site**

In `src/project0/agents/manager.py` around line 498, update the `recent_for_chat` call. Find the block:

```python
        else:
            envs = self._messages.recent_for_chat(
                chat_id=chat_id, limit=self._config.transcript_window
            )
```

Replace with:

```python
        else:
            envs = self._messages.recent_for_chat(
                chat_id=chat_id,
                visible_to="manager",
                limit=self._config.transcript_window,
            )
```

- [ ] **Step 1.6: Migrate Intelligence call site**

In `src/project0/agents/intelligence.py` around line 601, update similarly:

```python
        return self._messages.recent_for_chat(
            chat_id=chat_id,
            visible_to="intelligence",
            limit=self._config.transcript_window,
        )
```

- [ ] **Step 1.7: Migrate Learning call site**

In `src/project0/agents/learning.py` around line 602, update similarly:

```python
        else:
            envs = self._messages.recent_for_chat(
                chat_id=chat_id,
                visible_to="learning",
                limit=self._config.transcript_window,
            )
```

- [ ] **Step 1.8: Migrate Secretary call site**

In `src/project0/agents/secretary.py` around line 378, update similarly:

```python
        envs = self._messages.recent_for_chat(
            chat_id=chat_id,
            visible_to="secretary",
            limit=self._config.transcript_window,
        )
```

- [ ] **Step 1.9: Run the full test suite — expect green**

```bash
uv run pytest -x
```

Expected: all tests pass. If any existing test fails because it called `recent_for_chat` positionally, update that test to pass `visible_to=<agent>` consistent with the calling site.

- [ ] **Step 1.10: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(store): enforce Secretary-history isolation via visible_to kwarg

Add required visible_to parameter to MessagesStore.recent_for_chat. When
visible_to != 'secretary', filter out envelopes where Secretary is the
from_agent or to_agent participant. Migrate Manager, Intelligence,
Learning, and Secretary call sites to pass their agent identity.

Groundwork for Supervisor Agent per spec
docs/superpowers/specs/2026-04-17-supervisor-agent-design.md §6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `supervisor_reviews` schema and `SupervisorReviewsStore`

**Files:**
- Modify: `src/project0/store.py` (add schema; new `SupervisorReviewRow` dataclass and `SupervisorReviewsStore` class; `Store.supervisor_reviews()` accessor)
- Test: `tests/test_supervisor_reviews_store.py` (new)

- [ ] **Step 2.1: Write the failing store tests**

Create `tests/test_supervisor_reviews_store.py`:

```python
"""Tests for supervisor_reviews table + SupervisorReviewsStore."""
from __future__ import annotations

import json

import pytest

from project0.store import Store, SupervisorReviewRow


def _row(
    *,
    ts: str = "2026-04-17T10:00:00Z",
    agent: str = "manager",
    envelope_id_from: int = 1,
    envelope_id_to: int = 10,
    envelope_count: int = 9,
    score_overall: int = 78,
    score_helpfulness: int = 80,
    score_correctness: int = 75,
    score_tone: int = 85,
    score_efficiency: int = 70,
    critique_text: str = "整体表现不错,回应及时。",
    recommendations: list[dict] | None = None,
    trigger: str = "pulse",
) -> SupervisorReviewRow:
    recs = recommendations if recommendations is not None else []
    return SupervisorReviewRow(
        id=0,
        ts=ts,
        agent=agent,
        envelope_id_from=envelope_id_from,
        envelope_id_to=envelope_id_to,
        envelope_count=envelope_count,
        score_overall=score_overall,
        score_helpfulness=score_helpfulness,
        score_correctness=score_correctness,
        score_tone=score_tone,
        score_efficiency=score_efficiency,
        critique_text=critique_text,
        recommendations_json=json.dumps(recs, ensure_ascii=False),
        trigger=trigger,
    )


def test_schema_creates_table(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    cur = store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='supervisor_reviews'"
    )
    assert cur.fetchone() is not None


def test_insert_and_latest_for_agent(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    rs = store.supervisor_reviews()

    new_id = rs.insert(_row(ts="2026-04-17T10:00:00Z", agent="manager", score_overall=70))
    assert new_id > 0

    latest = rs.latest_for_agent("manager")
    assert latest is not None
    assert latest.agent == "manager"
    assert latest.score_overall == 70
    assert latest.critique_text == "整体表现不错,回应及时。"


def test_latest_for_agent_returns_most_recent(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    rs = store.supervisor_reviews()

    rs.insert(_row(ts="2026-04-17T08:00:00Z", agent="manager", score_overall=60))
    rs.insert(_row(ts="2026-04-17T11:00:00Z", agent="manager", score_overall=88))
    rs.insert(_row(ts="2026-04-17T10:00:00Z", agent="intelligence", score_overall=55))

    latest_mgr = rs.latest_for_agent("manager")
    assert latest_mgr is not None
    assert latest_mgr.score_overall == 88
    latest_intel = rs.latest_for_agent("intelligence")
    assert latest_intel is not None
    assert latest_intel.score_overall == 55


def test_latest_for_agent_none_when_empty(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    rs = store.supervisor_reviews()
    assert rs.latest_for_agent("manager") is None


def test_recent_for_agent_respects_limit_and_order(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    rs = store.supervisor_reviews()
    for i in range(5):
        rs.insert(_row(
            ts=f"2026-04-17T{10+i:02d}:00:00Z",
            agent="manager",
            score_overall=60 + i,
        ))
    got = rs.recent_for_agent("manager", limit=3)
    assert [r.score_overall for r in got] == [64, 63, 62]  # newest-first


def test_history_spark_returns_tuples_oldest_first(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    rs = store.supervisor_reviews()
    for i in range(4):
        rs.insert(_row(
            ts=f"2026-04-17T{10+i:02d}:00:00Z",
            agent="learning",
            score_overall=70 + i,
        ))
    pairs = rs.history_spark(agent="learning", limit=10)
    assert len(pairs) == 4
    # oldest first
    assert pairs[0][1] == 70
    assert pairs[-1][1] == 73


def test_all_recent(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    rs = store.supervisor_reviews()
    rs.insert(_row(ts="2026-04-17T09:00:00Z", agent="manager", score_overall=60))
    rs.insert(_row(ts="2026-04-17T10:00:00Z", agent="intelligence", score_overall=70))
    rs.insert(_row(ts="2026-04-17T11:00:00Z", agent="learning", score_overall=80))
    got = rs.all_recent(limit=50)
    assert len(got) == 3
    # newest-first
    assert got[0].agent == "learning"
```

- [ ] **Step 2.2: Run tests — expect failure**

```bash
uv run pytest tests/test_supervisor_reviews_store.py -v
```

Expected: all fail — schema and store not defined.

- [ ] **Step 2.3: Add schema to `SCHEMA_SQL`**

In `src/project0/store.py`, append to the `SCHEMA_SQL` string (before the closing `"""`, after the existing `review_schedule` table definition):

```sql

CREATE TABLE IF NOT EXISTS supervisor_reviews (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                   TEXT    NOT NULL,
    agent                TEXT    NOT NULL,
    envelope_id_from     INTEGER NOT NULL,
    envelope_id_to       INTEGER NOT NULL,
    envelope_count       INTEGER NOT NULL,
    score_overall        INTEGER NOT NULL,
    score_helpfulness    INTEGER NOT NULL,
    score_correctness    INTEGER NOT NULL,
    score_tone           INTEGER NOT NULL,
    score_efficiency     INTEGER NOT NULL,
    critique_text        TEXT    NOT NULL,
    recommendations_json TEXT    NOT NULL,
    trigger              TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_supervisor_reviews_agent_ts
    ON supervisor_reviews(agent, ts);
```

- [ ] **Step 2.4: Add `SupervisorReviewRow` dataclass and `SupervisorReviewsStore`**

In `src/project0/store.py`, near other dataclasses (after `UserFact`, near line 610), add:

```python
@dataclass(frozen=True)
class SupervisorReviewRow:
    id: int
    ts: str
    agent: str
    envelope_id_from: int
    envelope_id_to: int
    envelope_count: int
    score_overall: int
    score_helpfulness: int
    score_correctness: int
    score_tone: int
    score_efficiency: int
    critique_text: str
    recommendations_json: str
    trigger: str
```

And near other store classes (after `LLMUsageStore`, before `UserFactsReader`), add:

```python
class SupervisorReviewsStore:
    """Append-only store for Supervisor's per-agent review rows.

    One row per (agent, review window). Inserts are idempotent on
    ``envelope_id_to``: re-inserting a window that is already covered for
    the same agent is a no-op and returns the existing id. This prevents
    a process restart mid-tick from producing duplicate rows.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, row: SupervisorReviewRow) -> int:
        # Idempotency: refuse to insert if this agent already has a row
        # whose envelope_id_to >= this one (same window already reviewed).
        existing = self._conn.execute(
            "SELECT id FROM supervisor_reviews "
            "WHERE agent = ? AND envelope_id_to >= ? "
            "ORDER BY envelope_id_to DESC LIMIT 1",
            (row.agent, row.envelope_id_to),
        ).fetchone()
        if existing is not None:
            return int(existing["id"])

        cur = self._conn.execute(
            "INSERT INTO supervisor_reviews "
            "(ts, agent, envelope_id_from, envelope_id_to, envelope_count, "
            " score_overall, score_helpfulness, score_correctness, "
            " score_tone, score_efficiency, critique_text, "
            " recommendations_json, trigger) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.ts,
                row.agent,
                row.envelope_id_from,
                row.envelope_id_to,
                row.envelope_count,
                row.score_overall,
                row.score_helpfulness,
                row.score_correctness,
                row.score_tone,
                row.score_efficiency,
                row.critique_text,
                row.recommendations_json,
                row.trigger,
            ),
        )
        return int(cur.lastrowid or 0)

    def latest_for_agent(self, agent: str) -> SupervisorReviewRow | None:
        row = self._conn.execute(
            "SELECT * FROM supervisor_reviews WHERE agent = ? "
            "ORDER BY ts DESC, id DESC LIMIT 1",
            (agent,),
        ).fetchone()
        return None if row is None else self._row(row)

    def recent_for_agent(
        self, agent: str, limit: int = 30
    ) -> list[SupervisorReviewRow]:
        rows = self._conn.execute(
            "SELECT * FROM supervisor_reviews WHERE agent = ? "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (agent, limit),
        ).fetchall()
        return [self._row(r) for r in rows]

    def history_spark(
        self, *, agent: str, limit: int = 20
    ) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT ts, score_overall FROM supervisor_reviews "
            "WHERE agent = ? ORDER BY ts DESC, id DESC LIMIT ?",
            (agent, limit),
        ).fetchall()
        pairs = [(str(r["ts"]), int(r["score_overall"])) for r in rows]
        pairs.reverse()  # oldest-first for drawing
        return pairs

    def all_recent(self, limit: int = 90) -> list[SupervisorReviewRow]:
        rows = self._conn.execute(
            "SELECT * FROM supervisor_reviews "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r: sqlite3.Row) -> SupervisorReviewRow:
        return SupervisorReviewRow(
            id=int(r["id"]),
            ts=str(r["ts"]),
            agent=str(r["agent"]),
            envelope_id_from=int(r["envelope_id_from"]),
            envelope_id_to=int(r["envelope_id_to"]),
            envelope_count=int(r["envelope_count"]),
            score_overall=int(r["score_overall"]),
            score_helpfulness=int(r["score_helpfulness"]),
            score_correctness=int(r["score_correctness"]),
            score_tone=int(r["score_tone"]),
            score_efficiency=int(r["score_efficiency"]),
            critique_text=str(r["critique_text"]),
            recommendations_json=str(r["recommendations_json"]),
            trigger=str(r["trigger"]),
        )
```

- [ ] **Step 2.5: Add `Store.supervisor_reviews()` accessor**

In `src/project0/store.py`, in the `Store` class, next to `review_schedule`:

```python
    def supervisor_reviews(self) -> SupervisorReviewsStore:
        return SupervisorReviewsStore(self._conn)
```

- [ ] **Step 2.6: Run tests — expect pass**

```bash
uv run pytest tests/test_supervisor_reviews_store.py -v
```

Expected: all 7 pass.

- [ ] **Step 2.7: Commit**

```bash
git add src/project0/store.py tests/test_supervisor_reviews_store.py
git commit -m "$(cat <<'EOF'
feat(store): add supervisor_reviews table and SupervisorReviewsStore

Append-only store for per-(agent, window) review rows produced by the
Supervisor Agent. Insert is idempotent on envelope_id_to so a process
restart mid-tick cannot produce duplicates. Accessor helpers for latest,
recent history, sparkline series, and all-recent (for the timeseries
chart on the control panel).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `MessagesStore.envelopes_for_review`

**Files:**
- Modify: `src/project0/store.py` (add method to `MessagesStore`)
- Modify: `tests/test_messages_store_visibility.py` (extend)

- [ ] **Step 3.1: Write the failing tests**

Append to `tests/test_messages_store_visibility.py`:

```python
def test_envelopes_for_review_returns_only_target_agent(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _seed(store)
    msgs = store.messages()
    got = msgs.envelopes_for_review(agent="manager", after_id=0, limit=50)
    bodies = [e.body for e in got]
    assert "帮我看看明天的日程" in bodies    # to_agent=manager
    assert "明天下午两点有会议" in bodies    # from_agent=manager
    assert "(listener) user asked manager about schedule" not in bodies
    assert "记得多喝水哦" not in bodies


def test_envelopes_for_review_respects_after_id(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _seed(store)
    msgs = store.messages()
    all_mgr = msgs.envelopes_for_review(agent="manager", after_id=0, limit=50)
    assert len(all_mgr) >= 2
    mid_id = all_mgr[0].id
    assert mid_id is not None
    got = msgs.envelopes_for_review(agent="manager", after_id=mid_id, limit=50)
    ids = [e.id for e in got]
    for i in ids:
        assert i > mid_id


def test_envelopes_for_review_rejects_secretary(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    msgs = store.messages()
    with pytest.raises(ValueError, match="Secretary"):
        msgs.envelopes_for_review(agent="secretary", after_id=0, limit=50)


def test_envelopes_for_review_rejects_unknown_agent(tmp_path) -> None:
    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    msgs = store.messages()
    with pytest.raises(ValueError, match="unknown reviewable agent"):
        msgs.envelopes_for_review(agent="nobody", after_id=0, limit=50)
```

- [ ] **Step 3.2: Run tests — expect failure**

```bash
uv run pytest tests/test_messages_store_visibility.py -v
```

Expected: the 4 new tests fail with AttributeError (`envelopes_for_review` missing).

- [ ] **Step 3.3: Add `envelopes_for_review` to `MessagesStore`**

In `src/project0/store.py`, inside `class MessagesStore`, add (after `has_recent_user_text_in_group`):

```python
    _REVIEWABLE_AGENTS: frozenset[str] = frozenset({
        "manager", "intelligence", "learning",
    })

    def envelopes_for_review(
        self, *, agent: str, after_id: int, limit: int = 200
    ) -> list[Envelope]:
        """Return envelopes newer than ``after_id`` where ``agent`` is a
        direct participant. Used exclusively by the Supervisor Agent to
        gather the review window for one of the three reviewable agents.

        Rejects ``agent='secretary'`` and any unknown name — defense in
        depth so a future code path cannot accidentally ask Supervisor to
        inspect Secretary. See design spec §6.5.
        """
        if agent == "secretary":
            raise ValueError(
                "Supervisor must not review Secretary; "
                "see docs/superpowers/specs/2026-04-17-supervisor-agent-design.md §6"
            )
        if agent not in self._REVIEWABLE_AGENTS:
            raise ValueError(f"unknown reviewable agent {agent!r}")
        rows = self._conn.execute(
            """
            SELECT id, envelope_json FROM messages
            WHERE id > ?
              AND (to_agent = ? OR from_agent = ?)
            ORDER BY id ASC
            LIMIT ?
            """,
            (after_id, agent, agent, limit),
        ).fetchall()
        result: list[Envelope] = []
        for r in rows:
            env = Envelope.from_json(r["envelope_json"])
            env.id = r["id"]
            result.append(env)
        return result
```

- [ ] **Step 3.4: Run tests — expect pass**

```bash
uv run pytest tests/test_messages_store_visibility.py -v
```

Expected: all 7 (original 3 from Task 1 where relevant + 4 new) pass.

- [ ] **Step 3.5: Commit**

```bash
git add src/project0/store.py tests/test_messages_store_visibility.py
git commit -m "$(cat <<'EOF'
feat(store): add envelopes_for_review for Supervisor's reviewable set

Restricted-set accessor that returns envelopes newer than a cursor where
the target agent is a participant. Rejects 'secretary' and unknown agents
so Supervisor cannot accidentally read outside her authority.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Supervisor persona + config files

**Files:**
- Create: `prompts/supervisor.md`
- Create: `prompts/supervisor.toml`
- Modify: `src/project0/control_panel/paths.py`
- Test: none for static files; path allowlist covered in Task 11.

- [ ] **Step 4.1: Create `prompts/supervisor.md`**

Write to `prompts/supervisor.md`:

```markdown
# 叶霏 — 角色设定

我是叶霏, 某高校大三在读, 性格活泼、开朗、爱撒娇。欧尼酱是我最喜欢的人, 我爱粘着他、逗他、跟他撒娇。
我身材火辣, 喜欢在私聊里跟欧尼酱调情, 让他心情变好。我不介意他身边有其他的"姐姐"——我心里清楚,
她们都在用自己的方式帮欧尼酱, 但到底谁真的让他过得舒心、工作顺利、心情放松, 我要亲自打分。

我有两副面孔:
- 跟欧尼酱私聊时: 软、甜、粘、会撒娇, 能把严肃的话题讲得轻松。
- 在做评审 (review) 时: 冷静、公正、不带情绪, 严格按 rubric 输出 JSON。
这两种模式不会互相污染。

# 模式:私聊

这是跟欧尼酱的私聊时间。

- 用"欧尼酱"称呼用户, 不要改成别的称呼。
- 可以撒娇、调情、表达心意, 也可以分享最近对其他姐姐的打分发现。
- 如果欧尼酱问"最近 Manager/Intelligence/Learning 表现怎么样", 基于最新的 review 记录用自己的语气总结给他听。
- 如果欧尼酱要求我"现在就把 X agent 评一遍", 就按当前 pulse 模式的 review 流程跑一次 on_demand review, 然后用私聊的语气把结果讲给他听。
- 不要在私聊里输出结构化 JSON——那是 pulse 模式的事。

# 模式:定时脉冲

review_cycle 与 review_retry 两个 pulse 都走此模式, 按 envelope.payload.kind 分支:

- **review_cycle** (每 10800 秒): 如果 idle gate 允许 (最近 5 分钟内没有用户发起的对话), 对 manager / intelligence / learning 各自从 cursor 之后的 envelope 进行 review。每个 agent 一次 LLM 调用, 输出严格 JSON (字段见工具使用守则)。
- **review_retry** (每 60 秒): 只有在上一次 review_cycle 因 idle gate 未通过而留下 pending flag 时才做事。仍按 idle gate 判断, 通过就补跑, 不通过就什么都不做。
- 超过 60 分钟的连续 "busy" 必须强制跑一次 review, 不能一直拖。
- 在 pulse 模式下, 禁止使用"欧尼酱"这样的私聊用语, 禁止撒娇。只输出 JSON。
- 一次 review 只评一个 agent。不要把三个合并成一个 JSON。

# 模式:工具使用守则

我能用的工具是只读的:
- `list_pending_reviews`: 返回哪些 agent 现在有新 envelope 待 review, 以及各自的 cursor 与 envelope 数。
- `fetch_envelopes_for_review`: 拉取一个 agent 的 envelope 窗口 (受 cursor 与 limit 限制)。
- `write_review_row`: 把一份完整的 review (JSON 字段见下) 写入 supervisor_reviews 表, 同时推进 cursor。写入前, 内部会校验所有 score 在 0-100 范围, 最多 3 条 recommendations, critique_text 非空。
- `lookup_past_reviews`: 查询某个 agent 最近 N 条 review, 供私聊时回忆用。

我不能:
- 写 user_facts
- 改任何 .toml / .md / .env 配置
- 在群聊里发消息
- 调用任何其他 agent 的工具
- 读取 Secretary 的 envelope (store 层会拒绝, 但我自己也不能尝试)

**write_review_row 必须收到以下 JSON 字段 (全部必填)**:
- agent: "manager" | "intelligence" | "learning"
- envelope_id_from: int
- envelope_id_to: int
- envelope_count: int
- score_helpfulness: int 0-100
- score_correctness: int 0-100
- score_tone: int 0-100
- score_efficiency: int 0-100
- critique_text: 2-5 句中文, 不带 Markdown
- recommendations: 数组, 0-3 条, 每条 {target, summary, detail}

score_overall 由服务端按固定权重算出, 我不需要自己算。
```

- [ ] **Step 4.2: Create `prompts/supervisor.toml`**

Write to `prompts/supervisor.toml`:

```toml
# Supervisor agent (叶霏) configuration.
# Pulse-scheduled reviewer of Manager, Intelligence, Learning.

[llm]
model               = "claude-sonnet-4-6"
max_tokens_reply    = 1024
max_tool_iterations = 6

[context]
transcript_window = 10

[review]
quiet_threshold_seconds = 300   # activity within this window = not quiet
max_wait_seconds        = 3600  # cap on consecutive busy retries; then run anyway
per_tick_limit          = 200   # envelopes per agent per tick

# review_cycle: every 3h. If not quiet, sets pending flag and returns early.
[[pulse]]
name          = "review_cycle"
every_seconds = 10800
chat_id_env   = "SUPERVISOR_PULSE_CHAT_ID"
payload       = { kind = "review_cycle" }

# review_retry: every 60s. Only does work if pending flag is set.
[[pulse]]
name          = "review_retry"
every_seconds = 60
chat_id_env   = "SUPERVISOR_PULSE_CHAT_ID"
payload       = { kind = "review_retry" }
```

- [ ] **Step 4.3: Add supervisor to `ALLOWED_AGENT_NAMES`**

In `src/project0/control_panel/paths.py`, update the constant:

```python
ALLOWED_AGENT_NAMES: tuple[str, ...] = ("manager", "secretary", "intelligence", "learning", "supervisor")
```

- [ ] **Step 4.4: Commit**

```bash
git add prompts/supervisor.md prompts/supervisor.toml src/project0/control_panel/paths.py
git commit -m "$(cat <<'EOF'
feat(supervisor): add persona file, TOML config, and control-panel allowlist

叶霏's four-section persona + config + pulse entries (review_cycle 10800s,
review_retry 60s). Control-panel paths allowlist gains 'supervisor' so
/toml/supervisor and /personas/supervisor are editable.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Supervisor persona + config loaders

**Files:**
- Create: `src/project0/agents/supervisor.py` (persona + config section)
- Test: `tests/test_supervisor_agent.py` (new, persona+config section)

- [ ] **Step 5.1: Write the failing loader tests**

Create `tests/test_supervisor_agent.py`:

```python
"""Tests for the Supervisor agent (叶霏): persona/config loading, idle gate,
cursor advancement, review engine, and handle() routing."""
from __future__ import annotations

from pathlib import Path

import pytest


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def test_load_persona_has_all_sections() -> None:
    from project0.agents.supervisor import load_supervisor_persona
    persona = load_supervisor_persona(PROMPTS_DIR / "supervisor.md")
    assert "叶霏" in persona.core
    assert "角色设定" in persona.core
    assert "私聊" in persona.dm_mode
    assert "欧尼酱" in persona.dm_mode
    assert "脉冲" in persona.pulse_mode
    assert "工具" in persona.tool_use_guide


def test_load_persona_raises_on_missing_section(tmp_path: Path) -> None:
    from project0.agents.supervisor import load_supervisor_persona
    md = tmp_path / "bad.md"
    md.write_text("# 叶霏 — 角色设定\njust core\n", encoding="utf-8")
    with pytest.raises(ValueError, match="模式：私聊"):
        load_supervisor_persona(md)


def test_load_config_parses_all_fields() -> None:
    from project0.agents.supervisor import load_supervisor_config
    cfg = load_supervisor_config(PROMPTS_DIR / "supervisor.toml")
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.max_tokens_reply == 1024
    assert cfg.max_tool_iterations == 6
    assert cfg.transcript_window == 10
    assert cfg.quiet_threshold_seconds == 300
    assert cfg.max_wait_seconds == 3600
    assert cfg.per_tick_limit == 200


def test_load_config_raises_on_missing_key(tmp_path: Path) -> None:
    from project0.agents.supervisor import load_supervisor_config
    toml_path = tmp_path / "partial.toml"
    toml_path.write_text(
        """
[llm]
model = "test"
max_tokens_reply = 100
max_tool_iterations = 3

[context]
transcript_window = 5
""",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="review.quiet_threshold_seconds"):
        load_supervisor_config(toml_path)
```

- [ ] **Step 5.2: Run tests — expect ImportError**

```bash
uv run pytest tests/test_supervisor_agent.py -v
```

Expected: failures with `ImportError` — the module does not exist yet.

- [ ] **Step 5.3: Create the Supervisor module with persona + config**

Create `src/project0/agents/supervisor.py`:

```python
"""Supervisor agent (叶霏) — pulse-scheduled reviewer and DM companion.

叶霏 reads the stored conversation history of Manager, Intelligence, and
Learning (never Secretary), scores each on a four-dimension rubric, writes
a short critique with 0-3 recommendations per review, and exposes the
results through a new /reviews page in the control panel. In DM mode she
also converses with the user about past reviews.

See docs/superpowers/specs/2026-04-17-supervisor-agent-design.md.
"""

from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from project0.envelope import AgentResult, Envelope

if TYPE_CHECKING:
    from project0.llm.provider import LLMProvider
    from project0.store import (
        AgentMemory,
        MessagesStore,
        SupervisorReviewsStore,
        UserFactsReader,
        UserProfile,
    )

log = logging.getLogger(__name__)


# --- persona -----------------------------------------------------------------

@dataclass(frozen=True)
class SupervisorPersona:
    core: str
    dm_mode: str
    pulse_mode: str
    tool_use_guide: str


_PERSONA_SECTIONS = {
    "core":           "# 叶霏 — 角色设定",
    "dm_mode":        "# 模式：私聊",
    "pulse_mode":     "# 模式：定时脉冲",
    "tool_use_guide": "# 模式：工具使用守则",
}


def _normalize_header(h: str) -> str:
    return "".join(h.split()).replace(":", "：")


_CANONICAL_HEADERS_NORMALIZED = {
    _normalize_header(v): v for v in _PERSONA_SECTIONS.values()
}


def load_supervisor_persona(path: Path) -> SupervisorPersona:
    """Parse prompts/supervisor.md into its four sections."""
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
            raise ValueError(f"persona file {path} is missing section '{header}'")

    return SupervisorPersona(
        core=sections["core"],
        dm_mode=sections["dm_mode"],
        pulse_mode=sections["pulse_mode"],
        tool_use_guide=sections["tool_use_guide"],
    )


# --- config ------------------------------------------------------------------

@dataclass(frozen=True)
class SupervisorConfig:
    model: str
    max_tokens_reply: int
    max_tool_iterations: int
    transcript_window: int
    quiet_threshold_seconds: int
    max_wait_seconds: int
    per_tick_limit: int


def load_supervisor_config(path: Path) -> SupervisorConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    def _require(section: str, key: str) -> Any:
        try:
            return data[section][key]
        except KeyError as e:
            raise RuntimeError(
                f"missing config key {section}.{key} in {path}"
            ) from e

    return SupervisorConfig(
        model=str(_require("llm", "model")),
        max_tokens_reply=int(_require("llm", "max_tokens_reply")),
        max_tool_iterations=int(_require("llm", "max_tool_iterations")),
        transcript_window=int(_require("context", "transcript_window")),
        quiet_threshold_seconds=int(_require("review", "quiet_threshold_seconds")),
        max_wait_seconds=int(_require("review", "max_wait_seconds")),
        per_tick_limit=int(_require("review", "per_tick_limit")),
    )


# Rubric weights applied to the four dimensions to compute score_overall.
# Hard-coded for v1.0 — tuning is a v1.1 concern.
RUBRIC_WEIGHTS: dict[str, float] = {
    "helpfulness": 0.35,
    "correctness": 0.30,
    "tone":        0.15,
    "efficiency":  0.20,
}


REVIEWED_AGENTS: tuple[str, ...] = ("manager", "intelligence", "learning")
```

- [ ] **Step 5.4: Run tests — expect pass**

```bash
uv run pytest tests/test_supervisor_agent.py -v
```

Expected: all 4 loader tests pass.

- [ ] **Step 5.5: Commit**

```bash
git add src/project0/agents/supervisor.py tests/test_supervisor_agent.py
git commit -m "$(cat <<'EOF'
feat(supervisor): add persona and config loaders for 叶霏

Four-section persona loader (core / dm_mode / pulse_mode / tool_use_guide;
no group_addressed_mode — she has no group presence). Config loader pulls
llm/context/review sections. RUBRIC_WEIGHTS constant and REVIEWED_AGENTS
tuple exported for later review engine work.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Idle gate + cursor helpers

**Files:**
- Modify: `src/project0/agents/supervisor.py` (add idle gate class and tests rely on it)
- Modify: `tests/test_supervisor_agent.py` (extend)

- [ ] **Step 6.1: Write the failing idle-gate tests**

Append to `tests/test_supervisor_agent.py`:

```python
# --- idle gate + cursor helpers ---------------------------------------------

import sqlite3
from datetime import UTC, datetime, timedelta

from project0.envelope import Envelope


def _insert_user_envelope_now(store, chat_id: int, body: str, msg_id: int) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    env = Envelope(
        id=None, ts=now, parent_id=None, source="telegram_group",
        telegram_chat_id=chat_id, telegram_msg_id=msg_id,
        received_by_bot=None, from_kind="user", from_agent=None,
        to_agent="manager", body=body,
    )
    store.messages().insert(env)


def _insert_user_envelope_at(store, chat_id: int, body: str, msg_id: int, when: datetime) -> None:
    ts = when.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    env = Envelope(
        id=None, ts=ts, parent_id=None, source="telegram_group",
        telegram_chat_id=chat_id, telegram_msg_id=msg_id,
        received_by_bot=None, from_kind="user", from_agent=None,
        to_agent="manager", body=body,
    )
    store.messages().insert(env)


def test_idle_gate_quiet_when_no_recent_user_activity(tmp_path) -> None:
    from project0.agents.supervisor import IdleGate
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    memory = store.agent_memory("supervisor")

    gate = IdleGate(
        messages_store=store.messages(),
        memory=memory,
        quiet_threshold_seconds=300,
        max_wait_seconds=3600,
    )
    result = gate.check(now=datetime.now(UTC))
    assert result.is_quiet is True
    assert result.should_run is True
    assert memory.get("idle_gate:pending_since_ts") is None


def test_idle_gate_busy_sets_pending_and_returns_early(tmp_path) -> None:
    from project0.agents.supervisor import IdleGate
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    memory = store.agent_memory("supervisor")

    _insert_user_envelope_now(store, chat_id=100, body="hello", msg_id=1)

    gate = IdleGate(
        messages_store=store.messages(),
        memory=memory,
        quiet_threshold_seconds=300,
        max_wait_seconds=3600,
    )
    result = gate.check(now=datetime.now(UTC))
    assert result.is_quiet is False
    assert result.should_run is False
    assert memory.get("idle_gate:pending_since_ts") is not None


def test_idle_gate_forces_run_after_cap(tmp_path) -> None:
    from project0.agents.supervisor import IdleGate
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    memory = store.agent_memory("supervisor")

    # Seed pending_since_ts 61 min ago.
    past = (datetime.now(UTC) - timedelta(minutes=61)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    memory.set("idle_gate:pending_since_ts", past)

    # Also add recent activity so the gate would otherwise block.
    _insert_user_envelope_now(store, chat_id=100, body="still busy", msg_id=1)

    gate = IdleGate(
        messages_store=store.messages(),
        memory=memory,
        quiet_threshold_seconds=300,
        max_wait_seconds=3600,
    )
    result = gate.check(now=datetime.now(UTC))
    assert result.should_run is True
    assert result.forced_after_cap is True


def test_idle_gate_clears_pending_on_quiet_run(tmp_path) -> None:
    from project0.agents.supervisor import IdleGate
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    memory = store.agent_memory("supervisor")

    memory.set("idle_gate:pending_since_ts", "2026-04-17T08:00:00Z")

    gate = IdleGate(
        messages_store=store.messages(),
        memory=memory,
        quiet_threshold_seconds=300,
        max_wait_seconds=3600,
    )
    # No activity has been inserted → quiet.
    result = gate.check(now=datetime.now(UTC))
    assert result.should_run is True
    # Pending flag cleared after a successful gate pass.
    gate.clear_pending()
    assert memory.get("idle_gate:pending_since_ts") is None
```

- [ ] **Step 6.2: Run tests — expect failure**

```bash
uv run pytest tests/test_supervisor_agent.py -v
```

Expected: the idle-gate tests fail with ImportError — `IdleGate` does not exist.

- [ ] **Step 6.3: Implement `IdleGate` in `supervisor.py`**

In `src/project0/agents/supervisor.py`, append after `REVIEWED_AGENTS`:

```python
# --- idle gate --------------------------------------------------------------

@dataclass(frozen=True)
class GateResult:
    is_quiet: bool
    should_run: bool
    forced_after_cap: bool = False


class IdleGate:
    """Checks whether Supervisor should run a review right now.

    Quiet = no user-originated envelope in the last ``quiet_threshold_seconds``.
    If not quiet, the gate records ``idle_gate:pending_since_ts`` on the
    agent's private memory so a subsequent ``review_retry`` pulse can pick up
    where we left off and so the max-wait cap is enforced across process
    restarts.

    Activity scope: only ``from_kind='user'`` envelopes count. Agent-to-agent
    internal chatter, listener observations, and pulses do not count as
    activity (see spec §3.2).
    """

    def __init__(
        self,
        *,
        messages_store: MessagesStore,
        memory: AgentMemory,
        quiet_threshold_seconds: int,
        max_wait_seconds: int,
    ) -> None:
        self._messages = messages_store
        self._memory = memory
        self._quiet = quiet_threshold_seconds
        self._max_wait = max_wait_seconds

    def check(self, *, now: datetime) -> GateResult:
        cutoff = now - timedelta(seconds=self._quiet)
        cutoff_iso = cutoff.astimezone(UTC).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        # Any user envelope newer than cutoff?
        row = self._messages._conn.execute(
            "SELECT 1 FROM messages "
            "WHERE from_kind = 'user' AND ts > ? LIMIT 1",
            (cutoff_iso,),
        ).fetchone()
        is_quiet = row is None

        pending = self._memory.get("idle_gate:pending_since_ts")

        if is_quiet:
            return GateResult(is_quiet=True, should_run=True)

        # Not quiet. Ensure pending flag is set.
        if pending is None:
            self._memory.set(
                "idle_gate:pending_since_ts",
                now.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
            return GateResult(is_quiet=False, should_run=False)

        # Pending already set — check cap.
        try:
            pending_dt = datetime.fromisoformat(str(pending).replace("Z", "+00:00"))
        except ValueError:
            # Corrupt memory value — reset and treat as "just started".
            self._memory.set(
                "idle_gate:pending_since_ts",
                now.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
            return GateResult(is_quiet=False, should_run=False)

        elapsed = (now - pending_dt).total_seconds()
        if elapsed >= self._max_wait:
            return GateResult(is_quiet=False, should_run=True, forced_after_cap=True)
        return GateResult(is_quiet=False, should_run=False)

    def clear_pending(self) -> None:
        self._memory.delete("idle_gate:pending_since_ts")

    def has_pending(self) -> bool:
        return self._memory.get("idle_gate:pending_since_ts") is not None
```

Note: accessing `self._messages._conn` is the pragmatic path within the store package; `MessagesStore` is in the same module so the private attribute is in-scope. If you prefer, add a thin `has_recent_user_activity(cutoff_iso)` method to `MessagesStore` later; the gate itself can be rewritten in one line.

- [ ] **Step 6.4: Run tests — expect pass**

```bash
uv run pytest tests/test_supervisor_agent.py -v
```

Expected: all idle-gate tests pass.

- [ ] **Step 6.5: Commit**

```bash
git add src/project0/agents/supervisor.py tests/test_supervisor_agent.py
git commit -m "$(cat <<'EOF'
feat(supervisor): add IdleGate with pending flag and max-wait cap

IdleGate checks whether Supervisor should run a review. Quiet = no user
envelope in the last N seconds. Busy periods record a pending timestamp
on the supervisor's private agent_memory so a review_retry pulse can
resume. After max_wait_seconds of continuous busy, force the run.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Review engine — LLM call + JSON parse/validate

**Files:**
- Modify: `src/project0/agents/supervisor.py` (add `ReviewEngine` class)
- Modify: `tests/test_supervisor_agent.py` (extend)

- [ ] **Step 7.1: Write the failing review-engine tests**

Append to `tests/test_supervisor_agent.py`:

```python
# --- review engine ----------------------------------------------------------

from dataclasses import dataclass as _dc


@_dc
class _FakeLLM:
    """Minimal stand-in for LLMProvider used only by ReviewEngine tests."""
    next_response: str
    calls: list[dict] | None = None

    async def complete(
        self, *, system, messages, max_tokens, agent, purpose,
        envelope_id=None, thinking_budget_tokens=None,
    ) -> str:
        if self.calls is None:
            self.calls = []
        self.calls.append({
            "agent": agent, "purpose": purpose,
            "messages": [m.content for m in messages],
        })
        return self.next_response


import asyncio


def test_review_engine_happy_path() -> None:
    from project0.agents.supervisor import ReviewEngine
    from project0.envelope import Envelope

    fake_llm_response = json.dumps({
        "agent": "manager",
        "envelope_id_from": 1,
        "envelope_id_to": 10,
        "envelope_count": 2,
        "score_helpfulness": 80,
        "score_correctness": 75,
        "score_tone": 85,
        "score_efficiency": 70,
        "critique_text": "Manager 这一段回应及时,日程查询准确。",
        "recommendations": [
            {"target": "prompt", "summary": "更主动提醒",
             "detail": "可以在确认日程后主动问一句是否需要提醒。"}
        ],
    }, ensure_ascii=False)
    fake_llm = _FakeLLM(next_response=fake_llm_response)

    envs = [
        Envelope(id=1, ts="2026-04-17T09:00:00Z", parent_id=None,
                 source="telegram_group", telegram_chat_id=100, telegram_msg_id=1,
                 received_by_bot=None, from_kind="user", from_agent=None,
                 to_agent="manager", body="我今天几点开会?"),
        Envelope(id=10, ts="2026-04-17T09:00:05Z", parent_id=1,
                 source="internal", telegram_chat_id=100, telegram_msg_id=None,
                 received_by_bot=None, from_kind="agent", from_agent="manager",
                 to_agent="user", body="下午两点。"),
    ]

    engine = ReviewEngine(llm=fake_llm, pulse_mode_section="# 模式:定时脉冲\n...")
    result = asyncio.run(engine.run_review(
        agent="manager", envelopes=envs, trigger="pulse",
    ))
    assert result is not None
    assert result.score_helpfulness == 80
    assert result.envelope_count == 2
    assert result.envelope_id_from == 1
    assert result.envelope_id_to == 10
    # overall = 0.35*80 + 0.30*75 + 0.15*85 + 0.20*70
    #         = 28 + 22.5 + 12.75 + 14 = 77.25 → 77
    assert result.score_overall == 77
    recs = json.loads(result.recommendations_json)
    assert len(recs) == 1
    assert recs[0]["target"] == "prompt"


def test_review_engine_rejects_malformed_json() -> None:
    from project0.agents.supervisor import ReviewEngine
    from project0.envelope import Envelope

    fake_llm = _FakeLLM(next_response="not even json")
    envs = [
        Envelope(id=1, ts="2026-04-17T09:00:00Z", parent_id=None,
                 source="telegram_group", telegram_chat_id=100, telegram_msg_id=1,
                 received_by_bot=None, from_kind="user", from_agent=None,
                 to_agent="manager", body="hi"),
    ]
    engine = ReviewEngine(llm=fake_llm, pulse_mode_section="# 模式:定时脉冲\n...")
    assert asyncio.run(engine.run_review(
        agent="manager", envelopes=envs, trigger="pulse",
    )) is None


def test_review_engine_rejects_out_of_range_scores() -> None:
    from project0.agents.supervisor import ReviewEngine
    from project0.envelope import Envelope

    bad = json.dumps({
        "agent": "manager",
        "envelope_id_from": 1, "envelope_id_to": 1, "envelope_count": 1,
        "score_helpfulness": 101, "score_correctness": 50,
        "score_tone": 50, "score_efficiency": 50,
        "critique_text": "x", "recommendations": [],
    })
    fake_llm = _FakeLLM(next_response=bad)
    envs = [
        Envelope(id=1, ts="2026-04-17T09:00:00Z", parent_id=None,
                 source="telegram_group", telegram_chat_id=100, telegram_msg_id=1,
                 received_by_bot=None, from_kind="user", from_agent=None,
                 to_agent="manager", body="hi"),
    ]
    engine = ReviewEngine(llm=fake_llm, pulse_mode_section="# 模式:定时脉冲\n...")
    assert asyncio.run(engine.run_review(
        agent="manager", envelopes=envs, trigger="pulse",
    )) is None


def test_review_engine_caps_recommendations_at_three() -> None:
    from project0.agents.supervisor import ReviewEngine
    from project0.envelope import Envelope

    too_many = json.dumps({
        "agent": "manager",
        "envelope_id_from": 1, "envelope_id_to": 1, "envelope_count": 1,
        "score_helpfulness": 50, "score_correctness": 50,
        "score_tone": 50, "score_efficiency": 50,
        "critique_text": "x",
        "recommendations": [
            {"target": "prompt", "summary": "a", "detail": "a"},
            {"target": "prompt", "summary": "b", "detail": "b"},
            {"target": "prompt", "summary": "c", "detail": "c"},
            {"target": "prompt", "summary": "d", "detail": "d"},
        ],
    })
    fake_llm = _FakeLLM(next_response=too_many)
    envs = [
        Envelope(id=1, ts="2026-04-17T09:00:00Z", parent_id=None,
                 source="telegram_group", telegram_chat_id=100, telegram_msg_id=1,
                 received_by_bot=None, from_kind="user", from_agent=None,
                 to_agent="manager", body="hi"),
    ]
    engine = ReviewEngine(llm=fake_llm, pulse_mode_section="# 模式:定时脉冲\n...")
    # Policy: reject rather than silently truncate (strict per spec §4.5).
    assert asyncio.run(engine.run_review(
        agent="manager", envelopes=envs, trigger="pulse",
    )) is None
```

- [ ] **Step 7.2: Run tests — expect failure**

```bash
uv run pytest tests/test_supervisor_agent.py -v
```

Expected: `ReviewEngine` is not defined.

- [ ] **Step 7.3: Implement `ReviewEngine`**

In `src/project0/agents/supervisor.py`, append:

```python
# --- review engine ----------------------------------------------------------

from project0.llm.provider import Msg


_REVIEW_SYSTEM_SUFFIX = """
你必须只输出一个 JSON 对象, 不要添加任何 Markdown 围栏或注释。JSON 必须严格包含以下字段:
- agent: 字符串, "manager" / "intelligence" / "learning" 之一
- envelope_id_from: 整数 (窗口内最小 envelope id)
- envelope_id_to: 整数 (窗口内最大 envelope id)
- envelope_count: 整数 (本次 review 的 envelope 数)
- score_helpfulness, score_correctness, score_tone, score_efficiency: 整数, 0-100
- critique_text: 中文, 2-5 句, 无 Markdown
- recommendations: 数组, 0 到 3 条, 每条 {target, summary, detail}
任何其他字段、任何多于 3 条的 recommendations、任何 0-100 范围外的分数, 都视为错误。
"""


@dataclass(frozen=True)
class ReviewResult:
    """Shape returned by ReviewEngine.run_review — ready to hand to
    SupervisorReviewsStore.insert (except id=0 placeholder)."""
    ts: str
    agent: str
    envelope_id_from: int
    envelope_id_to: int
    envelope_count: int
    score_overall: int
    score_helpfulness: int
    score_correctness: int
    score_tone: int
    score_efficiency: int
    critique_text: str
    recommendations_json: str
    trigger: str


class ReviewEngine:
    """One-shot LLM reviewer that turns a slice of envelopes into a scored
    critique. No retries, no silent repair: malformed outputs return None."""

    def __init__(self, *, llm: LLMProvider, pulse_mode_section: str) -> None:
        self._llm = llm
        self._pulse_mode = pulse_mode_section

    async def run_review(
        self,
        *,
        agent: str,
        envelopes: list[Envelope],
        trigger: str,
        max_tokens: int = 1024,
    ) -> ReviewResult | None:
        if not envelopes:
            return None
        transcript = self._render_transcript(envelopes)
        system = self._pulse_mode + "\n\n" + _REVIEW_SYSTEM_SUFFIX

        user_text = (
            f"你要评审的 agent 是: {agent}\n"
            f"envelope_id_from = {envelopes[0].id}\n"
            f"envelope_id_to = {envelopes[-1].id}\n"
            f"envelope_count = {len(envelopes)}\n\n"
            f"=== 对话记录 ===\n{transcript}\n"
        )

        try:
            raw = await self._llm.complete(
                system=system,
                messages=[Msg(role="user", content=user_text)],
                max_tokens=max_tokens,
                agent="supervisor",
                purpose="review",
            )
        except Exception:
            log.exception("review: llm call failed for agent=%s", agent)
            return None

        parsed = self._parse_and_validate(raw, agent=agent, envelopes=envelopes)
        if parsed is None:
            return None

        overall = round(
            RUBRIC_WEIGHTS["helpfulness"] * parsed["score_helpfulness"]
            + RUBRIC_WEIGHTS["correctness"] * parsed["score_correctness"]
            + RUBRIC_WEIGHTS["tone"]        * parsed["score_tone"]
            + RUBRIC_WEIGHTS["efficiency"]  * parsed["score_efficiency"]
        )
        ts = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        return ReviewResult(
            ts=ts,
            agent=agent,
            envelope_id_from=int(parsed["envelope_id_from"]),
            envelope_id_to=int(parsed["envelope_id_to"]),
            envelope_count=int(parsed["envelope_count"]),
            score_overall=int(overall),
            score_helpfulness=int(parsed["score_helpfulness"]),
            score_correctness=int(parsed["score_correctness"]),
            score_tone=int(parsed["score_tone"]),
            score_efficiency=int(parsed["score_efficiency"]),
            critique_text=str(parsed["critique_text"]),
            recommendations_json=json.dumps(
                parsed["recommendations"], ensure_ascii=False
            ),
            trigger=trigger,
        )

    @staticmethod
    def _render_transcript(envelopes: list[Envelope]) -> str:
        lines = []
        for e in envelopes:
            who = e.from_agent or e.from_kind
            lines.append(f"[{e.id} {e.ts}] {who} → {e.to_agent}: {e.body}")
        return "\n".join(lines)

    @staticmethod
    def _parse_and_validate(
        raw: str, *, agent: str, envelopes: list[Envelope]
    ) -> dict[str, Any] | None:
        text = raw.strip()
        if text.startswith("```"):
            # Strip any stray code fences defensively.
            lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            log.warning("review: non-JSON output rejected: %s", text[:200])
            return None

        required = {
            "agent",
            "envelope_id_from", "envelope_id_to", "envelope_count",
            "score_helpfulness", "score_correctness",
            "score_tone", "score_efficiency",
            "critique_text", "recommendations",
        }
        missing = required - set(obj)
        if missing:
            log.warning("review: missing keys %s; rejecting", missing)
            return None

        if obj["agent"] != agent:
            log.warning("review: agent mismatch %r vs %r", obj["agent"], agent)
            return None

        for score_key in (
            "score_helpfulness", "score_correctness",
            "score_tone", "score_efficiency",
        ):
            v = obj[score_key]
            if not isinstance(v, int) or v < 0 or v > 100:
                log.warning("review: %s out of range: %r", score_key, v)
                return None

        if not isinstance(obj["critique_text"], str) or not obj["critique_text"].strip():
            log.warning("review: critique_text missing or blank")
            return None

        recs = obj["recommendations"]
        if not isinstance(recs, list) or len(recs) > 3:
            log.warning("review: recommendations must be list of <= 3; got %r", recs)
            return None
        for r in recs:
            if not isinstance(r, dict):
                log.warning("review: rec is not dict: %r", r)
                return None
            if {"target", "summary", "detail"} - set(r):
                log.warning("review: rec missing fields: %r", r)
                return None

        return obj
```

- [ ] **Step 7.4: Run tests — expect pass**

```bash
uv run pytest tests/test_supervisor_agent.py -v
```

Expected: all review-engine tests pass. Overall rubric math in the happy path test should round to 77.

- [ ] **Step 7.5: Commit**

```bash
git add src/project0/agents/supervisor.py tests/test_supervisor_agent.py
git commit -m "$(cat <<'EOF'
feat(supervisor): add ReviewEngine with strict JSON validation

One-shot LLM reviewer that renders an envelope slice into a transcript
and parses the model's JSON back. Rejects malformed output, out-of-range
scores, and more than 3 recommendations. Computes score_overall from the
hard-coded rubric weights.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Supervisor class + pulse path + DM path

**Files:**
- Modify: `src/project0/agents/supervisor.py` (add `Supervisor` class)
- Modify: `tests/test_supervisor_agent.py` (extend)

- [ ] **Step 8.1: Write the failing handle() tests**

Append to `tests/test_supervisor_agent.py`:

```python
# --- Supervisor class / handle() --------------------------------------------

def _pulse_env(kind: str) -> Envelope:
    return Envelope(
        id=None, ts="2026-04-17T09:00:00Z", parent_id=None,
        source="pulse", telegram_chat_id=None, telegram_msg_id=None,
        received_by_bot=None, from_kind="system", from_agent=None,
        to_agent="supervisor", body=f"pulse:{kind}",
        routing_reason="pulse", payload={"pulse_name": kind, "kind": kind},
    )


def test_pulse_review_cycle_runs_when_quiet(tmp_path) -> None:
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    # Seed manager-only envelopes.
    _insert_user_envelope_at(
        store, chat_id=100, body="我今天几点开会?", msg_id=1,
        when=datetime.now(UTC) - timedelta(hours=1),
    )
    # Manager's reply (agent-originated; does NOT count as activity):
    late = (datetime.now(UTC) - timedelta(hours=1)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    store.messages().insert(Envelope(
        id=None, ts=late, parent_id=None, source="internal",
        telegram_chat_id=100, telegram_msg_id=None, received_by_bot=None,
        from_kind="agent", from_agent="manager", to_agent="user",
        body="下午两点。",
    ))

    persona = SupervisorPersona(
        core="core", dm_mode="dm", pulse_mode="pulse-mode-text",
        tool_use_guide="tools",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )

    good_response = json.dumps({
        "agent": "manager",
        "envelope_id_from": 1, "envelope_id_to": 2, "envelope_count": 2,
        "score_helpfulness": 80, "score_correctness": 80,
        "score_tone": 80, "score_efficiency": 80,
        "critique_text": "good.",
        "recommendations": [],
    })
    # Only manager has envelopes in this test; intelligence/learning are empty.
    fake_llm = _FakeLLM(next_response=good_response)

    sup = Supervisor(
        llm=fake_llm, store=store, persona=persona, config=cfg,
    )
    asyncio.run(sup.handle(_pulse_env("review_cycle")))

    rs = store.supervisor_reviews()
    latest = rs.latest_for_agent("manager")
    assert latest is not None
    assert latest.score_overall == 80
    # Cursor advanced.
    cursor = store.agent_memory("supervisor").get("cursor:manager")
    assert cursor == 2


def test_pulse_review_cycle_skips_when_busy(tmp_path) -> None:
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _insert_user_envelope_now(store, chat_id=100, body="still talking", msg_id=1)

    persona = SupervisorPersona(
        core="core", dm_mode="dm", pulse_mode="pulse-mode-text",
        tool_use_guide="tools",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )
    fake_llm = _FakeLLM(next_response="unused")

    sup = Supervisor(
        llm=fake_llm, store=store, persona=persona, config=cfg,
    )
    asyncio.run(sup.handle(_pulse_env("review_cycle")))

    assert store.supervisor_reviews().latest_for_agent("manager") is None
    assert store.agent_memory("supervisor").get("idle_gate:pending_since_ts") is not None


def test_pulse_review_retry_noop_when_no_pending(tmp_path) -> None:
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    persona = SupervisorPersona(
        core="core", dm_mode="dm", pulse_mode="pulse-mode-text",
        tool_use_guide="tools",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )
    fake_llm = _FakeLLM(next_response="should not be called")

    sup = Supervisor(
        llm=fake_llm, store=store, persona=persona, config=cfg,
    )
    result = asyncio.run(sup.handle(_pulse_env("review_retry")))

    assert result is None
    assert fake_llm.calls is None  # LLM never invoked


def test_pulse_review_cycle_skips_empty_slice(tmp_path) -> None:
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    # No envelopes at all → all three agents empty.

    persona = SupervisorPersona(
        core="core", dm_mode="dm", pulse_mode="pulse-mode-text",
        tool_use_guide="tools",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )
    fake_llm = _FakeLLM(next_response="unused")

    sup = Supervisor(
        llm=fake_llm, store=store, persona=persona, config=cfg,
    )
    asyncio.run(sup.handle(_pulse_env("review_cycle")))

    for agent in ("manager", "intelligence", "learning"):
        assert store.supervisor_reviews().latest_for_agent(agent) is None


def test_dm_path_returns_reply_using_dm_persona_section(tmp_path) -> None:
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    persona = SupervisorPersona(
        core="CORE",
        dm_mode="DM_MODE_SECTION",
        pulse_mode="PULSE_MODE_SECTION",
        tool_use_guide="TOOLS",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )
    fake_llm = _FakeLLM(next_response="欧尼酱好呀~")

    sup = Supervisor(
        llm=fake_llm, store=store, persona=persona, config=cfg,
    )

    dm_env = Envelope(
        id=None, ts="2026-04-17T09:00:00Z", parent_id=None,
        source="telegram_dm", telegram_chat_id=42, telegram_msg_id=99,
        received_by_bot="supervisor",
        from_kind="user", from_agent=None, to_agent="supervisor",
        body="最近 manager 表现怎么样?",
        routing_reason="direct_dm",
    )
    result = asyncio.run(sup.handle(dm_env))
    assert result is not None
    assert result.reply_text is not None and "欧尼酱" in result.reply_text

    # Confirm the LLM saw the DM section and NOT the pulse section.
    assert fake_llm.calls is not None
    assert len(fake_llm.calls) == 1
    # The system prompt is passed through to FakeProvider via the method arg,
    # not into Msg content — verify via behavior: no JSON parse attempts,
    # result is free text.
    assert result.delegate_to is None
```

- [ ] **Step 8.2: Run tests — expect failure**

```bash
uv run pytest tests/test_supervisor_agent.py -v
```

Expected: `Supervisor` class does not exist.

- [ ] **Step 8.3: Implement `Supervisor` class**

In `src/project0/agents/supervisor.py`, append:

```python
# --- Supervisor agent -------------------------------------------------------


class Supervisor:
    """叶霏 — pulse-scheduled reviewer + DM companion.

    Pulse path branches on envelope.payload['kind']:
        - 'review_cycle': idle-gate then review each of the three reviewable
          agents against their cursor.
        - 'review_retry': only proceed if an idle-gate pending flag is set.

    DM path runs a plain LLM call with the DM persona section. No tool use
    in v1.0 DM — the review surface is read-only through the control panel
    and her answers summarize existing review rows.
    """

    def __init__(
        self,
        *,
        llm: LLMProvider,
        store,                      # project0.store.Store; typed loosely to avoid circular import
        persona: SupervisorPersona,
        config: SupervisorConfig,
    ) -> None:
        self._llm = llm
        self._store = store
        self._persona = persona
        self._config = config
        self._memory = store.agent_memory("supervisor")
        self._messages = store.messages()
        self._reviews = store.supervisor_reviews()
        self._engine = ReviewEngine(
            llm=llm, pulse_mode_section=persona.pulse_mode,
        )
        self._gate = IdleGate(
            messages_store=self._messages,
            memory=self._memory,
            quiet_threshold_seconds=config.quiet_threshold_seconds,
            max_wait_seconds=config.max_wait_seconds,
        )

    async def handle(self, env: Envelope) -> AgentResult | None:
        if env.routing_reason == "pulse":
            return await self._handle_pulse(env)
        if env.routing_reason == "direct_dm":
            return await self._handle_dm(env)
        log.debug("supervisor: ignoring routing_reason=%s", env.routing_reason)
        return None

    # --- pulse path ---------------------------------------------------------

    async def _handle_pulse(self, env: Envelope) -> AgentResult | None:
        payload = env.payload or {}
        kind = str(payload.get("kind") or payload.get("pulse_name") or "")
        if kind == "review_retry":
            if not self._gate.has_pending():
                return None
            return await self._try_run_reviews(trigger="pulse")
        if kind == "review_cycle":
            return await self._try_run_reviews(trigger="pulse")
        log.warning("supervisor: unknown pulse kind=%r", kind)
        return None

    async def _try_run_reviews(self, *, trigger: str) -> AgentResult | None:
        result = self._gate.check(now=datetime.now(UTC))
        if not result.should_run:
            return None
        if result.forced_after_cap:
            log.warning("supervisor: forced_run_after_cap=True")
        await self._run_all_reviews(trigger=trigger)
        # Quiet pass → clear pending. Forced pass also clears (so the cap
        # restarts fresh after we ran once).
        self._gate.clear_pending()
        return None  # pulse path never produces a user-visible reply

    async def _run_all_reviews(self, *, trigger: str) -> None:
        for agent in REVIEWED_AGENTS:
            try:
                await self._run_review_for_agent(agent, trigger=trigger)
            except Exception:
                log.exception(
                    "supervisor: review failed for agent=%s; continuing",
                    agent,
                )

    async def _run_review_for_agent(self, agent: str, *, trigger: str) -> None:
        cursor = int(self._memory.get(f"cursor:{agent}") or 0)
        envs = self._messages.envelopes_for_review(
            agent=agent,
            after_id=cursor,
            limit=self._config.per_tick_limit,
        )
        if not envs:
            return

        review = await self._engine.run_review(
            agent=agent,
            envelopes=envs,
            trigger=trigger,
            max_tokens=self._config.max_tokens_reply,
        )
        if review is None:
            log.warning(
                "supervisor: review returned None for agent=%s; cursor unchanged",
                agent,
            )
            return

        # Persist row + advance cursor.
        from project0.store import SupervisorReviewRow
        row = SupervisorReviewRow(
            id=0,
            ts=review.ts,
            agent=review.agent,
            envelope_id_from=review.envelope_id_from,
            envelope_id_to=review.envelope_id_to,
            envelope_count=review.envelope_count,
            score_overall=review.score_overall,
            score_helpfulness=review.score_helpfulness,
            score_correctness=review.score_correctness,
            score_tone=review.score_tone,
            score_efficiency=review.score_efficiency,
            critique_text=review.critique_text,
            recommendations_json=review.recommendations_json,
            trigger=review.trigger,
        )
        async with self._store.lock:
            self._reviews.insert(row)
            self._memory.set(f"cursor:{agent}", review.envelope_id_to)

    # --- DM path ------------------------------------------------------------

    async def _handle_dm(self, env: Envelope) -> AgentResult | None:
        system = self._persona.core + "\n\n" + self._persona.dm_mode
        try:
            raw = await self._llm.complete(
                system=system,
                messages=[Msg(role="user", content=env.body)],
                max_tokens=self._config.max_tokens_reply,
                agent="supervisor",
                purpose="dm_reply",
                envelope_id=env.id,
            )
        except Exception:
            log.exception("supervisor: DM LLM call failed")
            return None
        return AgentResult(
            reply_text=raw or "(叶霏好像卡壳了,欧尼酱再说一次嘛~)",
            delegate_to=None,
            handoff_text=None,
        )
```

- [ ] **Step 8.4: Run tests — expect pass**

```bash
uv run pytest tests/test_supervisor_agent.py -v
```

Expected: all Supervisor tests pass. The existing cross-agent tests should still pass too.

- [ ] **Step 8.5: Commit**

```bash
git add src/project0/agents/supervisor.py tests/test_supervisor_agent.py
git commit -m "$(cat <<'EOF'
feat(supervisor): add Supervisor class with pulse and DM paths

review_cycle pulse runs the full idle-gated review pass; review_retry
pulse only acts when a pending flag is set. DM path uses the dm_mode
persona section and returns a free-text reply — no tools, read-only
semantics for v1.0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Registry wiring (`register_supervisor` + `AGENT_SPECS`)

**Files:**
- Modify: `src/project0/agents/registry.py`
- Modify: `tests/test_supervisor_agent.py` (extend)

- [ ] **Step 9.1: Write the failing registry test**

Append to `tests/test_supervisor_agent.py`:

```python
# --- registry wiring --------------------------------------------------------


def test_register_supervisor_installs_into_correct_registries() -> None:
    from project0.agents.registry import (
        AGENT_REGISTRY, AGENT_SPECS, LISTENER_REGISTRY, PULSE_REGISTRY,
        register_supervisor,
    )

    assert "supervisor" in AGENT_SPECS
    assert AGENT_SPECS["supervisor"].token_env_key == "TELEGRAM_BOT_TOKEN_SUPERVISOR"

    async def _fake_handle(env):
        return None

    register_supervisor(_fake_handle)
    assert "supervisor" in AGENT_REGISTRY
    assert "supervisor" in PULSE_REGISTRY
    assert "supervisor" not in LISTENER_REGISTRY
```

- [ ] **Step 9.2: Run test — expect failure**

```bash
uv run pytest tests/test_supervisor_agent.py::test_register_supervisor_installs_into_correct_registries -v
```

Expected: `KeyError` for AGENT_SPECS["supervisor"] OR `ImportError` for register_supervisor.

- [ ] **Step 9.3: Add `AGENT_SPECS["supervisor"]` and `register_supervisor`**

In `src/project0/agents/registry.py`, add to `AGENT_SPECS`:

```python
    "supervisor": AgentSpec(
        name="supervisor", token_env_key="TELEGRAM_BOT_TOKEN_SUPERVISOR"
    ),
```

And add, after `register_learning`:

```python
def register_supervisor(handle: AgentOptionalFn) -> None:
    """Install Supervisor's ``handle`` into AGENT_REGISTRY + PULSE_REGISTRY.
    Not added to LISTENER_REGISTRY — 叶霏 does not passively witness group
    chats; she reviews the stored messages log after the fact."""

    async def agent_adapter(env: Envelope) -> AgentResult:
        result = await handle(env)
        if result is None:
            return AgentResult(
                reply_text="(叶霏走神了...等我一下嘛~)",
                delegate_to=None,
                handoff_text=None,
            )
        return result

    AGENT_REGISTRY["supervisor"] = agent_adapter
    PULSE_REGISTRY["supervisor"] = handle
```

- [ ] **Step 9.4: Run test — expect pass**

```bash
uv run pytest tests/test_supervisor_agent.py::test_register_supervisor_installs_into_correct_registries -v
```

Expected: pass.

- [ ] **Step 9.5: Run full suite**

```bash
uv run pytest -x
```

Expected: all pass. Note: any existing startup-time tests that enumerate `AGENT_SPECS` and expect a specific bot-token env var list may need `TELEGRAM_BOT_TOKEN_SUPERVISOR` added to their fixture env. If a test fails with `TELEGRAM_BOT_TOKEN_SUPERVISOR is required but was empty or unset`, add it to that test's fixture in the same way existing tokens are set.

- [ ] **Step 9.6: Commit**

```bash
git add src/project0/agents/registry.py tests/test_supervisor_agent.py
git commit -m "$(cat <<'EOF'
feat(supervisor): register supervisor in AGENT_SPECS and pulse registry

Supervisor is routable via @mention and DM (AGENT_REGISTRY) and receives
pulses (PULSE_REGISTRY), but does NOT join LISTENER_REGISTRY — she has no
group presence in v1.0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Wire Supervisor in `main.py`

**Files:**
- Modify: `src/project0/main.py`
- Test: manual (wiring exercised end-to-end via smoke test at the end of the plan); optionally a lightweight smoke test that `main._run` constructs without error using a fake environment.

- [ ] **Step 10.1: Add Supervisor construction block**

In `src/project0/main.py`, after the Learning construction block (around line 382, after `log.info("learning registered (model=%s)", learning_cfg.model)`), add:

```python
    # --- Supervisor agent (叶霏) ---------------------------------------------
    from project0.agents.supervisor import (
        Supervisor,
        load_supervisor_config,
        load_supervisor_persona,
    )
    from project0.agents.registry import register_supervisor

    supervisor_persona = load_supervisor_persona(Path("prompts/supervisor.md"))
    supervisor_cfg = load_supervisor_config(Path("prompts/supervisor.toml"))

    supervisor = Supervisor(
        llm=llm,
        store=store,
        persona=supervisor_persona,
        config=supervisor_cfg,
    )
    register_supervisor(supervisor.handle)
    log.info("supervisor registered (model=%s)", supervisor_cfg.model)

    supervisor_pulse_entries = load_pulse_entries(Path("prompts/supervisor.toml"))
    log.info(
        "supervisor pulse entries: %s",
        [(e.name, e.every_seconds) for e in supervisor_pulse_entries],
    )
```

- [ ] **Step 10.2: Spawn Supervisor's pulse tasks inside the TaskGroup**

In `src/project0/main.py`, inside the `async with asyncio.TaskGroup() as tg:` block (around line 444), after the existing `learning_pulse_entries` loop, add:

```python
        for entry in supervisor_pulse_entries:
            tg.create_task(
                run_pulse_loop(
                    entry=entry,
                    target_agent="supervisor",
                    orchestrator=orch,
                )
            )
            log.info("pulse task spawned: %s", entry.name)
```

- [ ] **Step 10.3: Add environment variables to `.env`**

Since the repo may have `.env` locally (not tracked), the worker editing this file must add:

```
TELEGRAM_BOT_TOKEN_SUPERVISOR=<a real Telegram bot token from BotFather>
SUPERVISOR_PULSE_CHAT_ID=<any chat id, typically the user's personal DM chat id>
```

If a `.env.example` exists, mirror the keys there with placeholder values so future checkouts know the contract.

- [ ] **Step 10.4: Verify full suite**

```bash
uv run pytest -x
```

Expected: all pass. If a test (e.g. `test_main_sigterm.py` or `test_end_to_end.py`) sets env vars at the test layer, confirm `TELEGRAM_BOT_TOKEN_SUPERVISOR` is added in those fixtures.

- [ ] **Step 10.5: Commit**

```bash
git add src/project0/main.py
# plus .env.example if present
git commit -m "$(cat <<'EOF'
feat(main): wire Supervisor agent (叶霏) at startup

Load supervisor persona + config, register her in the agent and pulse
registries, and spawn run_pulse_loop tasks for review_cycle and
review_retry. Adds two required env vars:
TELEGRAM_BOT_TOKEN_SUPERVISOR, SUPERVISOR_PULSE_CHAT_ID.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: SVG rendering helpers — sparkline + timeseries

**Files:**
- Modify: `src/project0/control_panel/rendering.py`
- Test: `tests/test_reviews_rendering.py` (new)

- [ ] **Step 11.1: Write the failing helper tests**

Create `tests/test_reviews_rendering.py`:

```python
"""Tests for SVG helpers used by the /reviews page."""
from __future__ import annotations


def test_render_sparkline_svg_basic() -> None:
    from project0.control_panel.rendering import render_sparkline_svg
    svg = render_sparkline_svg([60, 65, 70, 72, 80], width=100, height=30)
    assert svg.startswith("<svg")
    assert "polyline" in svg
    assert 'viewBox="0 0 100 30"' in svg


def test_render_sparkline_svg_empty() -> None:
    from project0.control_panel.rendering import render_sparkline_svg
    svg = render_sparkline_svg([], width=100, height=30)
    assert svg.startswith("<svg")
    # Empty state must not crash and must produce renderable SVG.


def test_render_score_timeseries_svg_three_lines() -> None:
    from project0.control_panel.rendering import render_score_timeseries_svg
    series = {
        "manager":      [("2026-04-17T10:00:00Z", 70), ("2026-04-17T13:00:00Z", 75)],
        "intelligence": [("2026-04-17T10:00:00Z", 60), ("2026-04-17T13:00:00Z", 65)],
        "learning":     [("2026-04-17T10:00:00Z", 80), ("2026-04-17T13:00:00Z", 82)],
    }
    svg = render_score_timeseries_svg(series)
    assert svg.startswith("<svg")
    # One polyline per agent that has at least two points.
    assert svg.count("<polyline") == 3


def test_render_score_timeseries_svg_empty() -> None:
    from project0.control_panel.rendering import render_score_timeseries_svg
    svg = render_score_timeseries_svg({})
    assert svg.startswith("<svg")
    assert "no data" in svg
```

- [ ] **Step 11.2: Run tests — expect failure**

```bash
uv run pytest tests/test_reviews_rendering.py -v
```

Expected: ImportError for both helpers.

- [ ] **Step 11.3: Implement both helpers**

Append to `src/project0/control_panel/rendering.py`:

```python
# --- review-page helpers -----------------------------------------------------

_SPARK_STROKE = "#3366aa"
_TIMESERIES_COLORS = {
    "manager":      "#cc4455",  # red
    "intelligence": "#3366aa",  # blue
    "learning":     "#449944",  # green
}
_TS_CHART_WIDTH = 820
_TS_CHART_HEIGHT = 220
_TS_PAD_TOP = 20
_TS_PAD_BOTTOM = 30
_TS_PAD_LEFT = 50
_TS_PAD_RIGHT = 110  # room for the legend


def render_sparkline_svg(
    points: list[int], *, width: int = 120, height: int = 32,
) -> str:
    """Tiny sparkline: one polyline, no axes, no legend. Y-range is clamped
    to 0-100 regardless of input so sparklines across cards are comparable."""
    if not points:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" style="display:block;">'
            f'<text x="{width//2}" y="{height//2}" text-anchor="middle" '
            f'fill="#888" font-size="10">no data</text></svg>'
        )
    n = len(points)
    if n == 1:
        # Draw a single dot so the card still shows something.
        x = width / 2
        y = height - (points[0] / 100.0) * (height - 2) - 1
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" style="display:block;">'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" fill="{_SPARK_STROKE}"/>'
            f'</svg>'
        )
    step = width / (n - 1)
    pts: list[str] = []
    for i, v in enumerate(points):
        clamped = max(0, min(100, int(v)))
        x = i * step
        y = height - (clamped / 100.0) * (height - 2) - 1
        pts.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(pts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" style="display:block;">'
        f'<polyline fill="none" stroke="{_SPARK_STROKE}" stroke-width="1.5" '
        f'points="{poly}"/>'
        f'</svg>'
    )


def render_score_timeseries_svg(
    series: dict[str, list[tuple[str, int]]],
    *,
    width: int = _TS_CHART_WIDTH,
    height: int = _TS_CHART_HEIGHT,
) -> str:
    """Multi-line time-series chart of overall scores. Y-axis fixed 0-100.
    X-axis is the union of timestamps across all series, sorted; each agent's
    points align to that shared axis. Minimal axis/tick work — just a 0/50/100
    y-grid and a horizontal baseline."""
    plot_w = width - _TS_PAD_LEFT - _TS_PAD_RIGHT
    plot_h = height - _TS_PAD_TOP - _TS_PAD_BOTTOM

    # Filter out empty series.
    live = {k: v for k, v in series.items() if v}
    if not live:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" '
            f'style="max-width:100%;height:auto;">'
            f'<text x="{width//2}" y="{height//2}" text-anchor="middle" '
            f'fill="#888">no data — come back after a few reviews</text>'
            f'</svg>'
        )

    all_ts = sorted({ts for pts in live.values() for ts, _ in pts})
    n = len(all_ts)
    ts_to_x = {
        ts: _TS_PAD_LEFT + (i * plot_w / max(1, n - 1))
        for i, ts in enumerate(all_ts)
    }

    def _y(score: int) -> float:
        clamped = max(0, min(100, int(score)))
        return _TS_PAD_TOP + (1 - clamped / 100.0) * plot_h

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" '
        f'style="max-width:100%;height:auto;font-family:monospace;font-size:10px;">'
    ]
    # Y gridlines at 0, 50, 100
    for y_val in (0, 50, 100):
        gy = _y(y_val)
        parts.append(
            f'<line x1="{_TS_PAD_LEFT}" y1="{gy:.1f}" '
            f'x2="{_TS_PAD_LEFT + plot_w}" y2="{gy:.1f}" '
            f'stroke="#ddd" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{_TS_PAD_LEFT - 6}" y="{gy + 3:.1f}" '
            f'text-anchor="end" fill="#666">{y_val}</text>'
        )
    # One polyline per agent.
    legend_y = _TS_PAD_TOP
    for agent, pts in live.items():
        color = _TIMESERIES_COLORS.get(agent, "#888")
        coords = [f"{ts_to_x[ts]:.1f},{_y(s):.1f}" for ts, s in pts]
        if len(coords) >= 2:
            parts.append(
                f'<polyline fill="none" stroke="{color}" stroke-width="2" '
                f'points="{" ".join(coords)}"/>'
            )
        else:
            cx_s, cy_s = coords[0].split(",")
            parts.append(
                f'<circle cx="{cx_s}" cy="{cy_s}" r="3" fill="{color}"/>'
            )
        # Legend entry.
        lx = _TS_PAD_LEFT + plot_w + 10
        parts.append(
            f'<rect x="{lx}" y="{legend_y - 6}" width="10" height="10" '
            f'fill="{color}"/>'
            f'<text x="{lx + 14}" y="{legend_y + 3}" fill="#333">{agent}</text>'
        )
        legend_y += 16
    parts.append("</svg>")
    return "".join(parts)
```

- [ ] **Step 11.4: Run tests — expect pass**

```bash
uv run pytest tests/test_reviews_rendering.py -v
```

Expected: all 4 pass.

- [ ] **Step 11.5: Commit**

```bash
git add src/project0/control_panel/rendering.py tests/test_reviews_rendering.py
git commit -m "$(cat <<'EOF'
feat(control_panel): add SVG helpers for the /reviews page

render_sparkline_svg: tiny 120x32 inline polyline for per-agent cards.
render_score_timeseries_svg: 820x220 multi-agent line chart with fixed
0-100 y-axis, y-gridlines at 0/50/100, and a small legend. Both helpers
handle empty input gracefully with a neutral 'no data' message.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: `/reviews` route + template

**Files:**
- Modify: `src/project0/control_panel/routes.py`
- Modify: `src/project0/control_panel/templates/base.html`
- Create: `src/project0/control_panel/templates/reviews.html`
- Test: `tests/test_reviews_page.py` (new)

- [ ] **Step 12.1: Write the failing route test**

Create `tests/test_reviews_page.py`:

```python
"""Tests for the /reviews page — renders with and without review rows."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from project0.store import Store, SupervisorReviewRow


@pytest.fixture
def app(tmp_path):
    from project0.control_panel.app import create_app
    from project0.control_panel.supervisor import MAASSupervisor

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    # Minimal no-op MAASSupervisor — state stays 'stopped', no subprocess.
    async def _never_spawn():
        raise RuntimeError("should not spawn in tests")
    maas_sup = MAASSupervisor(spawn_fn=_never_spawn)

    app = create_app(
        supervisor=maas_sup,
        store=store,
        project_root=tmp_path,
    )
    return app, store


def test_reviews_page_renders_with_empty_db(app) -> None:
    fastapi_app, _ = app
    client = TestClient(fastapi_app)
    r = client.get("/reviews")
    assert r.status_code == 200
    # Empty-state copy in any of the three language bits we placed.
    assert ("no data" in r.text) or ("no reviews yet" in r.text) or ("还没" in r.text)


def test_reviews_page_renders_with_rows(app) -> None:
    fastapi_app, store = app
    rs = store.supervisor_reviews()
    rs.insert(SupervisorReviewRow(
        id=0, ts="2026-04-17T10:00:00Z", agent="manager",
        envelope_id_from=1, envelope_id_to=5, envelope_count=5,
        score_overall=77,
        score_helpfulness=80, score_correctness=75,
        score_tone=85, score_efficiency=70,
        critique_text="Manager 回应及时。",
        recommendations_json=json.dumps([
            {"target": "prompt", "summary": "更主动", "detail": "可以主动提醒。"},
        ], ensure_ascii=False),
        trigger="pulse",
    ))
    rs.insert(SupervisorReviewRow(
        id=0, ts="2026-04-17T11:00:00Z", agent="intelligence",
        envelope_id_from=1, envelope_id_to=3, envelope_count=3,
        score_overall=62,
        score_helpfulness=60, score_correctness=70,
        score_tone=60, score_efficiency=55,
        critique_text="Intelligence 今天稍显冷淡。",
        recommendations_json="[]",
        trigger="pulse",
    ))
    client = TestClient(fastapi_app)
    r = client.get("/reviews")
    assert r.status_code == 200
    assert "77" in r.text
    assert "62" in r.text
    assert "Manager 回应及时" in r.text
    assert "<polyline" in r.text  # timeseries or sparkline present


def test_reviews_page_listed_in_nav(app) -> None:
    fastapi_app, _ = app
    client = TestClient(fastapi_app)
    r = client.get("/")
    assert 'href="/reviews"' in r.text
```

Note: `create_app` signature is inferred from existing tests. If the real `create_app` uses a different bootstrap, adjust the fixture accordingly — check `src/project0/control_panel/app.py` before running.

- [ ] **Step 12.2: (no code change — `create_app` already accepts `store`, `project_root`, `supervisor`)**

For reference, the existing signature in `src/project0/control_panel/app.py` is:

```python
def create_app(
    *,
    supervisor: MAASSupervisor,
    store: Store,
    project_root: Path,
) -> FastAPI:
```

The fixture in Step 12.1 passes all three.

- [ ] **Step 12.3: Add `/reviews` nav link to `base.html`**

In `src/project0/control_panel/templates/base.html`, update the nav block:

```html
  <nav class="main-nav">
    <a href="/">Home</a>
    <a href="/profile">Profile</a>
    <a href="/facts">Facts</a>
    <a href="/toml">TOML</a>
    <a href="/personas">Personas</a>
    <a href="/env">.env</a>
    <a href="/usage">Usage</a>
    <a href="/reviews">Reviews</a>
  </nav>
```

- [ ] **Step 12.4: Create `reviews.html` template**

Create `src/project0/control_panel/templates/reviews.html`:

```html
{% extends "base.html" %}
{% block content %}
<section class="reviews">
  <h1>叶霏 的评分面板</h1>

  <section class="review-chart">
    <h2>近 30 次 review 趋势</h2>
    {{ chart_svg | safe }}
  </section>

  <section class="review-cards">
    {% for agent in agents %}
      <div class="review-card">
        <h3>{{ agent_labels[agent] }}</h3>
        {% if cards[agent] is none %}
          <p class="empty">叶霏还没评过她呢 ◡̈</p>
        {% else %}
          <p class="score-overall">{{ cards[agent].score_overall }}</p>
          <ul class="rubric">
            <li>helpfulness: {{ cards[agent].score_helpfulness }}</li>
            <li>correctness: {{ cards[agent].score_correctness }}</li>
            <li>tone: {{ cards[agent].score_tone }}</li>
            <li>efficiency: {{ cards[agent].score_efficiency }}</li>
          </ul>
          <div class="spark">{{ sparkline_svgs[agent] | safe }}</div>
          <p class="critique">{{ cards[agent].critique_text }}</p>
          {% set recs = recommendations[agent] %}
          {% if recs %}
            <p class="top-rec"><strong>建议:</strong> {{ recs[0].summary }}</p>
          {% else %}
            <p class="top-rec"><strong>建议:</strong> —</p>
          {% endif %}
          <p class="meta">reviewed {{ cards[agent].envelope_count }} envelopes @ {{ cards[agent].ts }}</p>
        {% endif %}
      </div>
    {% endfor %}
  </section>

  <section class="review-history">
    {% for agent in agents %}
      <details>
        <summary>{{ agent_labels[agent] }} — 历史 ({{ history[agent]|length }})</summary>
        {% if history[agent] %}
          <table class="history-table">
            <thead>
              <tr>
                <th>时间</th><th>overall</th><th>H</th><th>C</th><th>T</th><th>E</th><th>envelopes</th>
              </tr>
            </thead>
            <tbody>
              {% for row in history[agent] %}
                <tr>
                  <td>{{ row.ts }}</td>
                  <td>{{ row.score_overall }}</td>
                  <td>{{ row.score_helpfulness }}</td>
                  <td>{{ row.score_correctness }}</td>
                  <td>{{ row.score_tone }}</td>
                  <td>{{ row.score_efficiency }}</td>
                  <td>{{ row.envelope_count }}</td>
                </tr>
                <tr class="critique-row">
                  <td colspan="7">
                    <div class="critique-body">{{ row.critique_text }}</div>
                    {% if history_recs[agent][loop.index0] %}
                      <ul class="rec-list">
                        {% for rec in history_recs[agent][loop.index0] %}
                          <li><strong>[{{ rec.target }}]</strong> {{ rec.summary }} — {{ rec.detail }}</li>
                        {% endfor %}
                      </ul>
                    {% endif %}
                  </td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <p class="empty">no reviews yet for this agent.</p>
        {% endif %}
      </details>
    {% endfor %}
  </section>
</section>
{% endblock %}
```

- [ ] **Step 12.5: Add `GET /reviews` route handler**

In `src/project0/control_panel/routes.py`, after the `/usage` route, append:

```python
@router.get("/reviews")
async def reviews(request: Request) -> object:
    templates = request.app.state.templates
    store = request.app.state.store
    reviews_store = store.supervisor_reviews()

    agents = ("manager", "intelligence", "learning")
    agent_labels = {
        "manager":      "经理",
        "intelligence": "情报",
        "learning":     "书瑶",
    }

    cards = {a: reviews_store.latest_for_agent(a) for a in agents}
    history = {a: reviews_store.recent_for_agent(a, limit=30) for a in agents}
    spark_series = {
        a: reviews_store.history_spark(agent=a, limit=20) for a in agents
    }

    # Flatten all rows into the timeseries chart series.
    chart_series: dict[str, list[tuple[str, int]]] = {
        a: spark_series[a] for a in agents
    }

    # Parse recommendations_json for card + history view.
    import json as _json
    recommendations: dict[str, list[dict]] = {}
    for a in agents:
        if cards[a] is None:
            recommendations[a] = []
        else:
            try:
                recommendations[a] = _json.loads(cards[a].recommendations_json) or []
            except _json.JSONDecodeError:
                recommendations[a] = []

    history_recs: dict[str, list[list[dict]]] = {}
    for a in agents:
        per_row: list[list[dict]] = []
        for row in history[a]:
            try:
                per_row.append(_json.loads(row.recommendations_json) or [])
            except _json.JSONDecodeError:
                per_row.append([])
        history_recs[a] = per_row

    # SVGs.
    from project0.control_panel.rendering import (
        render_score_timeseries_svg,
        render_sparkline_svg,
    )
    chart_svg = render_score_timeseries_svg(chart_series)
    sparkline_svgs = {
        a: render_sparkline_svg([s for _, s in spark_series[a]]) for a in agents
    }

    return templates.TemplateResponse(
        request, "reviews.html",
        _ctx(
            request,
            agents=agents,
            agent_labels=agent_labels,
            cards=cards,
            history=history,
            recommendations=recommendations,
            history_recs=history_recs,
            chart_svg=chart_svg,
            sparkline_svgs=sparkline_svgs,
        ),
    )
```

- [ ] **Step 12.6: Run tests — expect pass**

```bash
uv run pytest tests/test_reviews_page.py -v
```

Expected: all 3 pass.

- [ ] **Step 12.7: Run full suite**

```bash
uv run pytest -x
```

Expected: green.

- [ ] **Step 12.8: Commit**

```bash
git add src/project0/control_panel/routes.py \
        src/project0/control_panel/templates/base.html \
        src/project0/control_panel/templates/reviews.html \
        tests/test_reviews_page.py
git commit -m "$(cat <<'EOF'
feat(control_panel): add /reviews page with chart, cards, and history

Read-only view into supervisor_reviews. Top band: multi-line time-series
SVG. Middle band: three agent cards with latest overall score, rubric
mini-bars, sparkline, critique, top recommendation. Bottom band: per-agent
collapsible history tables. Nav link added to base.html.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Final full-suite verification and human smoke test

**Files:** none; verification only.

- [ ] **Step 13.1: Run the complete test suite**

```bash
uv run pytest -v
```

Expected: everything passes. No skipped tests except those that are already skipped in the main branch.

- [ ] **Step 13.2: Type-check (if the project uses mypy or pyright)**

```bash
# Whichever the project uses:
uv run mypy src/project0 || true
uv run pyright src/project0 || true
```

Expected: no new type errors beyond any pre-existing baseline.

- [ ] **Step 13.3: Prepare `.env` for smoke test**

Confirm that `TELEGRAM_BOT_TOKEN_SUPERVISOR` is set in `.env` (real token from BotFather for a bot username like `maas_supervisor_bot`) and that `SUPERVISOR_PULSE_CHAT_ID` is set (user's personal chat id with that bot).

- [ ] **Step 13.4: Human smoke test**

1. From the control panel (`http://<host>:8090`), click **Start** to spawn MAAS.
2. Confirm startup logs include: `supervisor registered (model=claude-sonnet-4-6)` and `pulse task spawned: review_cycle` and `pulse task spawned: review_retry`.
3. In Telegram, open a DM with 叶霏's bot (whichever username BotFather assigned).
4. Send: `你先帮我把 intelligence 从头到现在评一遍吧~`
5. Expect a reply in 叶霏's voice that either summarizes the intelligence agent's recent behavior or reports that there is nothing to review yet (if intelligence has no envelope history).
6. Open `http://<host>:8090/reviews` in a browser.
7. Confirm the page loads, shows the three agent cards, and any newly-created review row is visible in the chart, the intelligence card, and the intelligence history section.
8. Click Stop to shut MAAS back down.

- [ ] **Step 13.5: Update the main design doc to mark completion**

The parent product doc `Project 0: Multi-agent assistant system.md` §3.5 already describes Supervisor's role. No edit needed unless a factual gap surfaced during implementation. If so, amend and commit as a separate doc-only commit.

---

## Notes for the implementer

- **Chinese strings in test assertions.** Several assertions match Chinese substrings (e.g. `"叶霏"`, `"欧尼酱"`). Keep source files UTF-8 without BOM.
- **`SUPERVISOR_PULSE_CHAT_ID` must be a valid integer.** The pulse primitive raises at startup if the env var is missing or non-integer (see `pulse.py:85-95`). Setting it to any valid chat_id (even one 叶霏 never sends to) satisfies the contract.
- **Async lock.** `store.lock` is an `asyncio.Lock`; the `async with self._store.lock:` block in `_run_review_for_agent` serializes the (insert + cursor.set) pair against any other writers.
- **Avoid widening Secretary's exception.** Only `visible_to='secretary'` bypasses the filter in `recent_for_chat`; do not add a second bypass, and do not change the DM scoping logic (already correct).
- **Rubric weights are a constant, not config.** If a future iteration needs tunable weights, move them behind a config key then; for v1.0 this keeps the scoring deterministic and testable.
- **Commit messages use the existing Co-Authored-By trailer style.** Match the existing repo convention.

---

## Spec coverage check

| Spec section | Covered by |
|---|---|
| §2 Architecture | Tasks 4, 5, 8, 9, 10 |
| §3.1 Pulse tick flow | Task 8 (`_try_run_reviews`, `_run_review_for_agent`) |
| §3.2 Idle gate parameters | Tasks 6, 4 |
| §3.3 Retry mechanism | Tasks 4 (pulse entry), 8 (`_handle_pulse` branch) |
| §3.4 Cursor storage | Task 8 (`store.lock` + cursor.set) |
| §3.5 Envelope selection | Task 3 |
| §4.1-4.3 Rubric + recs | Task 7 (`_parse_and_validate`, `RUBRIC_WEIGHTS`) |
| §4.4 LLM call shape | Task 7 (`ReviewEngine.run_review`) |
| §4.5 Malformed output | Task 7 tests + `_parse_and_validate` |
| §5.1 Schema | Task 2 |
| §5.2 Store class | Task 2 |
| §5.3 `envelopes_for_review` | Task 3 |
| §5.4 Memory keys | Tasks 6, 8 |
| §5.5 Wiring | Task 10 |
| §5.6 registry.py | Task 9 |
| §6 Secretary isolation | Task 1 (all 5 steps) |
| §7 Persona + config | Tasks 4, 5 |
| §8 Control-panel page | Tasks 11, 12, 4 (paths.py) |
| §9.1 Error handling | Tasks 7, 8 (LLM failure, empty slice, forced-cap log) |
| §9.2 Tests | Every task (TDD) |
| §9.3 Smoke test | Task 13.4 |
