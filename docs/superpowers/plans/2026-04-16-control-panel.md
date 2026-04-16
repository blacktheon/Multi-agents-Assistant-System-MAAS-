# WebUI Control Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone web control panel that supervises MAAS as a child process and provides textarea-based editing for `user_profile.yaml`, `prompts/*.{toml,md}`, `.env`, plus full CRUD on `user_facts`, plus a token-usage page over `llm_usage`.

**Architecture:** Two processes, one disk. The control panel is a separate FastAPI+Jinja2 app (sibling to `intelligence_web`) running on port 8090. It holds a `MAASSupervisor` that spawns `uv run python -m project0.main` as a child process. Edits to files require clicking Restart; edits to `user_facts` are live via SQLite WAL. No authentication — Tailscale is the gate.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, `asyncio.subprocess`, sqlite3 WAL mode. No JavaScript framework. No CDN. Inline SVG for the chart.

**Spec:** `docs/superpowers/specs/2026-04-16-control-panel-design.md`

---

## File structure

**New package:** `src/project0/control_panel/`

```
src/project0/control_panel/
├── __init__.py              # empty
├── __main__.py              # entry point: uvicorn.run(...)
├── app.py                   # create_app(supervisor, store_path, project_root) factory
├── supervisor.py            # MAASSupervisor state machine + SpawnFn
├── paths.py                 # allowlist + resolution for toml/persona file names
├── writes.py                # atomic_write_text helper
├── rendering.py             # Jinja2 setup + SVG bar chart macro helper
├── routes.py                # all HTTP routes
├── templates/
│   ├── base.html            # layout with status header + nav
│   ├── home.html
│   ├── profile.html
│   ├── facts.html
│   ├── toml_list.html
│   ├── toml_edit.html
│   ├── personas_list.html
│   ├── personas_edit.html
│   ├── env.html
│   └── usage.html
└── static/
    └── style.css            # minimal
```

**New tests:** `tests/control_panel/`

```
tests/control_panel/
├── __init__.py
├── conftest.py              # fixtures: tmp project root, seeded store, FakeSpawnFn
├── test_supervisor.py
├── test_atomic_write.py
├── test_paths.py
├── test_profile_routes.py
├── test_facts_routes.py
├── test_toml_routes.py
├── test_personas_routes.py
├── test_env_route.py
├── test_usage_routes.py
├── test_supervisor_routes.py
└── test_home.py
```

**Modifications:**

- `src/project0/store.py` — WAL pragmas; extend `UserFactsWriter` author allowlist + `reactivate`/`edit`/`delete`; add `LLMUsageStore.daily_rollup`/`agent_rollup`/`recent`.
- `src/project0/main.py` — SIGTERM handler so clean shutdown runs from the supervisor's SIGTERM.
- `tests/test_store.py` — extend trust-boundary suite.
- `README.md` — new section on the panel.
- `pyproject.toml` — no changes required (FastAPI, Jinja2, uvicorn already present).

---

## Task 1: Enable SQLite WAL mode in `store.py`

**Files:**
- Modify: `src/project0/store.py` (Store.__init__)
- Test: `tests/test_store_wal.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_store_wal.py`:

```python
"""WAL journaling mode is required so the control panel (separate process)
can read llm_usage and write user_facts concurrently with a running MAAS
process. See docs/superpowers/specs/2026-04-16-control-panel-design.md §7.6."""

from pathlib import Path

from project0.store import Store


def test_journal_mode_is_wal(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_synchronous_is_normal(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    # PRAGMA synchronous returns 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
    val = store.conn.execute("PRAGMA synchronous").fetchone()[0]
    assert int(val) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store_wal.py -v`
Expected: FAIL — current `Store.__init__` does not set `journal_mode=WAL`.

- [ ] **Step 3: Add WAL pragmas in `Store.__init__`**

In `src/project0/store.py`, modify `Store.__init__` (around line 110-118). Current body:

```python
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        # isolation_level=None => autocommit; we use explicit transactions when needed.
        self._conn = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = asyncio.Lock()
```

Change to:

```python
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        # isolation_level=None => autocommit; we use explicit transactions when needed.
        self._conn = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # WAL mode enables concurrent readers + one writer across processes,
        # required so the control panel (separate process) can read llm_usage
        # and write user_facts concurrently with a running MAAS process.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._lock = asyncio.Lock()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store_wal.py -v`
Expected: PASS both tests.

- [ ] **Step 5: Run the full existing store test suite**

Run: `uv run pytest tests/test_store.py -v`
Expected: all existing tests still pass (WAL is transparent to existing code).

- [ ] **Step 6: Commit**

```bash
git add src/project0/store.py tests/test_store_wal.py
git commit -m "$(cat <<'EOF'
feat(store): enable WAL + synchronous=NORMAL for multi-process access

Control panel (separate process) must read llm_usage and write user_facts
concurrently with the MAAS process. WAL mode is SQLite's supported answer.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Extend `UserFactsWriter` author allowlist + new methods

**Files:**
- Modify: `src/project0/store.py` (UserFactsWriter class)
- Test: `tests/test_store.py` (amended section)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py` a new test class:

```python
class TestUserFactsWriterExtended:
    """Layer D writer extended for the control panel sub-project.

    See docs/superpowers/specs/2026-04-16-control-panel-design.md §5.
    Authorized authors are {'secretary', 'human'}. 'human' is the only
    author permitted to edit or hard-delete existing facts.
    """

    def _store(self, tmp_path: Path) -> Store:
        s = Store(tmp_path / "s.db")
        s.init_schema()
        return s

    def test_human_writer_can_be_constructed(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        UserFactsWriter("human", s.conn)  # must not raise

    def test_secretary_writer_still_works(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        UserFactsWriter("secretary", s.conn)

    def test_unknown_agent_rejected(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        with pytest.raises(PermissionError):
            UserFactsWriter("manager", s.conn)
        with pytest.raises(PermissionError):
            UserFactsWriter("supervisor", s.conn)

    def test_human_add_sets_author_agent_human(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        w = UserFactsWriter("human", s.conn)
        fact_id = w.add("用户喜欢寿司", topic="food")
        row = s.conn.execute(
            "SELECT author_agent, fact_text, topic, is_active FROM user_facts WHERE id=?",
            (fact_id,),
        ).fetchone()
        assert row["author_agent"] == "human"
        assert row["fact_text"] == "用户喜欢寿司"
        assert row["topic"] == "food"
        assert row["is_active"] == 1

    def test_secretary_add_still_sets_author_agent_secretary(
        self, tmp_path: Path
    ) -> None:
        s = self._store(tmp_path)
        w = UserFactsWriter("secretary", s.conn)
        fact_id = w.add("用户生日是三月十四日")
        row = s.conn.execute(
            "SELECT author_agent FROM user_facts WHERE id=?", (fact_id,)
        ).fetchone()
        assert row["author_agent"] == "secretary"

    def test_reactivate(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        w = UserFactsWriter("human", s.conn)
        fid = w.add("x")
        w.deactivate(fid)
        assert s.conn.execute(
            "SELECT is_active FROM user_facts WHERE id=?", (fid,)
        ).fetchone()[0] == 0
        w.reactivate(fid)
        assert s.conn.execute(
            "SELECT is_active FROM user_facts WHERE id=?", (fid,)
        ).fetchone()[0] == 1

    def test_human_edit_updates_text_and_topic(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        w = UserFactsWriter("human", s.conn)
        fid = w.add("old text", topic="old_topic")
        original_ts = s.conn.execute(
            "SELECT ts FROM user_facts WHERE id=?", (fid,)
        ).fetchone()[0]
        w.edit(fid, "new text", "new_topic")
        row = s.conn.execute(
            "SELECT fact_text, topic, ts, author_agent FROM user_facts WHERE id=?",
            (fid,),
        ).fetchone()
        assert row["fact_text"] == "new text"
        assert row["topic"] == "new_topic"
        # Editing does not rewrite the original ts or author_agent:
        assert row["ts"] == original_ts
        assert row["author_agent"] == "human"

    def test_secretary_edit_raises(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        w_h = UserFactsWriter("human", s.conn)
        fid = w_h.add("x")
        w_s = UserFactsWriter("secretary", s.conn)
        with pytest.raises(PermissionError):
            w_s.edit(fid, "y", None)

    def test_human_delete_removes_row(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        w = UserFactsWriter("human", s.conn)
        fid = w.add("x")
        w.delete(fid)
        row = s.conn.execute(
            "SELECT id FROM user_facts WHERE id=?", (fid,)
        ).fetchone()
        assert row is None

    def test_secretary_delete_raises(self, tmp_path: Path) -> None:
        s = self._store(tmp_path)
        w_h = UserFactsWriter("human", s.conn)
        fid = w_h.add("x")
        w_s = UserFactsWriter("secretary", s.conn)
        with pytest.raises(PermissionError):
            w_s.delete(fid)
```

Make sure `UserFactsWriter` is imported at the top of `tests/test_store.py` (it may already be).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py::TestUserFactsWriterExtended -v`
Expected: FAIL — `UserFactsWriter("human", ...)` raises `PermissionError`, and `edit`, `reactivate`, `delete` methods do not exist.

- [ ] **Step 3: Extend `UserFactsWriter` in `src/project0/store.py`**

Replace the current `UserFactsWriter` class (around lines 570-596) with:

```python
class UserFactsWriter:
    """Append-only writes to user_facts.

    Authorized authors:
        - 'secretary' — Layer D write path from the agentic tool loop.
        - 'human'     — Layer D write path from the control panel (sub-project 3).

    ``add``, ``deactivate``, ``reactivate`` are available to both.
    ``edit`` and ``delete`` are **human-only** — Secretary has no product
    affordance for rewriting or hard-deleting her own past facts; her
    correction path is ``deactivate`` + ``add``.

    ``author_agent`` is written server-side; callers cannot spoof.
    """

    _AUTHORIZED_AUTHORS: frozenset[str] = frozenset({"secretary", "human"})
    _HUMAN_ONLY_AUTHORS: frozenset[str] = frozenset({"human"})

    def __init__(self, agent_name: str, conn: sqlite3.Connection) -> None:
        if agent_name not in self._AUTHORIZED_AUTHORS:
            raise PermissionError(
                f"user_facts writer not allowed for agent={agent_name!r}; "
                f"authorized authors: {sorted(self._AUTHORIZED_AUTHORS)}"
            )
        self._agent = agent_name
        self._conn = conn

    def add(self, fact_text: str, topic: str | None = None) -> int:
        ts = _utc_now_iso()
        cur = self._conn.execute(
            "INSERT INTO user_facts (ts, author_agent, fact_text, topic, is_active) "
            "VALUES (?, ?, ?, ?, 1)",
            (ts, self._agent, fact_text, topic),
        )
        return int(cur.lastrowid or 0)

    def deactivate(self, fact_id: int) -> None:
        self._conn.execute(
            "UPDATE user_facts SET is_active=0 WHERE id=?",
            (fact_id,),
        )

    def reactivate(self, fact_id: int) -> None:
        self._conn.execute(
            "UPDATE user_facts SET is_active=1 WHERE id=?",
            (fact_id,),
        )

    def edit(
        self, fact_id: int, fact_text: str, topic: str | None
    ) -> None:
        if self._agent not in self._HUMAN_ONLY_AUTHORS:
            raise PermissionError(
                f"user_facts edit is human-only; agent={self._agent!r} not permitted"
            )
        self._conn.execute(
            "UPDATE user_facts SET fact_text=?, topic=? WHERE id=?",
            (fact_text, topic, fact_id),
        )

    def delete(self, fact_id: int) -> None:
        if self._agent not in self._HUMAN_ONLY_AUTHORS:
            raise PermissionError(
                f"user_facts hard delete is human-only; agent={self._agent!r} not permitted"
            )
        self._conn.execute(
            "DELETE FROM user_facts WHERE id=?",
            (fact_id,),
        )
```

Note: The existing `add` hardcoded `'secretary'` in the SQL — we now use `self._agent` so human writes get labeled correctly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_store.py::TestUserFactsWriterExtended -v`
Expected: PASS all 10 tests.

- [ ] **Step 5: Run the full store test suite**

Run: `uv run pytest tests/test_store.py -v`
Expected: all existing tests pass (the hardcoded `'secretary'` change is equivalent because Secretary's writer still labels rows `'secretary'`).

- [ ] **Step 6: Also run the broader trust-boundary test if present**

Run: `uv run pytest tests/ -k "user_fact" -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/project0/store.py tests/test_store.py
git commit -m "$(cat <<'EOF'
feat(store): extend UserFactsWriter with 'human' author + edit/delete

Control panel needs to write user_facts from the browser. Extends the
authorized-author allowlist to {secretary, human} and adds reactivate,
edit, and delete methods. edit and delete are human-only — Secretary's
correction path remains deactivate+add.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add `daily_rollup`, `agent_rollup`, `recent` to `LLMUsageStore`

**Files:**
- Modify: `src/project0/store.py` (LLMUsageStore class)
- Test: `tests/test_store.py` (amended)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
class TestLLMUsageRollups:
    """Read APIs used by the control panel /usage page.

    Spec: docs/superpowers/specs/2026-04-16-control-panel-design.md §6.
    """

    def _seed(self, tmp_path: Path) -> Store:
        s = Store(tmp_path / "s.db")
        s.init_schema()
        # Seed three rows across two days, two agents, two purposes.
        # ts is set by Store's _utc_now_iso at insert; we INSERT directly
        # with known ts values so the rollup windows are deterministic.
        rows = [
            ("2026-04-14T10:00:00Z", "secretary", "claude-sonnet-4-6", 100, 0, 0, 50, None, "listener"),
            ("2026-04-15T10:00:00Z", "secretary", "claude-sonnet-4-6", 200, 0, 80, 60, 1, "reply"),
            ("2026-04-15T11:00:00Z", "manager",   "claude-sonnet-4-6", 300, 0, 90, 70, 2, "tool_loop"),
        ]
        for r in rows:
            s.conn.execute(
                "INSERT INTO llm_usage "
                "(ts, agent, model, input_tokens, cache_creation_input_tokens, "
                " cache_read_input_tokens, output_tokens, envelope_id, purpose) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                r,
            )
        return s

    def test_daily_rollup_groups_by_day_desc(self, tmp_path: Path) -> None:
        s = self._seed(tmp_path)
        rows = s.llm_usage().daily_rollup(days=30)
        # Expect two days, newest first.
        assert len(rows) == 2
        assert rows[0]["day"] == "2026-04-15"
        assert rows[0]["calls"] == 2
        assert rows[0]["in_tok"] == 500       # 200 + 300
        assert rows[0]["cr_tok"] == 170       # 80 + 90
        assert rows[0]["out_tok"] == 130      # 60 + 70
        assert rows[1]["day"] == "2026-04-14"
        assert rows[1]["calls"] == 1

    def test_agent_rollup_groups_by_agent_and_purpose(self, tmp_path: Path) -> None:
        s = self._seed(tmp_path)
        rows = s.llm_usage().agent_rollup(days=365)  # wide window to include all
        # Expect 3 groups: secretary/listener, secretary/reply, manager/tool_loop.
        keys = {(r["agent"], r["purpose"]) for r in rows}
        assert keys == {
            ("secretary", "listener"),
            ("secretary", "reply"),
            ("manager", "tool_loop"),
        }
        # Ordered by in_total desc — manager/tool_loop has highest total input.
        assert rows[0]["agent"] == "manager"
        assert rows[0]["in_total"] == 390   # 300 + 0 + 90
        assert rows[0]["out_total"] == 70

    def test_recent_returns_newest_first_with_limit(self, tmp_path: Path) -> None:
        s = self._seed(tmp_path)
        rows = s.llm_usage().recent(limit=2)
        assert len(rows) == 2
        assert rows[0]["agent"] == "manager"        # id 3, newest
        assert rows[1]["agent"] == "secretary"      # id 2
        assert rows[0]["envelope_id"] == 2
        # All columns present:
        assert set(rows[0].keys()) >= {
            "id", "ts", "agent", "purpose", "model",
            "input_tokens", "cache_creation_input_tokens",
            "cache_read_input_tokens", "output_tokens", "envelope_id",
        }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py::TestLLMUsageRollups -v`
Expected: FAIL — `daily_rollup`, `agent_rollup`, `recent` do not exist on `LLMUsageStore`.

- [ ] **Step 3: Add the three methods to `LLMUsageStore`**

Append to `LLMUsageStore` in `src/project0/store.py`, after `summary_since`:

```python
    def daily_rollup(self, days: int) -> list[dict[str, Any]]:
        """Per-day rollup for the last ``days`` days, newest first.

        Used by the control panel /usage page for the SVG bar chart and
        the daily table. See spec §6.2.
        """
        rows = self._conn.execute(
            "SELECT substr(ts, 1, 10)                 AS day, "
            "       SUM(input_tokens)                 AS in_tok, "
            "       SUM(cache_creation_input_tokens)  AS cc_tok, "
            "       SUM(cache_read_input_tokens)      AS cr_tok, "
            "       SUM(output_tokens)                AS out_tok, "
            "       COUNT(*)                          AS calls "
            "FROM llm_usage "
            "WHERE ts >= date('now', ?) "
            "GROUP BY day "
            "ORDER BY day DESC",
            (f"-{int(days)} days",),
        ).fetchall()
        return [
            {
                "day": str(r["day"]),
                "in_tok": int(r["in_tok"] or 0),
                "cc_tok": int(r["cc_tok"] or 0),
                "cr_tok": int(r["cr_tok"] or 0),
                "out_tok": int(r["out_tok"] or 0),
                "calls": int(r["calls"] or 0),
            }
            for r in rows
        ]

    def agent_rollup(self, days: int) -> list[dict[str, Any]]:
        """Per-(agent, purpose) rollup for the last ``days`` days, ordered
        by total input tokens descending. Spec §6.3."""
        rows = self._conn.execute(
            "SELECT agent, purpose, "
            "       SUM(input_tokens + cache_creation_input_tokens + cache_read_input_tokens) AS in_total, "
            "       SUM(output_tokens) AS out_total, "
            "       COUNT(*)           AS calls "
            "FROM llm_usage "
            "WHERE ts >= datetime('now', ?) "
            "GROUP BY agent, purpose "
            "ORDER BY in_total DESC",
            (f"-{int(days)} days",),
        ).fetchall()
        return [
            {
                "agent": str(r["agent"]),
                "purpose": str(r["purpose"]),
                "in_total": int(r["in_total"] or 0),
                "out_total": int(r["out_total"] or 0),
                "calls": int(r["calls"] or 0),
            }
            for r in rows
        ]

    def recent(self, limit: int) -> list[dict[str, Any]]:
        """Last ``limit`` rows, newest first. Spec §6.4."""
        rows = self._conn.execute(
            "SELECT id, ts, agent, purpose, model, "
            "       input_tokens, cache_creation_input_tokens, "
            "       cache_read_input_tokens, output_tokens, envelope_id "
            "FROM llm_usage "
            "ORDER BY id DESC "
            "LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "ts": str(r["ts"]),
                "agent": str(r["agent"]),
                "purpose": str(r["purpose"]),
                "model": str(r["model"]),
                "input_tokens": int(r["input_tokens"] or 0),
                "cache_creation_input_tokens": int(r["cache_creation_input_tokens"] or 0),
                "cache_read_input_tokens": int(r["cache_read_input_tokens"] or 0),
                "output_tokens": int(r["output_tokens"] or 0),
                "envelope_id": (
                    int(r["envelope_id"]) if r["envelope_id"] is not None else None
                ),
            }
            for r in rows
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_store.py::TestLLMUsageRollups -v`
Expected: PASS 3 tests.

- [ ] **Step 5: Run mypy on the store module**

Run: `uv run mypy src/project0/store.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add src/project0/store.py tests/test_store.py
git commit -m "$(cat <<'EOF'
feat(store): add daily_rollup/agent_rollup/recent to LLMUsageStore

Read APIs consumed by the control panel /usage page. Three fixed windows
(last 30 days, last 7 days, last 50 calls) — no filtering or date picker
in v1 per spec §6.6.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add SIGTERM handler to `main.py`

**Files:**
- Modify: `src/project0/main.py`
- Test: `tests/test_main_sigterm.py` (new, small)

**Context:** `main.py` currently only catches `KeyboardInterrupt` (SIGINT). Default Python behavior for SIGTERM is to terminate immediately without running cleanup. The supervisor sends SIGTERM on Stop, so we need `main.py` to translate it into a clean asyncio-loop shutdown. The simplest reliable approach is to raise `KeyboardInterrupt` from a SIGTERM handler — this reuses the existing cleanup path.

- [ ] **Step 1: Write the failing test**

Create `tests/test_main_sigterm.py`:

```python
"""SIGTERM must trigger the same clean shutdown path as SIGINT/KeyboardInterrupt,
so the control panel's Stop button (SIGTERM) shuts MAAS down gracefully.

We can't easily assert full asyncio behavior in-process, so we verify that
`main._install_sigterm_handler` is importable and that invoking the installed
handler function raises KeyboardInterrupt.
"""

import signal

from project0 import main


def test_install_sigterm_handler_exists() -> None:
    assert hasattr(main, "_install_sigterm_handler")
    assert callable(main._install_sigterm_handler)


def test_sigterm_handler_raises_keyboard_interrupt(monkeypatch) -> None:
    captured: list = []

    def fake_signal(sig: int, handler) -> None:
        captured.append((sig, handler))

    monkeypatch.setattr(signal, "signal", fake_signal)
    main._install_sigterm_handler()

    assert len(captured) == 1
    assert captured[0][0] == signal.SIGTERM
    handler = captured[0][1]
    try:
        handler(signal.SIGTERM, None)
    except KeyboardInterrupt:
        return
    raise AssertionError("handler should raise KeyboardInterrupt")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_main_sigterm.py -v`
Expected: FAIL — `_install_sigterm_handler` does not exist.

- [ ] **Step 3: Add the handler to `main.py`**

In `src/project0/main.py`, add a helper near `_setup_logging` (after line 46):

```python
def _install_sigterm_handler() -> None:
    """Translate SIGTERM to KeyboardInterrupt so ``asyncio.run`` exits via
    the existing clean-shutdown path. Used when MAAS runs as a child of the
    control panel, which sends SIGTERM on Stop."""
    import signal

    def _handler(signum: int, frame: object) -> None:  # noqa: ARG001
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, _handler)
```

Then in `main()` (around line 433), install the handler before `asyncio.run`:

```python
def main() -> None:
    settings = load_settings()
    _setup_logging(settings.log_level)
    _install_sigterm_handler()
    try:
        asyncio.run(_run(settings))
    except KeyboardInterrupt:
        log.info("shutting down")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_main_sigterm.py -v`
Expected: PASS.

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/project0/main.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add src/project0/main.py tests/test_main_sigterm.py
git commit -m "$(cat <<'EOF'
feat(main): translate SIGTERM into clean KeyboardInterrupt shutdown

Control panel supervises MAAS as a child process and sends SIGTERM on
Stop. main.py's existing shutdown path is wired to KeyboardInterrupt;
this handler lets SIGTERM reuse it without adding a second exit path.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Create `control_panel` package skeleton + atomic write helper

**Files:**
- Create: `src/project0/control_panel/__init__.py`
- Create: `src/project0/control_panel/writes.py`
- Create: `tests/control_panel/__init__.py`
- Create: `tests/control_panel/test_atomic_write.py`

- [ ] **Step 1: Create empty package files**

```bash
mkdir -p src/project0/control_panel src/project0/control_panel/templates src/project0/control_panel/static
mkdir -p tests/control_panel
touch src/project0/control_panel/__init__.py
touch tests/control_panel/__init__.py
```

- [ ] **Step 2: Write the failing test for atomic_write_text**

Create `tests/control_panel/test_atomic_write.py`:

```python
"""Atomic file writes via tmp + rename. Required so a panel crash mid-save
cannot leave .env or user_profile.yaml in a half-written state."""

from pathlib import Path

from project0.control_panel.writes import atomic_write_text


def test_writes_new_file(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


def test_leaves_no_tmp_file_on_success(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    atomic_write_text(target, "hi")
    # No *.tmp siblings left over:
    leftover = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftover == []


def test_handles_chinese_content(tmp_path: Path) -> None:
    target = tmp_path / "profile.yaml"
    atomic_write_text(target, "address_as: 主人\n备注: 测试\n")
    assert target.read_text(encoding="utf-8") == "address_as: 主人\n备注: 测试\n"


def test_handles_missing_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "does_not_exist" / "x.txt"
    # atomic_write_text should refuse rather than silently create dirs.
    try:
        atomic_write_text(target, "x")
    except FileNotFoundError:
        return
    raise AssertionError("expected FileNotFoundError for missing parent dir")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/control_panel/test_atomic_write.py -v`
Expected: FAIL — `project0.control_panel.writes` does not exist.

- [ ] **Step 4: Implement `atomic_write_text`**

Create `src/project0/control_panel/writes.py`:

```python
"""Atomic text-file writes used by every file-edit route in the control
panel. A panel crash between truncate() and write() on a target file
like .env or user_profile.yaml would corrupt it; writing to a sibling
tmp file and os.replace-ing is the standard POSIX fix."""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically (UTF-8).

    The parent directory must already exist — this function does not
    create it. Raises FileNotFoundError if it does not. On POSIX, the
    os.replace call is atomic; a concurrent reader either sees the old
    file or the new file, never a half-written one.
    """
    parent = path.parent
    if not parent.exists():
        raise FileNotFoundError(f"parent directory does not exist: {parent}")
    tmp = parent / (path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/control_panel/test_atomic_write.py -v`
Expected: PASS all 5 tests.

- [ ] **Step 6: Commit**

```bash
git add src/project0/control_panel/ tests/control_panel/__init__.py tests/control_panel/test_atomic_write.py
git commit -m "$(cat <<'EOF'
feat(control_panel): package skeleton + atomic_write_text helper

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Implement `MAASSupervisor` state machine

**Files:**
- Create: `src/project0/control_panel/supervisor.py`
- Create: `tests/control_panel/test_supervisor.py`

**Context:** See spec §7. The supervisor is a single object with a spawn_fn seam for tests. It tracks state in memory, spawns/stops a child process, and uses an asyncio.Lock for transitions.

- [ ] **Step 1: Write the failing tests**

Create `tests/control_panel/test_supervisor.py`:

```python
"""Supervisor state machine tests using a fake spawn_fn.

Spec: docs/superpowers/specs/2026-04-16-control-panel-design.md §7.
No test spawns a real MAAS subprocess.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from project0.control_panel.supervisor import MAASSupervisor


class FakeProc:
    """Stand-in for asyncio.subprocess.Process with controllable wait().

    Call ``finish(rc)`` to make the pending wait() return. ``terminate``
    and ``kill`` are recorded for assertions. Default pid is 12345.
    """

    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid
        self.terminate_called = False
        self.kill_called = False
        self._done = asyncio.Event()
        self._rc: int = 0

    async def wait(self) -> int:
        await self._done.wait()
        return self._rc

    def terminate(self) -> None:
        self.terminate_called = True

    def kill(self) -> None:
        self.kill_called = True

    def finish(self, rc: int = 0) -> None:
        self._rc = rc
        self._done.set()


def _make_supervisor(proc_queue: list[FakeProc]) -> MAASSupervisor:
    """Supervisor whose spawn_fn returns each queued FakeProc in order."""
    async def spawn() -> Any:
        if not proc_queue:
            raise RuntimeError("no more fake procs queued")
        return proc_queue.pop(0)

    return MAASSupervisor(spawn_fn=spawn, stop_timeout=0.2)


@pytest.mark.asyncio
async def test_initial_state_is_stopped() -> None:
    sup = _make_supervisor([])
    assert sup.state == "stopped"
    assert sup.pid is None
    assert sup.last_exit_code is None


@pytest.mark.asyncio
async def test_start_transitions_to_running() -> None:
    proc = FakeProc(pid=999)
    sup = _make_supervisor([proc])
    await sup.start()
    assert sup.state == "running"
    assert sup.pid == 999


@pytest.mark.asyncio
async def test_stop_sends_sigterm_and_transitions() -> None:
    proc = FakeProc()
    sup = _make_supervisor([proc])
    await sup.start()

    # Run stop concurrently; simulate clean exit after SIGTERM.
    async def clean_exit() -> None:
        await asyncio.sleep(0.01)
        assert proc.terminate_called
        proc.finish(rc=0)

    await asyncio.gather(sup.stop(), clean_exit())
    assert sup.state == "stopped"
    assert proc.terminate_called
    assert not proc.kill_called


@pytest.mark.asyncio
async def test_stop_timeout_triggers_sigkill() -> None:
    proc = FakeProc()
    sup = _make_supervisor([proc])
    await sup.start()

    # Never call proc.finish(); force the wait_for to time out. After
    # SIGKILL, the supervisor still awaits wait(); finish it in the
    # background so the test can complete.
    async def kill_then_finish() -> None:
        while not proc.kill_called:
            await asyncio.sleep(0.01)
        proc.finish(rc=-9)

    await asyncio.gather(sup.stop(), kill_then_finish())
    assert proc.terminate_called
    assert proc.kill_called
    assert sup.state == "stopped"


@pytest.mark.asyncio
async def test_unexpected_exit_transitions_to_crashed() -> None:
    proc = FakeProc()
    sup = _make_supervisor([proc])
    await sup.start()
    # Simulate MAAS crashing on its own (no stop requested).
    proc.finish(rc=7)
    # Give the watcher task a tick to observe.
    await asyncio.sleep(0.05)
    assert sup.state == "crashed"
    assert sup.last_exit_code == 7


@pytest.mark.asyncio
async def test_concurrent_start_does_not_double_spawn() -> None:
    proc = FakeProc()
    sup = _make_supervisor([proc])  # only one in the queue
    # Second start() should observe running state and return without
    # requesting a second spawn.
    await asyncio.gather(sup.start(), sup.start())
    assert sup.state == "running"


@pytest.mark.asyncio
async def test_start_from_stopped_resets_exit_code() -> None:
    proc1 = FakeProc()
    proc2 = FakeProc(pid=222)
    sup = _make_supervisor([proc1, proc2])
    await sup.start()
    proc1.finish(rc=3)
    await asyncio.sleep(0.05)
    assert sup.state == "crashed"
    assert sup.last_exit_code == 3

    await sup.start()
    assert sup.state == "running"
    assert sup.pid == 222
    # last_exit_code is allowed to remain; what matters is that state is running.


@pytest.mark.asyncio
async def test_stop_when_already_stopped_is_noop() -> None:
    sup = _make_supervisor([])
    await sup.stop()
    assert sup.state == "stopped"


@pytest.mark.asyncio
async def test_restart_stop_then_start() -> None:
    proc1 = FakeProc(pid=111)
    proc2 = FakeProc(pid=222)
    sup = _make_supervisor([proc1, proc2])
    await sup.start()
    assert sup.pid == 111

    async def finish_first() -> None:
        await asyncio.sleep(0.01)
        proc1.finish(rc=0)

    await asyncio.gather(sup.restart(), finish_first())
    assert sup.state == "running"
    assert sup.pid == 222
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/control_panel/test_supervisor.py -v`
Expected: FAIL — `project0.control_panel.supervisor` does not exist.

- [ ] **Step 3: Implement `MAASSupervisor`**

Create `src/project0/control_panel/supervisor.py`:

```python
"""In-memory supervisor for MAAS as a child process.

See docs/superpowers/specs/2026-04-16-control-panel-design.md §7.

Design constraints enforced here:
- Single child at a time (state machine).
- All transitions serialized by an asyncio.Lock.
- spawn_fn is injectable for tests; default is the real uv subprocess.
- Watcher task detects unexpected exits and transitions to 'crashed'.
- In-memory state only; panel restart forgets any orphan MAAS.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Literal, Protocol

log = logging.getLogger(__name__)

State = Literal["stopped", "starting", "running", "stopping", "crashed"]


class _Proc(Protocol):
    """Subset of asyncio.subprocess.Process we rely on."""
    pid: int
    async def wait(self) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


SpawnFn = Callable[[], Awaitable[_Proc]]


async def _real_spawn() -> _Proc:
    """Default spawn_fn: launch MAAS via `uv run python -m project0.main`.

    stdout and stderr go to DEVNULL per spec decision — the panel has no
    log view; server-side debug uses the terminal that launched the panel.
    """
    proc = await asyncio.create_subprocess_exec(
        "uv", "run", "python", "-m", "project0.main",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return proc  # type: ignore[return-value]


class MAASSupervisor:
    def __init__(
        self,
        spawn_fn: SpawnFn = _real_spawn,
        stop_timeout: float = 10.0,
    ) -> None:
        self._spawn_fn = spawn_fn
        self._stop_timeout = stop_timeout
        self._state: State = "stopped"
        self._proc: _Proc | None = None
        self._watcher: asyncio.Task[None] | None = None
        self._last_exit_code: int | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> State:
        return self._state

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None and self._state == "running" else None

    @property
    def last_exit_code(self) -> int | None:
        return self._last_exit_code

    async def start(self) -> None:
        async with self._lock:
            if self._state in ("starting", "running", "stopping"):
                return
            self._state = "starting"
            try:
                self._proc = await self._spawn_fn()
            except Exception:
                self._state = "stopped"
                self._proc = None
                raise
            self._state = "running"
            log.info("MAAS spawned, pid=%s", self._proc.pid)
            self._watcher = asyncio.create_task(self._watch(self._proc))

    async def stop(self) -> None:
        async with self._lock:
            if self._state != "running" or self._proc is None:
                return
            self._state = "stopping"
            proc = self._proc
            proc.terminate()
            try:
                rc = await asyncio.wait_for(proc.wait(), timeout=self._stop_timeout)
            except asyncio.TimeoutError:
                log.warning("MAAS did not exit after SIGTERM; sending SIGKILL")
                proc.kill()
                rc = await proc.wait()
            self._last_exit_code = rc
            self._state = "stopped"
            self._proc = None

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _watch(self, proc: _Proc) -> None:
        try:
            rc = await proc.wait()
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self._state == "stopping":
                # stop() is driving the transition; let it finish.
                return
            if self._proc is not proc:
                # Superseded by a newer spawn; ignore.
                return
            self._last_exit_code = rc
            self._state = "crashed"
            self._proc = None
            log.warning("MAAS exited unexpectedly with code=%s", rc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/control_panel/test_supervisor.py -v`
Expected: PASS all 9 tests.

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/project0/control_panel/`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add src/project0/control_panel/supervisor.py tests/control_panel/test_supervisor.py
git commit -m "$(cat <<'EOF'
feat(control_panel): MAASSupervisor state machine with spawn_fn seam

Single-child state machine (stopped/starting/running/stopping/crashed)
with asyncio.Lock serialization and a watcher task for crash detection.
Tests use a FakeProc through the injectable spawn_fn; no real subprocess
is launched in the test suite.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Implement `paths.py` (allowlisted TOML/persona file names)

**Files:**
- Create: `src/project0/control_panel/paths.py`
- Create: `tests/control_panel/test_paths.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/control_panel/test_paths.py`:

```python
"""Allowlisted name → path resolution for TOML and persona edits.

Prevents path traversal via ``..`` or absolute paths in URL params.
Only three base names are permitted: manager, secretary, intelligence.
"""

from pathlib import Path

import pytest

from project0.control_panel.paths import (
    ALLOWED_AGENT_NAMES,
    persona_path,
    toml_path,
)


def test_allowed_names_are_fixed() -> None:
    assert ALLOWED_AGENT_NAMES == ("manager", "secretary", "intelligence")


def test_toml_path_resolves_known_name(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    p = toml_path("manager", project_root=tmp_path)
    assert p == tmp_path / "prompts" / "manager.toml"


def test_toml_path_rejects_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        toml_path("supervisor", project_root=tmp_path)


def test_toml_path_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        toml_path("../secrets", project_root=tmp_path)


def test_persona_path_resolves_known_name(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    p = persona_path("secretary", project_root=tmp_path)
    assert p == tmp_path / "prompts" / "secretary.md"


def test_persona_path_rejects_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        persona_path("random", project_root=tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/control_panel/test_paths.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `paths.py`**

Create `src/project0/control_panel/paths.py`:

```python
"""File-path resolution for TOML and persona edit routes.

All editable TOML and persona files use a three-name allowlist. Any URL
parameter outside this list is a 404 at the route layer; this module
centralizes the allowlist so tests and routes never drift apart.
"""

from __future__ import annotations

from pathlib import Path

ALLOWED_AGENT_NAMES: tuple[str, ...] = ("manager", "secretary", "intelligence")


def toml_path(name: str, *, project_root: Path) -> Path:
    if name not in ALLOWED_AGENT_NAMES:
        raise ValueError(f"unknown agent name: {name!r}")
    return project_root / "prompts" / f"{name}.toml"


def persona_path(name: str, *, project_root: Path) -> Path:
    if name not in ALLOWED_AGENT_NAMES:
        raise ValueError(f"unknown agent name: {name!r}")
    return project_root / "prompts" / f"{name}.md"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/control_panel/test_paths.py -v`
Expected: PASS 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src/project0/control_panel/paths.py tests/control_panel/test_paths.py
git commit -m "$(cat <<'EOF'
feat(control_panel): allowlisted TOML/persona path resolution

Prevents path traversal in /toml/{name} and /personas/{name} URL params
by centralizing the three-name allowlist.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Rendering module + SVG bar chart macro

**Files:**
- Create: `src/project0/control_panel/rendering.py`
- Create: `tests/control_panel/test_rendering.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/control_panel/test_rendering.py`:

```python
"""SVG bar chart renderer for the /usage daily chart.

One <rect> per input row, height proportional to the value. Weekends
rendered with a lighter fill so weekly rhythm is visible. Native SVG
<title> for hover tooltip — zero JS.
"""

from project0.control_panel.rendering import render_bar_chart_svg


def test_empty_rows_returns_empty_svg() -> None:
    svg = render_bar_chart_svg([])
    assert "<svg" in svg
    assert "</svg>" in svg
    assert svg.count("<rect") == 0


def test_one_rect_per_row() -> None:
    rows = [
        {"day": "2026-04-01", "total": 100},
        {"day": "2026-04-02", "total": 200},
        {"day": "2026-04-03", "total": 150},
    ]
    svg = render_bar_chart_svg(rows)
    assert svg.count("<rect") == 3


def test_title_tooltip_contains_day_and_total() -> None:
    rows = [{"day": "2026-04-01", "total": 12345}]
    svg = render_bar_chart_svg(rows)
    assert "<title>2026-04-01: 12,345 tokens</title>" in svg


def test_tallest_bar_has_max_height() -> None:
    rows = [
        {"day": "2026-04-01", "total": 100},
        {"day": "2026-04-02", "total": 1000},  # max
        {"day": "2026-04-03", "total": 500},
    ]
    svg = render_bar_chart_svg(rows, chart_height=120)
    # The max-height bar is drawn with height close to chart_height (minus padding).
    # We assert that the largest height attribute in the SVG is >= any other.
    import re
    heights = [float(h) for h in re.findall(r'height="(\d+(?:\.\d+)?)"', svg)]
    assert heights, "no heights found"
    # The first <svg> element also has a height attribute; filter to <rect> heights
    # by re-finding within rect tags:
    rect_heights = [
        float(m.group(1))
        for m in re.finditer(r'<rect[^>]*\sheight="(\d+(?:\.\d+)?)"', svg)
    ]
    assert len(rect_heights) == 3
    assert max(rect_heights) == rect_heights[1]  # index of 1000 total


def test_max_label_shown() -> None:
    rows = [
        {"day": "2026-04-01", "total": 100},
        {"day": "2026-04-02", "total": 1000},
    ]
    svg = render_bar_chart_svg(rows)
    # Max label text appears somewhere in the SVG (formatted with thousands sep).
    assert "1,000" in svg


def test_weekend_fill_differs_from_weekday() -> None:
    # 2026-04-04 is a Saturday (weekday), 2026-04-06 is a Monday.
    rows = [
        {"day": "2026-04-04", "total": 100},  # Saturday
        {"day": "2026-04-06", "total": 100},  # Monday
    ]
    svg = render_bar_chart_svg(rows)
    # Extract the fill attribute from each rect:
    import re
    fills = re.findall(r'<rect[^>]*\sfill="([^"]+)"', svg)
    assert len(fills) == 2
    assert fills[0] != fills[1]  # Saturday and Monday get different fills
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/control_panel/test_rendering.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `rendering.py`**

Create `src/project0/control_panel/rendering.py`:

```python
"""Jinja2 environment + the SVG bar chart macro used by /usage.

The chart is rendered as a single string of inline <svg> markup so the
template can ``{{ svg | safe }}`` it directly. No JS, no CDN, no
external charting library — 30 days × 1 rect each is a trivial amount
of markup to generate server-side.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates

_PACKAGE_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"


def build_templates() -> Jinja2Templates:
    return Jinja2Templates(directory=str(_TEMPLATES_DIR))


# Chart constants — the SVG is a fixed 800x150 canvas. CSS max-width on the
# <svg> tag lets it shrink responsively. These are deliberately not a
# configurable API; one chart, one size.
_CHART_WIDTH = 800
_CHART_HEIGHT = 150
_PAD_TOP = 20
_PAD_BOTTOM = 10
_PAD_LEFT = 50
_PAD_RIGHT = 10
_WEEKDAY_FILL = "#3366aa"
_WEEKEND_FILL = "#88aadd"


def render_bar_chart_svg(
    rows: list[dict[str, Any]],
    *,
    chart_width: int = _CHART_WIDTH,
    chart_height: int = _CHART_HEIGHT,
) -> str:
    """Render an inline SVG bar chart from rollup rows.

    Each row must have ``day`` (YYYY-MM-DD string) and ``total`` (int).
    Empty list produces an empty chart frame.
    """
    plot_w = chart_width - _PAD_LEFT - _PAD_RIGHT
    plot_h = chart_height - _PAD_TOP - _PAD_BOTTOM

    if not rows:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {chart_width} {chart_height}" '
            f'width="{chart_width}" height="{chart_height}" '
            f'style="max-width:100%;height:auto;">'
            f'<text x="{chart_width//2}" y="{chart_height//2}" '
            f'text-anchor="middle" fill="#888">no data</text>'
            f'</svg>'
        )

    max_total = max(int(r["total"]) for r in rows) or 1
    n = len(rows)
    bar_w = plot_w / n
    gap = max(1.0, bar_w * 0.1)
    draw_w = bar_w - gap

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {chart_width} {chart_height}" '
        f'width="{chart_width}" height="{chart_height}" '
        f'style="max-width:100%;height:auto;font-family:monospace;font-size:10px;">'
    )
    # Max label top-left.
    parts.append(
        f'<text x="4" y="14" fill="#444">{_fmt(max_total)}</text>'
    )
    for i, r in enumerate(rows):
        day_str = str(r["day"])
        total = int(r["total"])
        h = (total / max_total) * plot_h
        x = _PAD_LEFT + i * bar_w
        y = _PAD_TOP + (plot_h - h)
        fill = _WEEKEND_FILL if _is_weekend(day_str) else _WEEKDAY_FILL
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{draw_w:.1f}" '
            f'height="{h:.1f}" fill="{fill}">'
            f'<title>{day_str}: {_fmt(total)} tokens</title>'
            f'</rect>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _fmt(n: int) -> str:
    return f"{n:,}"


def _is_weekend(day_str: str) -> bool:
    try:
        y, m, d = map(int, day_str.split("-"))
        return date(y, m, d).weekday() >= 5
    except (ValueError, IndexError):
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/control_panel/test_rendering.py -v`
Expected: PASS 6 tests.

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/project0/control_panel/`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add src/project0/control_panel/rendering.py tests/control_panel/test_rendering.py
git commit -m "$(cat <<'EOF'
feat(control_panel): SVG bar chart renderer for /usage daily chart

Server-rendered inline SVG, one <rect> per day, <title> tooltips, no
JavaScript library. 30×1 rects per chart is trivial to generate.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: FastAPI app factory + base template + home route

**Files:**
- Create: `src/project0/control_panel/app.py`
- Create: `src/project0/control_panel/routes.py` (home route + maas start/stop/restart)
- Create: `src/project0/control_panel/templates/base.html`
- Create: `src/project0/control_panel/templates/home.html`
- Create: `src/project0/control_panel/static/style.css`
- Create: `tests/control_panel/conftest.py`
- Create: `tests/control_panel/test_home.py`
- Create: `tests/control_panel/test_supervisor_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/control_panel/conftest.py`:

```python
"""Shared fixtures: a minimal project root + supervisor + FastAPI TestClient.

Every route test uses this conftest so the app construction stays in one
place. Tests never spawn a real MAAS.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from project0.control_panel.app import create_app
from project0.control_panel.supervisor import MAASSupervisor
from project0.store import Store


class FakeProc:
    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid
        self.terminate_called = False
        self.kill_called = False
        self._done = asyncio.Event()
        self._rc = 0

    async def wait(self) -> int:
        await self._done.wait()
        return self._rc

    def terminate(self) -> None:
        self.terminate_called = True
        # Tests usually want clean exit immediately after terminate:
        self._done.set()

    def kill(self) -> None:
        self.kill_called = True
        self._done.set()


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """A minimal project root with data/ and prompts/ directories."""
    (tmp_path / "data").mkdir()
    (tmp_path / "prompts").mkdir()
    # Seed the three TOML files with placeholder content.
    for name in ("manager", "secretary", "intelligence"):
        (tmp_path / "prompts" / f"{name}.toml").write_text(
            f"# placeholder for {name}\n", encoding="utf-8"
        )
        (tmp_path / "prompts" / f"{name}.md").write_text(
            f"# Persona {name}\n", encoding="utf-8"
        )
    # Seed .env
    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=sk-fake\nANTHROPIC_CACHE_TTL=ephemeral\n",
        encoding="utf-8",
    )
    # Seed profile
    (tmp_path / "data" / "user_profile.yaml").write_text(
        "address_as: 主人\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def store(project_root: Path) -> Store:
    s = Store(project_root / "data" / "store.db")
    s.init_schema()
    return s


@pytest.fixture
def supervisor() -> MAASSupervisor:
    """Supervisor whose spawn_fn always returns a fresh FakeProc."""
    async def spawn() -> Any:
        return FakeProc()
    return MAASSupervisor(spawn_fn=spawn, stop_timeout=0.2)


@pytest.fixture
def client(project_root: Path, store: Store, supervisor: MAASSupervisor) -> TestClient:
    app = create_app(
        supervisor=supervisor,
        store=store,
        project_root=project_root,
    )
    return TestClient(app)
```

Create `tests/control_panel/test_home.py`:

```python
"""GET / renders the status header and some content."""

from fastapi.testclient import TestClient


def test_home_200(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200


def test_home_shows_stopped_status_initially(client: TestClient) -> None:
    r = client.get("/")
    assert "stopped" in r.text.lower()


def test_home_has_nav_links(client: TestClient) -> None:
    r = client.get("/")
    for href in ("/profile", "/facts", "/toml", "/personas", "/env", "/usage"):
        assert href in r.text
```

Create `tests/control_panel/test_supervisor_routes.py`:

```python
"""POST /maas/start, /stop, /restart drive the supervisor."""

from fastapi.testclient import TestClient


def test_start_transitions_to_running(client: TestClient) -> None:
    r = client.post("/maas/start", follow_redirects=False)
    assert r.status_code in (302, 303)
    r2 = client.get("/")
    assert "running" in r2.text.lower()


def test_stop_after_start_transitions_to_stopped(client: TestClient) -> None:
    client.post("/maas/start")
    r = client.post("/maas/stop", follow_redirects=False)
    assert r.status_code in (302, 303)
    r2 = client.get("/")
    assert "stopped" in r2.text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/control_panel/test_home.py tests/control_panel/test_supervisor_routes.py -v`
Expected: FAIL — `create_app` does not exist.

- [ ] **Step 3: Implement `app.py` + `routes.py` + templates**

Create `src/project0/control_panel/app.py`:

```python
"""FastAPI app factory for the control panel.

Construction is a factory (create_app) so tests can inject a fake
supervisor, a tmp project root, and a tmp Store. The real entry point
(__main__.py) constructs the production versions.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from project0.control_panel import routes
from project0.control_panel.rendering import build_templates
from project0.control_panel.supervisor import MAASSupervisor
from project0.store import Store

_PACKAGE_DIR = Path(__file__).parent
_STATIC_DIR = _PACKAGE_DIR / "static"


def create_app(
    *,
    supervisor: MAASSupervisor,
    store: Store,
    project_root: Path,
) -> FastAPI:
    app = FastAPI(
        title="MAAS Control Panel",
        description="Single-user control panel for Project 0 / MAAS.",
    )
    app.state.supervisor = supervisor
    app.state.store = store
    app.state.project_root = project_root
    app.state.templates = build_templates()
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(routes.router)
    return app
```

Create `src/project0/control_panel/routes.py` (initial version — will grow in later tasks):

```python
"""HTTP routes for the control panel.

Each route group (home, profile, facts, toml, personas, env, usage) lives
in this one file for now. If it grows past ~400 lines it can be split by
concern. Responses are always HTML pages or redirects — never JSON.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter()


def _ctx(request: Request, **extra: object) -> dict[str, object]:
    sup = request.app.state.supervisor
    base = {
        "request": request,
        "maas_state": sup.state,
        "maas_pid": sup.pid,
        "maas_last_exit_code": sup.last_exit_code,
    }
    base.update(extra)
    return base


@router.get("/")
async def home(request: Request) -> object:
    templates = request.app.state.templates
    return templates.TemplateResponse("home.html", _ctx(request))


@router.post("/maas/start")
async def maas_start(request: Request) -> RedirectResponse:
    await request.app.state.supervisor.start()
    return RedirectResponse(url="/", status_code=303)


@router.post("/maas/stop")
async def maas_stop(request: Request) -> RedirectResponse:
    await request.app.state.supervisor.stop()
    return RedirectResponse(url="/", status_code=303)


@router.post("/maas/restart")
async def maas_restart(request: Request) -> RedirectResponse:
    await request.app.state.supervisor.restart()
    return RedirectResponse(url="/", status_code=303)
```

Create `src/project0/control_panel/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MAAS Control Panel</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header class="status-bar">
    <strong>MAAS Control</strong>
    Status: <span class="state state-{{ maas_state }}">● {{ maas_state }}</span>
    {% if maas_pid is not none %}(PID {{ maas_pid }}){% endif %}
    {% if maas_last_exit_code is not none and maas_state == "crashed" %}
      (last exit: {{ maas_last_exit_code }})
    {% endif %}
    <form method="post" action="/maas/start" style="display:inline">
      <button type="submit" {% if maas_state in ("starting","running","stopping") %}disabled{% endif %}>Start</button>
    </form>
    <form method="post" action="/maas/stop" style="display:inline">
      <button type="submit" {% if maas_state != "running" %}disabled{% endif %}>Stop</button>
    </form>
    <form method="post" action="/maas/restart" style="display:inline">
      <button type="submit" {% if maas_state != "running" %}disabled{% endif %}>Restart</button>
    </form>
  </header>
  <nav class="main-nav">
    <a href="/">Home</a>
    <a href="/profile">Profile</a>
    <a href="/facts">Facts</a>
    <a href="/toml">TOML</a>
    <a href="/personas">Personas</a>
    <a href="/env">.env</a>
    <a href="/usage">Token Usage</a>
  </nav>
  <main>
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

Create `src/project0/control_panel/templates/home.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>MAAS Control Panel</h1>
<p>Use the navigation above to edit settings or view token usage.</p>
<p>MAAS is currently <strong>{{ maas_state }}</strong>.</p>
{% endblock %}
```

Create `src/project0/control_panel/static/style.css`:

```css
body { font-family: system-ui, sans-serif; max-width: 960px; margin: 1em auto; padding: 0 1em; }
.status-bar { padding: 0.5em; background: #f0f0f0; border: 1px solid #ccc; }
.status-bar button { margin-left: 0.5em; }
.main-nav { padding: 0.5em 0; border-bottom: 1px solid #ccc; }
.main-nav a { margin-right: 1em; }
.state-stopped { color: #888; }
.state-starting, .state-stopping { color: #e68a00; }
.state-running { color: #2d7; }
.state-crashed { color: #c33; }
textarea { width: 100%; min-height: 400px; font-family: monospace; font-size: 13px; }
table { border-collapse: collapse; width: 100%; }
table th, table td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; font-size: 13px; }
.flash { padding: 0.5em; background: #ffc; border: 1px solid #dd6; margin: 1em 0; }
form.inline { display: inline; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/control_panel/test_home.py tests/control_panel/test_supervisor_routes.py -v`
Expected: PASS 5 tests.

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/project0/control_panel/`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add src/project0/control_panel/ tests/control_panel/
git commit -m "$(cat <<'EOF'
feat(control_panel): FastAPI factory + base layout + supervisor routes

Wires create_app, base.html (status header + nav), /, /maas/start,
/maas/stop, /maas/restart. Tests drive the supervisor through the fake
spawn_fn so no real subprocess is launched.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Profile route (GET + POST)

**Files:**
- Modify: `src/project0/control_panel/routes.py`
- Create: `src/project0/control_panel/templates/profile.html`
- Create: `tests/control_panel/test_profile_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/control_panel/test_profile_routes.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient


def test_get_renders_existing_profile(client: TestClient, project_root: Path) -> None:
    r = client.get("/profile")
    assert r.status_code == 200
    assert "address_as: 主人" in r.text


def test_get_when_missing_file_returns_empty_textarea(
    client: TestClient, project_root: Path
) -> None:
    (project_root / "data" / "user_profile.yaml").unlink()
    r = client.get("/profile")
    assert r.status_code == 200
    # The form still renders; the textarea is empty.
    assert "<textarea" in r.text


def test_post_overwrites_file(client: TestClient, project_root: Path) -> None:
    new_content = "address_as: 陛下\nbirthday: '2000-01-01'\n"
    r = client.post("/profile", data={"content": new_content}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert (project_root / "data" / "user_profile.yaml").read_text(encoding="utf-8") == new_content


def test_post_survives_chinese_content(client: TestClient, project_root: Path) -> None:
    new_content = "out_of_band_notes: |\n  我喜欢吃寿司\n"
    client.post("/profile", data={"content": new_content})
    assert "我喜欢吃寿司" in (project_root / "data" / "user_profile.yaml").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/control_panel/test_profile_routes.py -v`
Expected: FAIL — `/profile` is a 404.

- [ ] **Step 3: Add profile routes + template**

In `src/project0/control_panel/routes.py`, add at the top:

```python
from fastapi import Form

from project0.control_panel.writes import atomic_write_text
```

Then append:

```python
@router.get("/profile")
async def profile_get(request: Request) -> object:
    templates = request.app.state.templates
    path = request.app.state.project_root / "data" / "user_profile.yaml"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return templates.TemplateResponse("profile.html", _ctx(request, content=content))


@router.post("/profile")
async def profile_post(
    request: Request,
    content: str = Form(...),
) -> RedirectResponse:
    path = request.app.state.project_root / "data" / "user_profile.yaml"
    atomic_write_text(path, content)
    return RedirectResponse(url="/profile", status_code=303)
```

Create `src/project0/control_panel/templates/profile.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>User Profile (Layer A)</h1>
<p>Edit <code>data/user_profile.yaml</code>. <strong>Restart MAAS after saving to apply.</strong></p>
<form method="post" action="/profile">
  <textarea name="content">{{ content }}</textarea>
  <p><button type="submit">Save</button></p>
</form>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/control_panel/test_profile_routes.py -v`
Expected: PASS 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/project0/control_panel/routes.py src/project0/control_panel/templates/profile.html tests/control_panel/test_profile_routes.py
git commit -m "$(cat <<'EOF'
feat(control_panel): profile GET/POST routes with atomic save

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Facts routes (list + add + edit + deactivate + reactivate + delete)

**Files:**
- Modify: `src/project0/control_panel/routes.py`
- Create: `src/project0/control_panel/templates/facts.html`
- Create: `tests/control_panel/test_facts_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/control_panel/test_facts_routes.py`:

```python
from fastapi.testclient import TestClient

from project0.store import Store, UserFactsReader


def test_facts_list_empty(client: TestClient) -> None:
    r = client.get("/facts")
    assert r.status_code == 200
    assert "<form" in r.text  # the add-fact form is present


def test_facts_add_creates_row_with_human_author(client: TestClient, store: Store) -> None:
    r = client.post(
        "/facts",
        data={"fact_text": "用户喜欢寿司", "topic": "food"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    facts = UserFactsReader("secretary", store.conn).active()
    assert len(facts) == 1
    assert facts[0].fact_text == "用户喜欢寿司"
    assert facts[0].author_agent == "human"
    assert facts[0].topic == "food"


def test_facts_list_shows_added_fact(client: TestClient) -> None:
    client.post("/facts", data={"fact_text": "A", "topic": ""})
    r = client.get("/facts")
    assert "A" in r.text


def test_facts_edit(client: TestClient, store: Store) -> None:
    client.post("/facts", data={"fact_text": "old", "topic": ""})
    fact_id = UserFactsReader("secretary", store.conn).active()[0].id
    client.post(f"/facts/{fact_id}/edit", data={"fact_text": "new", "topic": "t"})
    facts = UserFactsReader("secretary", store.conn).active()
    assert facts[0].fact_text == "new"
    assert facts[0].topic == "t"


def test_facts_deactivate_then_reactivate(client: TestClient, store: Store) -> None:
    client.post("/facts", data={"fact_text": "X", "topic": ""})
    fid = UserFactsReader("secretary", store.conn).active()[0].id

    client.post(f"/facts/{fid}/deactivate")
    assert UserFactsReader("secretary", store.conn).active() == []

    client.post(f"/facts/{fid}/reactivate")
    assert len(UserFactsReader("secretary", store.conn).active()) == 1


def test_facts_delete_removes_row(client: TestClient, store: Store) -> None:
    client.post("/facts", data={"fact_text": "X", "topic": ""})
    fid = UserFactsReader("secretary", store.conn).active()[0].id
    client.post(f"/facts/{fid}/delete")
    assert UserFactsReader("secretary", store.conn).active() == []
    assert UserFactsReader("secretary", store.conn).all_including_inactive() == []


def test_show_inactive_toggle(client: TestClient, store: Store) -> None:
    client.post("/facts", data={"fact_text": "live", "topic": ""})
    client.post("/facts", data={"fact_text": "gone", "topic": ""})
    facts = UserFactsReader("secretary", store.conn).active()
    gone_id = [f for f in facts if f.fact_text == "gone"][0].id
    client.post(f"/facts/{gone_id}/deactivate")

    r_default = client.get("/facts")
    assert "live" in r_default.text
    assert "gone" not in r_default.text

    r_all = client.get("/facts?show_inactive=1")
    assert "live" in r_all.text
    assert "gone" in r_all.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/control_panel/test_facts_routes.py -v`
Expected: FAIL — routes do not exist.

- [ ] **Step 3: Add facts routes + template**

In `src/project0/control_panel/routes.py`, add imports at the top:

```python
from project0.store import UserFactsReader, UserFactsWriter
```

Append the routes:

```python
@router.get("/facts")
async def facts_list(
    request: Request,
    show_inactive: int = 0,
) -> object:
    templates = request.app.state.templates
    store = request.app.state.store
    reader = UserFactsReader("human", store.conn)
    if show_inactive:
        facts = reader.all_including_inactive()
    else:
        facts = reader.active(limit=500)
    return templates.TemplateResponse(
        "facts.html",
        _ctx(request, facts=facts, show_inactive=bool(show_inactive)),
    )


@router.post("/facts")
async def facts_add(
    request: Request,
    fact_text: str = Form(...),
    topic: str = Form(""),
) -> RedirectResponse:
    store = request.app.state.store
    writer = UserFactsWriter("human", store.conn)
    writer.add(fact_text, topic=topic or None)
    return RedirectResponse(url="/facts", status_code=303)


@router.post("/facts/{fact_id}/edit")
async def facts_edit(
    request: Request,
    fact_id: int,
    fact_text: str = Form(...),
    topic: str = Form(""),
) -> RedirectResponse:
    store = request.app.state.store
    writer = UserFactsWriter("human", store.conn)
    writer.edit(fact_id, fact_text, topic or None)
    return RedirectResponse(url="/facts", status_code=303)


@router.post("/facts/{fact_id}/deactivate")
async def facts_deactivate(request: Request, fact_id: int) -> RedirectResponse:
    store = request.app.state.store
    writer = UserFactsWriter("human", store.conn)
    writer.deactivate(fact_id)
    return RedirectResponse(url="/facts", status_code=303)


@router.post("/facts/{fact_id}/reactivate")
async def facts_reactivate(request: Request, fact_id: int) -> RedirectResponse:
    store = request.app.state.store
    writer = UserFactsWriter("human", store.conn)
    writer.reactivate(fact_id)
    return RedirectResponse(url="/facts?show_inactive=1", status_code=303)


@router.post("/facts/{fact_id}/delete")
async def facts_delete(request: Request, fact_id: int) -> RedirectResponse:
    store = request.app.state.store
    writer = UserFactsWriter("human", store.conn)
    writer.delete(fact_id)
    return RedirectResponse(url="/facts", status_code=303)
```

Note: `UserFactsReader` accepts any agent_name as a construction parameter (it is read-only and ungated in `store.py`). We pass `"human"` for clarity.

Create `src/project0/control_panel/templates/facts.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>User Facts (Layer D)</h1>
<p>
  {% if show_inactive %}
    Showing all facts including inactive. <a href="/facts">Hide inactive</a>
  {% else %}
    Showing active facts only. <a href="/facts?show_inactive=1">Show inactive</a>
  {% endif %}
</p>

<h2>Add fact</h2>
<form method="post" action="/facts">
  <p><label>Fact text: <input type="text" name="fact_text" required style="width:60%"></label></p>
  <p><label>Topic (optional): <input type="text" name="topic"></label></p>
  <p><button type="submit">Add</button></p>
</form>

<h2>Existing facts</h2>
<table>
  <thead>
    <tr><th>id</th><th>ts</th><th>author</th><th>fact</th><th>topic</th><th>active</th><th>actions</th></tr>
  </thead>
  <tbody>
    {% for f in facts %}
    <tr>
      <td>{{ f.id }}</td>
      <td>{{ f.ts }}</td>
      <td>{{ f.author_agent }}</td>
      <td>
        <form method="post" action="/facts/{{ f.id }}/edit" class="inline">
          <input type="text" name="fact_text" value="{{ f.fact_text }}" style="width:40ch">
          <input type="text" name="topic" value="{{ f.topic or '' }}" style="width:12ch">
          <button type="submit">Save</button>
        </form>
      </td>
      <td>{{ f.topic or '' }}</td>
      <td>{{ '✓' if f.is_active else '✗' }}</td>
      <td>
        {% if f.is_active %}
          <form method="post" action="/facts/{{ f.id }}/deactivate" class="inline"><button>Deactivate</button></form>
        {% else %}
          <form method="post" action="/facts/{{ f.id }}/reactivate" class="inline"><button>Reactivate</button></form>
        {% endif %}
        <form method="post" action="/facts/{{ f.id }}/delete" class="inline"
              onsubmit="return confirm('Hard delete fact {{ f.id }}? This cannot be undone.');">
          <button>Delete</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/control_panel/test_facts_routes.py -v`
Expected: PASS all 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/project0/control_panel/routes.py src/project0/control_panel/templates/facts.html tests/control_panel/test_facts_routes.py
git commit -m "$(cat <<'EOF'
feat(control_panel): facts CRUD routes (add/edit/deactivate/reactivate/delete)

All writes go through UserFactsWriter('human', ...). Hard delete is
behind a confirm() dialog in the template.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: TOML list + edit routes

**Files:**
- Modify: `src/project0/control_panel/routes.py`
- Create: `src/project0/control_panel/templates/toml_list.html`
- Create: `src/project0/control_panel/templates/toml_edit.html`
- Create: `tests/control_panel/test_toml_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/control_panel/test_toml_routes.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient


def test_toml_list_shows_three_files(client: TestClient) -> None:
    r = client.get("/toml")
    assert r.status_code == 200
    for name in ("manager", "secretary", "intelligence"):
        assert name in r.text


def test_toml_edit_renders_file(client: TestClient, project_root: Path) -> None:
    (project_root / "prompts" / "manager.toml").write_text(
        "transcript_window = 10\n", encoding="utf-8"
    )
    r = client.get("/toml/manager")
    assert r.status_code == 200
    assert "transcript_window" in r.text


def test_toml_edit_unknown_name_404(client: TestClient) -> None:
    r = client.get("/toml/supervisor")
    assert r.status_code == 404


def test_toml_edit_traversal_404(client: TestClient) -> None:
    r = client.get("/toml/..%2Fevil")
    assert r.status_code == 404


def test_toml_post_overwrites(client: TestClient, project_root: Path) -> None:
    new = "transcript_window = 5\n"
    r = client.post("/toml/manager", data={"content": new}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert (project_root / "prompts" / "manager.toml").read_text(encoding="utf-8") == new


def test_toml_post_unknown_name_404(client: TestClient) -> None:
    r = client.post("/toml/supervisor", data={"content": "x"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/control_panel/test_toml_routes.py -v`
Expected: FAIL.

- [ ] **Step 3: Add TOML routes + templates**

In `routes.py`, add imports at the top:

```python
from fastapi import HTTPException

from project0.control_panel.paths import (
    ALLOWED_AGENT_NAMES,
    persona_path,
    toml_path,
)
```

Append:

```python
@router.get("/toml")
async def toml_list(request: Request) -> object:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "toml_list.html",
        _ctx(request, names=ALLOWED_AGENT_NAMES),
    )


@router.get("/toml/{name}")
async def toml_edit_get(request: Request, name: str) -> object:
    templates = request.app.state.templates
    try:
        path = toml_path(name, project_root=request.app.state.project_root)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return templates.TemplateResponse(
        "toml_edit.html",
        _ctx(request, name=name, content=content),
    )


@router.post("/toml/{name}")
async def toml_edit_post(
    request: Request,
    name: str,
    content: str = Form(...),
) -> RedirectResponse:
    try:
        path = toml_path(name, project_root=request.app.state.project_root)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    atomic_write_text(path, content)
    return RedirectResponse(url=f"/toml/{name}", status_code=303)
```

Create `src/project0/control_panel/templates/toml_list.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>TOML config files</h1>
<ul>
  {% for n in names %}
  <li><a href="/toml/{{ n }}">prompts/{{ n }}.toml</a></li>
  {% endfor %}
</ul>
<p><strong>Restart MAAS after saving any change.</strong></p>
{% endblock %}
```

Create `src/project0/control_panel/templates/toml_edit.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>prompts/{{ name }}.toml</h1>
<p><a href="/toml">← back to list</a></p>
<form method="post" action="/toml/{{ name }}">
  <textarea name="content">{{ content }}</textarea>
  <p><button type="submit">Save</button></p>
</form>
<p><strong>Restart MAAS to apply.</strong></p>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/control_panel/test_toml_routes.py -v`
Expected: PASS 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src/project0/control_panel/routes.py src/project0/control_panel/templates/toml_list.html src/project0/control_panel/templates/toml_edit.html tests/control_panel/test_toml_routes.py
git commit -m "$(cat <<'EOF'
feat(control_panel): TOML list + edit routes with allowlisted names

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Persona list + edit routes

**Files:**
- Modify: `src/project0/control_panel/routes.py`
- Create: `src/project0/control_panel/templates/personas_list.html`
- Create: `src/project0/control_panel/templates/personas_edit.html`
- Create: `tests/control_panel/test_personas_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/control_panel/test_personas_routes.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient


def test_personas_list(client: TestClient) -> None:
    r = client.get("/personas")
    assert r.status_code == 200
    for name in ("manager", "secretary", "intelligence"):
        assert name in r.text


def test_personas_edit_renders(client: TestClient, project_root: Path) -> None:
    (project_root / "prompts" / "secretary.md").write_text("# Secretary\n", encoding="utf-8")
    r = client.get("/personas/secretary")
    assert r.status_code == 200
    assert "# Secretary" in r.text


def test_personas_edit_unknown_404(client: TestClient) -> None:
    r = client.get("/personas/random")
    assert r.status_code == 404


def test_personas_post_overwrites(client: TestClient, project_root: Path) -> None:
    r = client.post(
        "/personas/manager",
        data={"content": "# New\n"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert (project_root / "prompts" / "manager.md").read_text(encoding="utf-8") == "# New\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/control_panel/test_personas_routes.py -v`
Expected: FAIL.

- [ ] **Step 3: Add persona routes + templates**

Append to `routes.py`:

```python
@router.get("/personas")
async def personas_list(request: Request) -> object:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "personas_list.html",
        _ctx(request, names=ALLOWED_AGENT_NAMES),
    )


@router.get("/personas/{name}")
async def personas_edit_get(request: Request, name: str) -> object:
    templates = request.app.state.templates
    try:
        path = persona_path(name, project_root=request.app.state.project_root)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return templates.TemplateResponse(
        "personas_edit.html",
        _ctx(request, name=name, content=content),
    )


@router.post("/personas/{name}")
async def personas_edit_post(
    request: Request,
    name: str,
    content: str = Form(...),
) -> RedirectResponse:
    try:
        path = persona_path(name, project_root=request.app.state.project_root)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    atomic_write_text(path, content)
    return RedirectResponse(url=f"/personas/{name}", status_code=303)
```

Create `src/project0/control_panel/templates/personas_list.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>Persona markdown files</h1>
<ul>
  {% for n in names %}
  <li><a href="/personas/{{ n }}">prompts/{{ n }}.md</a></li>
  {% endfor %}
</ul>
<p><strong>Restart MAAS after saving any change.</strong></p>
{% endblock %}
```

Create `src/project0/control_panel/templates/personas_edit.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>prompts/{{ name }}.md</h1>
<p><a href="/personas">← back to list</a></p>
<form method="post" action="/personas/{{ name }}">
  <textarea name="content">{{ content }}</textarea>
  <p><button type="submit">Save</button></p>
</form>
<p><strong>Restart MAAS to apply.</strong></p>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/control_panel/test_personas_routes.py -v`
Expected: PASS 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/project0/control_panel/routes.py src/project0/control_panel/templates/personas_list.html src/project0/control_panel/templates/personas_edit.html tests/control_panel/test_personas_routes.py
git commit -m "$(cat <<'EOF'
feat(control_panel): persona list + edit routes

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: `.env` route

**Files:**
- Modify: `src/project0/control_panel/routes.py`
- Create: `src/project0/control_panel/templates/env.html`
- Create: `tests/control_panel/test_env_route.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/control_panel/test_env_route.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient


def test_env_get_renders_verbatim_including_secret(
    client: TestClient, project_root: Path
) -> None:
    # Secrets are rendered as-is. The panel is Tailscale-gated; no masking.
    r = client.get("/env")
    assert r.status_code == 200
    assert "ANTHROPIC_API_KEY=sk-fake" in r.text


def test_env_get_missing_file_empty_textarea(
    client: TestClient, project_root: Path
) -> None:
    (project_root / ".env").unlink()
    r = client.get("/env")
    assert r.status_code == 200
    assert "<textarea" in r.text


def test_env_post_overwrites(client: TestClient, project_root: Path) -> None:
    new = "ANTHROPIC_API_KEY=sk-new\nANTHROPIC_CACHE_TTL=1h\n"
    r = client.post("/env", data={"content": new}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert (project_root / ".env").read_text(encoding="utf-8") == new
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/control_panel/test_env_route.py -v`
Expected: FAIL.

- [ ] **Step 3: Add env routes + template**

Append to `routes.py`:

```python
@router.get("/env")
async def env_get(request: Request) -> object:
    templates = request.app.state.templates
    path = request.app.state.project_root / ".env"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return templates.TemplateResponse("env.html", _ctx(request, content=content))


@router.post("/env")
async def env_post(
    request: Request,
    content: str = Form(...),
) -> RedirectResponse:
    path = request.app.state.project_root / ".env"
    atomic_write_text(path, content)
    return RedirectResponse(url="/env", status_code=303)
```

Create `src/project0/control_panel/templates/env.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>.env</h1>
<p><strong>Secrets are shown verbatim.</strong> Tailscale is the gate. Restart MAAS after saving.</p>
<form method="post" action="/env">
  <textarea name="content">{{ content }}</textarea>
  <p><button type="submit">Save</button></p>
</form>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/control_panel/test_env_route.py -v`
Expected: PASS 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/project0/control_panel/routes.py src/project0/control_panel/templates/env.html tests/control_panel/test_env_route.py
git commit -m "$(cat <<'EOF'
feat(control_panel): .env edit route (verbatim, no masking)

Tailscale is the gate. User explicitly chose no secret masking for v1.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Token usage page

**Files:**
- Modify: `src/project0/control_panel/routes.py`
- Create: `src/project0/control_panel/templates/usage.html`
- Create: `tests/control_panel/test_usage_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/control_panel/test_usage_routes.py`:

```python
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from project0.store import Store


def _seed(store: Store) -> None:
    # 30 distinct days, each with one row.
    base = datetime.now(UTC).replace(hour=10, minute=0, second=0, microsecond=0)
    for i in range(30):
        day = base - timedelta(days=i)
        ts = day.isoformat(timespec="seconds").replace("+00:00", "Z")
        store.conn.execute(
            "INSERT INTO llm_usage "
            "(ts, agent, model, input_tokens, cache_creation_input_tokens, "
            " cache_read_input_tokens, output_tokens, envelope_id, purpose) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                "secretary" if i % 2 == 0 else "manager",
                "claude-sonnet-4-6",
                100 * (i + 1),        # input_tokens
                0,
                50 * (i + 1),         # cache_read
                25 * (i + 1),         # output
                None,
                "reply" if i % 2 == 0 else "tool_loop",
            ),
        )


def test_usage_200(client: TestClient, store: Store) -> None:
    _seed(store)
    r = client.get("/usage")
    assert r.status_code == 200


def test_usage_chart_has_expected_rect_count(client: TestClient, store: Store) -> None:
    _seed(store)
    r = client.get("/usage")
    assert r.text.count("<rect") == 30


def test_usage_table_contains_agent_rollup(client: TestClient, store: Store) -> None:
    _seed(store)
    r = client.get("/usage")
    # Both agents appear in the per-agent rollup.
    assert "secretary" in r.text
    assert "manager" in r.text


def test_usage_recent_table_rows(client: TestClient, store: Store) -> None:
    _seed(store)
    r = client.get("/usage")
    # 30 rows inserted, recent() is limit 50 so all 30 visible.
    # Count table rows containing one of the agent names in the recent table;
    # the agent rollup already contains both agents, so a raw count is tricky.
    # Instead, assert a representative row's input_token value appears.
    assert "3,000" in r.text or "3000" in r.text  # day 30 * 100 = 3000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/control_panel/test_usage_routes.py -v`
Expected: FAIL.

- [ ] **Step 3: Add usage route + template**

Append to `routes.py`:

```python
from project0.control_panel.rendering import render_bar_chart_svg


@router.get("/usage")
async def usage(request: Request) -> object:
    templates = request.app.state.templates
    store = request.app.state.store
    usage_store = store.llm_usage()
    daily = usage_store.daily_rollup(days=30)
    # Reverse so the SVG draws oldest→newest left-to-right.
    chart_rows = [
        {
            "day": r["day"],
            "total": r["in_tok"] + r["cc_tok"] + r["cr_tok"] + r["out_tok"],
        }
        for r in reversed(daily)
    ]
    chart_svg = render_bar_chart_svg(chart_rows)
    agent_rows = usage_store.agent_rollup(days=7)
    recent_rows = usage_store.recent(limit=50)
    return templates.TemplateResponse(
        "usage.html",
        _ctx(
            request,
            chart_svg=chart_svg,
            daily_rows=daily,
            agent_rows=agent_rows,
            recent_rows=recent_rows,
        ),
    )
```

Create `src/project0/control_panel/templates/usage.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>Token Usage</h1>

<h2>Daily (last 30 days)</h2>
<div class="chart">{{ chart_svg | safe }}</div>
<table>
  <thead>
    <tr><th>Day</th><th>Calls</th><th>Input</th><th>Cache-create</th><th>Cache-read</th><th>Output</th></tr>
  </thead>
  <tbody>
    {% for r in daily_rows %}
    <tr>
      <td>{{ r.day }}</td>
      <td>{{ "{:,}".format(r.calls) }}</td>
      <td>{{ "{:,}".format(r.in_tok) }}</td>
      <td>{{ "{:,}".format(r.cc_tok) }}</td>
      <td>{{ "{:,}".format(r.cr_tok) }}</td>
      <td>{{ "{:,}".format(r.out_tok) }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>

<h2>Per agent × purpose (last 7 days)</h2>
<table>
  <thead>
    <tr><th>Agent</th><th>Purpose</th><th>Calls</th><th>Input (all)</th><th>Output</th></tr>
  </thead>
  <tbody>
    {% for r in agent_rows %}
    <tr>
      <td>{{ r.agent }}</td>
      <td>{{ r.purpose }}</td>
      <td>{{ "{:,}".format(r.calls) }}</td>
      <td>{{ "{:,}".format(r.in_total) }}</td>
      <td>{{ "{:,}".format(r.out_total) }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>

<h2>Recent calls (last 50)</h2>
<table>
  <thead>
    <tr>
      <th>id</th><th>ts</th><th>agent</th><th>purpose</th><th>model</th>
      <th>in</th><th>cc</th><th>cr</th><th>out</th><th>env</th>
    </tr>
  </thead>
  <tbody>
    {% for r in recent_rows %}
    <tr>
      <td>{{ r.id }}</td>
      <td>{{ r.ts }}</td>
      <td>{{ r.agent }}</td>
      <td>{{ r.purpose }}</td>
      <td>{{ r.model }}</td>
      <td>{{ "{:,}".format(r.input_tokens) }}</td>
      <td>{{ "{:,}".format(r.cache_creation_input_tokens) }}</td>
      <td>{{ "{:,}".format(r.cache_read_input_tokens) }}</td>
      <td>{{ "{:,}".format(r.output_tokens) }}</td>
      <td>{{ r.envelope_id if r.envelope_id is not none else '—' }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/control_panel/test_usage_routes.py -v`
Expected: PASS 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/project0/control_panel/routes.py src/project0/control_panel/templates/usage.html tests/control_panel/test_usage_routes.py
git commit -m "$(cat <<'EOF'
feat(control_panel): /usage page with SVG chart + three rollup tables

Daily chart (30d), agent×purpose table (7d), recent calls (50).
No filters, no pricing math.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: `__main__.py` entry point

**Files:**
- Create: `src/project0/control_panel/__main__.py`

- [ ] **Step 1: Implement the entry point**

Create `src/project0/control_panel/__main__.py`:

```python
"""Entry point: ``uv run python -m project0.control_panel``.

Binds to 0.0.0.0:8090. Tailscale-gated by deployment convention — same
as the Intelligence webapp. The supervisor is constructed with the real
spawn_fn (uv run python -m project0.main).
"""

from __future__ import annotations

from pathlib import Path

import uvicorn

from project0.control_panel.app import create_app
from project0.control_panel.supervisor import MAASSupervisor
from project0.store import Store


def main() -> None:
    project_root = Path.cwd()
    store_path = project_root / "data" / "store.db"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(store_path)
    store.init_schema()

    supervisor = MAASSupervisor()  # real spawn_fn
    app = create_app(
        supervisor=supervisor,
        store=store,
        project_root=project_root,
    )
    uvicorn.run(app, host="0.0.0.0", port=8090, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports without error**

Run: `uv run python -c "from project0.control_panel.__main__ import main; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Type-check**

Run: `uv run mypy src/project0/control_panel/`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add src/project0/control_panel/__main__.py
git commit -m "$(cat <<'EOF'
feat(control_panel): __main__ entry point binding 0.0.0.0:8090

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 17: README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a new section documenting the control panel**

In `README.md`, add a new section after "Things to try" and before "Configuration reference" (or wherever it fits the existing structure). Insert:

```markdown
## Control panel (WebUI)

A separate web surface for editing every human-tweakable setting and
starting/stopping MAAS without touching a terminal. Runs as its own
process and supervises MAAS as a child.

### Starting the panel

```bash
uv run python -m project0.control_panel
```

Panel binds to `0.0.0.0:8090`. Same Tailscale deployment story as the
Intelligence webapp — reach it from your phone via your Tailnet IP.

### What it does

- **Start / Stop / Restart MAAS** — the panel spawns MAAS as a child
  process; MAAS's lifecycle is in the panel's hands while the panel is
  running.
- **Edit `data/user_profile.yaml`**, **`prompts/*.toml`**,
  **`prompts/*.md`**, and **`.env`** — plain textarea, Save, then click
  Restart on the header to apply.
- **Full CRUD on `user_facts`** — add, edit, deactivate/reactivate, and
  hard delete individual facts. Changes are live (shared SQLite in WAL
  mode); no restart required.
- **Token usage page** — daily SVG bar chart + rollup tables for the
  last 30 days, last 7 days by agent × purpose, and the last 50 calls.

### Caveat: panel crash with MAAS still running

If the panel process itself dies while MAAS is still running, the panel
on next start sees `stopped` because state is in-memory. If you then
click Start, a second MAAS spawns and fights the first one for Telegram
long-polling. **If the panel says `stopped` but the bots are still
responding in Telegram, SSH in and `pkill -f project0.main` before
clicking Start again.** A PID-file reattach mechanism is a deferred
future improvement.

### No authentication

The panel has no login, no token, no CSRF. Tailscale is the gate. Do
not expose port 8090 outside your Tailnet.
```

- [ ] **Step 2: Verify the markdown renders**

Run: `uv run python -c "print(open('README.md').read())" | head -50`
Expected: no rendering errors (this is a plain readability sanity check).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: add control panel section to README

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 18: Full suite + type check + lint + smoke preparation

**Files:** none (verification task)

- [ ] **Step 1: Full pytest**

Run: `uv run pytest -q`
Expected: all tests green. No new failures in pre-existing suites; new `tests/control_panel/` suite all green; `tests/test_store.py` including the new classes green; `tests/test_store_wal.py` green; `tests/test_main_sigterm.py` green.

- [ ] **Step 2: Type check**

Run: `uv run mypy src/project0`
Expected: 0 errors.

- [ ] **Step 3: Lint**

Run: `uv run ruff check src tests`
Expected: 0 warnings.

- [ ] **Step 4: Manual panel smoke (local, not Telegram)**

Run the panel against the real project directory in one terminal:

```bash
uv run python -m project0.control_panel
```

In another terminal or browser:

```bash
curl -sS http://127.0.0.1:8090/ | head -20
curl -sS http://127.0.0.1:8090/usage | grep -c '<rect'
```

Expected: the first command shows the base HTML with the status bar; the second shows a number of rects matching the number of distinct days with usage data (may be 0 on a fresh checkout — that's fine, `no data` placeholder will render).

- [ ] **Step 5: Prepare the final smoke-test environment per spec §10.8**

Create a short smoke-prep script `scripts/smoke_control_panel.sh` (new, per the spec's discipline of automating smoke preparation):

```bash
#!/usr/bin/env bash
set -euo pipefail

# Prepares the environment for the control-panel final human smoke test
# described in docs/superpowers/specs/2026-04-16-control-panel-design.md §10.8.

echo "== store state =="
sqlite3 data/store.db "SELECT 'user_facts:' AS t, COUNT(*) FROM user_facts UNION ALL SELECT 'llm_usage:', COUNT(*) FROM llm_usage;"

echo
echo "== recent llm_usage (rollup by agent) =="
sqlite3 data/store.db "SELECT agent, purpose, COUNT(*), SUM(input_tokens+cache_creation_input_tokens+cache_read_input_tokens), SUM(output_tokens) FROM llm_usage GROUP BY agent, purpose ORDER BY 2;"

echo
echo "== active user_facts =="
sqlite3 data/store.db "SELECT id, ts, author_agent, fact_text, topic, is_active FROM user_facts WHERE is_active=1 ORDER BY id DESC;"

echo
echo "panel: uv run python -m project0.control_panel  (port 8090)"
echo "maas:  click Start in the panel"
```

```bash
chmod +x scripts/smoke_control_panel.sh
```

- [ ] **Step 6: Commit smoke script**

```bash
git add scripts/smoke_control_panel.sh
git commit -m "$(cat <<'EOF'
chore: add smoke_control_panel.sh for final human smoke prep

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Hand off to the user**

Announce the smoke test is ready and walk through the 12-step sequence in spec §10.8. Do not close the sub-project until the user reports success.

---

## Self-review notes

**Spec coverage check (§11 acceptance criteria):**

- A.1 pytest green — Task 18 step 1.
- A.2 mypy clean — Task 18 step 2.
- A.3 ruff clean — Task 18 step 3.
- B.1 package exists — Tasks 5, 6, 7, 8, 9, 16.
- B.2 entry point serves / on 8090 — Task 16 + Task 18 step 4.
- B.3 fake spawn_fn drives supervisor in tests — Task 6.
- C.1 profile routes — Task 10.
- C.2 TOML routes — Task 12.
- C.3 persona routes — Task 13.
- C.4 .env route — Task 14.
- C.5 atomic writes — Task 5 + used in every file-edit route.
- D.1-D.5 facts CRUD — Task 11 (routes) + Task 2 (store-layer permissions).
- D.6 grep assertion (INSERT/UPDATE/DELETE each in one place) — covered implicitly: each SQL verb appears exactly once inside `UserFactsWriter` after Task 2. If CI requires an explicit test, add one in Task 2 step 3.
- E.1-E.5 supervisor state machine — Task 6 (tests cover every transition) + Task 4 (SIGTERM handler).
- F.1 WAL — Task 1.
- F.2 synchronous=NORMAL — Task 1.
- G.1-G.5 /usage page — Task 15 + Task 8 (SVG renderer).
- H scope — no changes outside listed files; verified by reviewing the git log after Task 18.
- I smoke test — Task 18 step 5-7.

**Gaps filled:**

- The spec's §11 D.6 grep-based test is not turned into a formal task here, but each of `INSERT INTO user_facts`, `UPDATE user_facts`, `DELETE FROM user_facts` appears exactly once in `store.py` after Task 2's rewrite. If executing-plans chooses to add a grep test, it can live as a one-step addition to Task 2.

- The spec mentions `LLMUsageStore.summary_since` as "existing" — Task 3 adds the three new rollup methods without removing it.

**Placeholder scan:** no TBDs, no "add appropriate error handling," no "similar to Task N" — every task contains the actual code.

**Type/name consistency:** `MAASSupervisor`, `SpawnFn`, `_Proc`, `render_bar_chart_svg`, `atomic_write_text`, `toml_path`, `persona_path`, `ALLOWED_AGENT_NAMES` — names match across task definitions and usages.
