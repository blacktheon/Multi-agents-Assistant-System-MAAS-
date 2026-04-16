# WebUI Control Panel — Sub-Project Design

**Date:** 2026-04-16
**Parent project:** Project 0: Multi-agent assistant system
**Parent sub-project spec:** `docs/superpowers/specs/2026-04-16-memory-hardening-design.md`
**Sub-project scope:** Build a minimal, always-on web control panel that replaces the text editor for every human-tweakable setting in MAAS, supervises MAAS as a child process (start / stop / restart), and renders the first view of the `llm_usage` instrumentation shipped in memory hardening.

---

## 1. Purpose and Framing

MAAS today requires a terminal and a text editor for every human operation: editing `data/user_profile.yaml`, adjusting `prompts/*.toml`, tweaking persona markdown, changing `.env`, inspecting the SQLite audit log, and launching the process. The memory-hardening sub-project just added `llm_usage` rows on every LLM call, but there is no surface that reads them.

This sub-project delivers one surface that covers all of it. Its product statement:

> Open the panel in a browser. Start MAAS. Tweak anything you'd normally open a text editor for. See where tokens are being spent. Stop MAAS when you're done.

### Design stance

- **The panel is the front door.** The user opens the panel first, then clicks Start. MAAS runs as a child process of the panel. When MAAS is stopped, the panel is still up.
- **The panel is a single-user tool behind Tailscale.** Same trust posture as the Intelligence webapp. No authentication, no authorization, no CSRF tokens, no rate limiting. Tailscale is the gate.
- **Keep edit surfaces dumb.** Every editable file or row is a plain `<textarea>` and a Save button. No schema-aware forms, no client-side validation, no WYSIWYG. If the user saves broken YAML or `.env`, the next MAAS start fails loudly. That is acceptable and preferred over building validators for every format.
- **Automate everything testable.** One prepared human smoke test at the end, not iterative manual checks during development.

### What this sub-project is not

- Not a process supervisor replacement. If the panel process itself dies, `systemctl` / SSH is still how you bring it back.
- Not an envelope trace viewer. The `messages` table is out of scope; it belongs to a future Supervisor-agent or observability sub-project.
- Not a persona pruning pass. Editing persona markdown is exposed as a textarea, but no voice changes happen in this sub-project.
- Not a hot-reload system. Edits to YAML / TOML / MD / `.env` take effect only after MAAS restart, clicked from the panel. `user_facts` edits are live because SQLite is shared.

---

## 2. Architecture

**Two processes, one disk.**

```
┌─────────────────────────────┐          ┌──────────────────────────────┐
│ control_panel (always on)   │          │ MAAS (child, start/stop)     │
│  FastAPI + Jinja2           │  spawns  │  main.py: bots + webapp      │
│  port 8090                  │ ───────▶ │  + pulses + orchestrator     │
└──────────────┬──────────────┘          └──────────────┬───────────────┘
               │                                        │
               │  writes                                │  reads at start
               ▼                                        ▼
    data/user_profile.yaml, prompts/*.{toml,md}, .env
               │                                        │
               │  CRUD via SQLite (WAL)                 │  live reads/writes
               └──────────────┬─────────────────────────┘
                              ▼
                         data/store.db
                   (user_facts, llm_usage, messages)
```

- **New package:** `src/project0/control_panel/`, sibling to `src/project0/intelligence_web/`. Own FastAPI app, own Jinja2 environment, own templates, own static files. Minor duplication of Jinja2 setup between the two apps is accepted — their audiences, risk profiles, and evolution paths differ.
- **Entry point:** `uv run python -m project0.control_panel`. Binds `0.0.0.0:8090`. Same Tailscale-gated deployment convention as the Intelligence webapp.
- **MAAS lifecycle:** the panel holds one `asyncio.subprocess.Process` handle at a time. MAAS is spawned as `asyncio.create_subprocess_exec("uv", "run", "python", "-m", "project0.main", stdout=DEVNULL, stderr=DEVNULL)`. Stdout and stderr are discarded; server-side debugging uses the terminal that launched the panel (the panel surface has no log view).
- **Shared state is the filesystem.** No sockets, no RPC, no IPC. Panel writes files; MAAS reads them on startup. Panel writes `user_facts` rows; MAAS reads them live via the shared SQLite file. SQLite WAL mode makes concurrent access safe.
- **Panel state is in-memory and non-durable.** Supervisor state (`stopped` / `starting` / `running` / `stopping` / `crashed`) is held in a single object on the panel process. Panel restart = supervisor forgets about any running MAAS. See §4.5 for the degenerate-case mitigation.
- **No authentication.** Tailscale is the only gate. Same as `intelligence_web`.

---

## 3. Scope

### 3.1 In scope

**Editable surfaces (textarea + Save):**

1. `data/user_profile.yaml` — Layer A, plain text edit. Restart required.
2. `prompts/manager.toml`, `prompts/secretary.toml`, `prompts/intelligence.toml` — plain text edit. Restart required.
3. `prompts/manager.md`, `prompts/secretary.md`, `prompts/intelligence.md` — plain text edit. Restart required.
4. `.env` — plain text edit. Secrets rendered verbatim in the browser over Tailscale. Restart required.

**Live surface (SQLite CRUD):**

5. `user_facts` — Layer D. Full CRUD: add, edit, soft-deactivate, reactivate, hard-delete. Writes are performed by extending `UserFactsWriter` to accept `"human"` as an authorized author_agent (decision A1 in §5.2). Hard delete is the escape hatch for mistakes, available on every row behind a confirm step (decision B2 in §5.2).

**Observability:**

6. Token usage view from `llm_usage`:
   - Daily rollup chart (inline SVG bar chart, last 30 days).
   - Daily rollup table (last 30 days).
   - Agent × purpose rollup table (last 7 days).
   - Recent calls table (last 50 rows).

**Supervisor:**

7. Start / Stop / Restart buttons + current status indicator, shown in the layout header on every page.

### 3.2 Canceled (not deferred — removed from scope permanently)

- **Layer C `agent_memory` editing.** User explicitly canceled. It is runtime scratch state, not human-authored content, and exposing it as an edit surface adds surface area without product value.

### 3.3 Deferred (may land in a later sub-project)

- Envelope trace viewer over the `messages` table.
- Dollar-cost math over `llm_usage`. Tokens are the durable record; pricing changes too frequently to maintain in v1.
- Healthcheck / readiness probes against MAAS.
- Automatic restart of MAAS on crash.
- Multi-user access controls.
- Log pane / stdout tailing. Debug is a server-side developer activity; the end user should not see logs.
- Filtering and date-picking on the usage view. Three fixed windows (30d / 7d / 50 calls) are the views.
- Hot reload of YAML / TOML / MD / `.env` without MAAS restart.

---

## 4. Pages and Routes

**Layout header, every page:**

```
[MAAS Control]  Status: ● running (PID 12345)  [Start] [Stop] [Restart]
─────────────────────────────────────────────────────────────────────
Home | Profile | Facts | TOML | Personas | .env | Token Usage
```

Status indicator and action buttons live in the shared layout template. Action buttons POST and redirect back to the referring page. Buttons are disabled based on state (Start disabled when running; Stop disabled when stopped or crashed).

**Route map:**

| Route | Method | Purpose |
|---|---|---|
| `GET /` | | Home: status, last-24h token totals, quick links to every page |
| `POST /maas/start` | | Spawn MAAS subprocess |
| `POST /maas/stop` | | SIGTERM + wait, SIGKILL on 10s timeout |
| `POST /maas/restart` | | Stop then start, sequenced under the supervisor lock |
| `GET /profile` | | Render `data/user_profile.yaml` in a textarea |
| `POST /profile` | | Overwrite file with posted body |
| `GET /facts` | | List active facts + add form; toggle to show inactive |
| `POST /facts` | | INSERT new fact, `author_agent="human"` |
| `POST /facts/{id}/edit` | | UPDATE `fact_text` / `topic` on an existing row |
| `POST /facts/{id}/deactivate` | | Set `is_active = 0` |
| `POST /facts/{id}/reactivate` | | Set `is_active = 1` |
| `POST /facts/{id}/delete` | | Hard DELETE, behind confirm |
| `GET /toml` | | List `prompts/*.toml`, link each to its edit page |
| `GET /toml/{name}` | | Textarea for one TOML file |
| `POST /toml/{name}` | | Overwrite |
| `GET /personas` | | List `prompts/*.md` |
| `GET /personas/{name}` | | Textarea for one persona file |
| `POST /personas/{name}` | | Overwrite |
| `GET /env` | | Textarea for `.env` |
| `POST /env` | | Overwrite |
| `GET /usage` | | Token usage views (chart + three tables) |

**File resolution for TOML and persona pages:**

- `{name}` is restricted to a hardcoded allowlist of base names: `{manager, secretary, intelligence}`. Any other value → 404. This is the only "validation" in the panel, and it exists to prevent `..` path traversal.

**Error rendering:**

- Missing file on GET → empty textarea. Not a 500. Fresh checkouts should load cleanly.
- Save failures (e.g., disk full, permission denied) → flash message with the OS error. The panel does not pre-check writability.
- Routes never return JSON. Every response is an HTML page or a redirect.

---

## 5. Layer D Editing — Trust Boundary Extension

The memory-hardening spec locked `UserFactsWriter` to `secretary` only at construction time, with the comment "only 'secretary' may write user facts in this sub-project." This sub-project extends that boundary to include the human operating the control panel.

### 5.1 Extension mechanism

`UserFactsWriter.__init__` is changed from:

```python
if agent_name != "secretary":
    raise PermissionError(...)
```

to:

```python
_AUTHORIZED_AUTHORS = frozenset({"secretary", "human"})

if agent_name not in _AUTHORIZED_AUTHORS:
    raise PermissionError(
        f"user_facts writer not allowed for agent={agent_name!r}; "
        f"authorized authors: {sorted(_AUTHORIZED_AUTHORS)}"
    )
```

The panel constructs `UserFactsWriter("human")`. The `author_agent` column retains the identity of every write, so "who wrote this fact" remains answerable: `secretary` for agent-written facts, `human` for panel-written facts. Other agents still fail at construction.

### 5.2 Decisions

**A1 — Extend `UserFactsWriter`, do not bypass it.** The memory spec's C.5 assertion ("`INSERT INTO user_facts` appears in exactly one place in `store.py`") is preserved. The panel does not introduce a parallel write path.

**B2 — Both soft deactivate and hard delete are exposed.** Deactivate is the normal button on every active row; hard delete is a separate button behind a confirmation step. Rationale: the memory spec's soft-delete-only stance was motivated by future Learning-agent consolidation needs, but the human operating the panel needs a real escape hatch when something is saved wrong or experimental. `UserFactsWriter` gains a `delete(fact_id)` method that issues `DELETE FROM user_facts WHERE id = ?`. It is exposed only to the `"human"` author — a `PermissionError` guards it for `"secretary"`. Rationale: Secretary has no UX for "I regret writing that permanently"; her agent-side affordance is soft deactivate, and that boundary should be preserved.

### 5.3 Editing existing fact text

The panel exposes an "edit" action per row that calls a new `UserFactsWriter.edit(fact_id, fact_text, topic)` method. This issues `UPDATE user_facts SET fact_text = ?, topic = ? WHERE id = ?`. `author_agent` and `ts` are **not** rewritten on edit — the row remains attributable to its original writer and original timestamp. A future schema extension could add an `edited_by_human_at` column; it is explicitly deferred.

Edit is also `"human"`-only, enforced by `PermissionError` at method entry on a `"secretary"`-constructed writer. Secretary has no product reason to edit an existing row; if she wants to correct a fact, she deactivates and re-adds.

### 5.4 Test matrix additions

The memory spec's trust-boundary tests (§C.1-C.5) are extended with:

- `UserFactsWriter("human")` constructs successfully.
- `UserFactsWriter("supervisor")` still raises.
- `UserFactsWriter("secretary").delete(1)` raises `PermissionError`.
- `UserFactsWriter("secretary").edit(1, "x", None)` raises `PermissionError`.
- `UserFactsWriter("human").add(...)` writes a row with `author_agent="human"`.
- `UserFactsWriter("human").delete(1)` removes the row.
- The existing C.5 grep test is updated: `INSERT INTO user_facts`, `DELETE FROM user_facts`, and `UPDATE user_facts` each appear in exactly one place in `store.py`.

---

## 6. Token Usage View

One page (`GET /usage`). Server-rendered, no JavaScript framework, no CDN, no charting library.

### 6.1 Daily rollup chart — inline SVG

An inline `<svg>` bar chart rendered from the daily rollup rows. Properties:

- One bar per day for the last 30 days, left-to-right oldest → newest.
- Bar height proportional to total tokens for that day (input + cache_creation_input + cache_read_input + output). One metric, not stacked — shape is the goal; precise numbers come from the table below.
- Single flat fill color. Weekends rendered in a slightly lighter shade so weekly rhythm is visible.
- Max-value label top-left. No gridlines, no ticks, no axis labels.
- Native SVG `<title>` child inside each `<rect>` for hover tooltip: `YYYY-MM-DD: N,NNN tokens`. Zero JS.
- Fixed SVG size: `width=800 height=150`. CSS `max-width: 100%` for responsiveness.
- Implementation: Jinja macro `bar_chart(rows, value_key)` lives in `control_panel/rendering.py`. Emits `<svg>` with one `<rect>` per row. Approximately 40 lines of Python plus template.

### 6.2 Daily rollup table (last 30 days)

```sql
SELECT
  substr(ts, 1, 10)                 AS day,
  SUM(input_tokens)                 AS in_tok,
  SUM(cache_creation_input_tokens)  AS cc_tok,
  SUM(cache_read_input_tokens)      AS cr_tok,
  SUM(output_tokens)                AS out_tok,
  COUNT(*)                          AS calls
FROM llm_usage
WHERE ts >= date('now', '-30 days')
GROUP BY day
ORDER BY day DESC;
```

Columns: `Day | Calls | Input | Cache-create | Cache-read | Output`. The cache-read column is the proof-of-life for the memory-hardening spec's cache discipline; if it is near zero when the panel first goes live, something regressed.

### 6.3 Agent × purpose rollup (last 7 days)

```sql
SELECT
  agent,
  purpose,
  SUM(input_tokens + cache_creation_input_tokens + cache_read_input_tokens) AS in_total,
  SUM(output_tokens) AS out_total,
  COUNT(*)           AS calls
FROM llm_usage
WHERE ts >= datetime('now', '-7 days')
GROUP BY agent, purpose
ORDER BY in_total DESC;
```

Columns: `Agent | Purpose | Calls | Input (all) | Output`. Daily report generation appears as `intelligence_summarizer / report_gen` on its own row, answering "how much did daily reports cost this week?" directly.

### 6.4 Recent calls (last 50 rows)

```sql
SELECT
  id, ts, agent, purpose, model,
  input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens,
  envelope_id
FROM llm_usage
ORDER BY id DESC
LIMIT 50;
```

Displayed as-is, one row per call. `envelope_id` is rendered as the integer or `—`. No join to `messages` in this sub-project.

### 6.5 LLMUsageStore API additions

`LLMUsageStore` gains three read methods used only by the panel:

```python
class LLMUsageStore:
    def daily_rollup(self, days: int) -> list[dict]: ...
    def agent_rollup(self, days: int) -> list[dict]: ...
    def recent(self, limit: int) -> list[dict]: ...
```

`store.py` remains the trust boundary even for read-only consumers. The panel never writes raw SQL.

### 6.6 Filtering, pricing, refresh

- No filters, no date pickers, no agent dropdowns in v1.
- No dollar-cost column. Tokens are the durable record; pricing per model changes too often to hand-maintain.
- No auto-refresh, no SSE. Plain page reload.

---

## 7. Supervisor Lifecycle

### 7.1 State machine

Five states held in memory on the panel process inside a single `MAASSupervisor` object:

```
                  ┌──────────┐
    click Start → │ starting │ → spawn ok → ┌─────────┐
                  └──────────┘              │ running │
                        ↑                   └────┬────┘
                        │                        │
           click Start  │      click Stop ───────┤
                        │                        ↓
                  ┌─────────┐              ┌──────────┐
                  │ stopped │ ← exit 0 ── │ stopping │
                  └─────────┘              └──────────┘
                        ↑                        │
                        │              SIGKILL after 10s
                        │                        ↓
                        │                  ┌─────────┐
                        └── click Start ── │ crashed │ ← unexpected exit
                                           └─────────┘
```

- **stopped** — no child process. Start enabled.
- **starting** — `asyncio.create_subprocess_exec(...)` awaited; transitions to `running` as soon as the coroutine returns. No readiness probe.
- **running** — PID displayed on every page. Stop / Restart enabled.
- **stopping** — SIGTERM sent, awaiting `process.wait()` with 10s timeout. On timeout: SIGKILL, log once, transition to `stopped`.
- **crashed** — watcher task observed `process.wait()` returning without a Stop having been requested. Last exit code retained for display. Start is enabled from this state.

### 7.2 Supervisor object shape

```python
class MAASSupervisor:
    def __init__(self, spawn_fn: SpawnFn = _real_spawn) -> None: ...
    @property
    def state(self) -> Literal["stopped","starting","running","stopping","crashed"]: ...
    @property
    def pid(self) -> int | None: ...
    @property
    def last_exit_code(self) -> int | None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def restart(self) -> None: ...
```

A single `asyncio.Lock` guards every state transition. Double-clicks on Restart do not double-spawn.

### 7.3 Watcher task

On spawn, the supervisor creates a background task:

```python
async def _watch(self, proc: asyncio.subprocess.Process) -> None:
    rc = await proc.wait()
    if self._state == "stopping":
        self._state = "stopped"
    else:
        self._exit_code = rc
        self._state = "crashed"
```

One task per child. The prior watcher is cancelled on a subsequent Start.

### 7.4 Stop flow

```python
async def stop(self) -> None:
    async with self._lock:
        if self._state != "running":
            return
        self._state = "stopping"
        self._proc.terminate()                              # SIGTERM
        try:
            await asyncio.wait_for(self._proc.wait(), 10)
        except asyncio.TimeoutError:
            self._proc.kill()                               # SIGKILL
            await self._proc.wait()
        # watcher task finalizes state transition
```

`main.py` is verified during implementation to exit cleanly on SIGTERM. If the current `asyncio.run(main())` scaffolding does not already install a SIGTERM handler, a minimal addition (`loop.add_signal_handler(signal.SIGTERM, ...)` or equivalent) is included as part of this sub-project's MAAS-side changes. No other MAAS-side logic is touched.

### 7.5 Panel crash recovery — degenerate case

If the panel process dies while MAAS is running, the panel has no way to reclaim the orphan on next start (state is in-memory). If the user then clicks Start again, the second MAAS instance fights the first for Telegram long-polling and Telegram returns HTTP 409 Conflict to the second poller.

**Decision:** accept the degenerate case in v1. Mitigation is a one-line entry in the README: "if the panel says `stopped` but Telegram is still responding to bots, SSH in and `pkill -f project0.main` before clicking Start again." A PID-file reattach mechanism is explicitly deferred — it would introduce a second process-control code path (PID-based signaling) alongside the normal `asyncio.subprocess` one, doubling the testing surface for a rare failure mode.

### 7.6 SQLite WAL

`store.py` gains two pragmas on connection open:

```python
conn = sqlite3.connect(path)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
```

WAL enables concurrent readers and a single writer without `database is locked` errors. `synchronous=NORMAL` is the standard WAL pairing. Both processes open their own `sqlite3.Connection`. No shared handle, no cross-process coordination code. The filesystem is the coordinator.

`.gitignore` already covers `data/`, which includes the `store.db-wal` and `store.db-shm` sidecar files SQLite creates in WAL mode.

### 7.7 What is explicitly not built

- No healthcheck ping against MAAS.
- No automatic restart on crash.
- No rolling log to disk.
- No multi-instance MAAS.
- No PID-file reattach.

---

## 8. Storage APIs Summary

Additions to `src/project0/store.py`:

```python
class UserFactsWriter:
    # existing:
    def __init__(self, agent_name: str) -> None: ...
    def add(self, fact_text: str, topic: str | None = None) -> int: ...
    def deactivate(self, fact_id: int) -> None: ...
    # new:
    def reactivate(self, fact_id: int) -> None: ...
    def edit(self, fact_id: int, fact_text: str, topic: str | None) -> None: ...
    def delete(self, fact_id: int) -> None: ...

class UserFactsReader:
    # existing:
    def active(self, limit: int = 30) -> list[UserFact]: ...
    def as_prompt_block(self, max_tokens: int = 600) -> str: ...
    # new:
    def all_including_inactive(self, limit: int = 200) -> list[UserFact]: ...

class LLMUsageStore:
    # existing:
    def record(self, **kwargs) -> int: ...
    def summary_since(self, ts: str) -> list[dict]: ...
    # new:
    def daily_rollup(self, days: int) -> list[dict]: ...
    def agent_rollup(self, days: int) -> list[dict]: ...
    def recent(self, limit: int) -> list[dict]: ...
```

Permission guards on `UserFactsWriter`:

- `add`, `deactivate`, `reactivate` — allowed for `"secretary"` and `"human"`.
- `edit`, `delete` — allowed for `"human"` only. Secretary calling either raises `PermissionError`.

`all_including_inactive` on `UserFactsReader` is ungated (read-only); the panel uses it to render the "show inactive" view.

No new SQLite tables. The schema remains the one shipped in memory hardening.

---

## 9. File Layout

```
src/project0/
├── control_panel/                    # NEW sibling to intelligence_web
│   ├── __init__.py
│   ├── __main__.py                   # entry point: uv run python -m project0.control_panel
│   ├── app.py                        # FastAPI factory + supervisor wiring
│   ├── routes.py                     # all HTTP routes
│   ├── supervisor.py                 # MAASSupervisor state machine
│   ├── paths.py                      # allowlist + resolution for toml/persona file names
│   ├── rendering.py                  # Jinja2 env + SVG bar chart macro
│   ├── templates/
│   │   ├── base.html                 # layout with status header and nav
│   │   ├── home.html
│   │   ├── profile.html
│   │   ├── facts.html
│   │   ├── toml_list.html
│   │   ├── toml_edit.html
│   │   ├── personas_list.html
│   │   ├── personas_edit.html
│   │   ├── env.html
│   │   └── usage.html
│   └── static/
│       └── style.css                 # minimal; shares no files with intelligence_web
```

Modifications:

- `src/project0/store.py` — new methods listed in §8; WAL pragmas on connection open.
- `src/project0/main.py` — SIGTERM handler if not already present (verified during implementation).
- `pyproject.toml` — script entry if convenient (`project0-panel = "project0.control_panel.__main__:main"`), optional.
- `README.md` — new section documenting the panel's entry point, Tailscale binding, and the panel-crash-recovery caveat.
- `.gitignore` — no change required; `data/` already covers WAL sidecar files.

---

## 10. Testing

Per project convention: automate everything mechanically testable, one prepared human smoke at the end.

### 10.1 Route tests (FastAPI TestClient)

One module per route group. Each test uses a tmp directory for file edits and a tmp SQLite path.

- **`test_profile_routes.py`** — GET renders file; POST overwrites; missing file → empty textarea.
- **`test_facts_routes.py`** — full CRUD path end-to-end, including deactivate, reactivate, edit, and hard delete. Asserts `author_agent="human"` on panel-added rows.
- **`test_toml_routes.py`** — GET lists exactly three files from a fixture `prompts/` dir; edit page renders file content; POST overwrites; unknown name → 404.
- **`test_personas_routes.py`** — mirror of TOML.
- **`test_env_route.py`** — GET renders `.env` verbatim (explicit assertion that secret values are not masked); POST overwrites.
- **`test_usage_routes.py`** — seed `llm_usage` with fixed rows; assert rollup numbers; assert SVG contains expected number of `<rect>` elements; assert recent-calls table contains expected row IDs.

### 10.2 Supervisor tests

The supervisor accepts a `spawn_fn` dependency (default: real `asyncio.create_subprocess_exec`). Tests inject a fake `spawn_fn` returning a stub process object with async `wait`, `terminate`, `kill`. No test spawns a real `python -m project0.main`.

- Start from `stopped` → `running`; PID is set.
- Stop from `running` → `stopping` → `stopped`; watcher task records the transition.
- Stop timeout path: fake `wait()` blocks longer than the timeout; `terminate` + `kill` both called; final state `stopped`.
- Unexpected child exit → `crashed`; `last_exit_code` populated.
- Concurrent `start()` calls: second call sees the lock, state remains `running`, no double-spawn.
- Restart: `stop` then `start`, single state transition sequence observed.

### 10.3 Integration — supervisor through routes

One test wires a real FastAPI app with a fake `spawn_fn`, POSTs `/maas/start`, GETs `/`, asserts status = `running` with the expected PID. POSTs `/maas/stop`, GETs `/`, asserts status = `stopped`. Proves the routes and the state machine are plumbed together without touching real processes.

### 10.4 WAL test

One hermetic test in `tests/test_store_wal.py`: open a connection via `store.py`'s constructor, assert `PRAGMA journal_mode` returns `wal`. SQLite's WAL implementation itself is trusted — no multi-process contention test.

### 10.5 SVG chart test

Seed `llm_usage` with 30 days of known rows; render `/usage`; assert:

- Exactly 30 `<rect>` elements in the daily chart SVG.
- The tallest rect's `height` matches the row with the maximum total (within rounding).
- `<title>` children contain the expected `YYYY-MM-DD: N tokens` strings for every day.

### 10.6 Permission-boundary tests (Layer D)

Extend the memory-hardening sub-project's trust-boundary suite:

- `UserFactsWriter("human")` constructs.
- `UserFactsWriter("supervisor")` raises.
- `UserFactsWriter("secretary").delete(1)` raises.
- `UserFactsWriter("secretary").edit(1, "x", None)` raises.
- `UserFactsWriter("human").add(...)` writes with `author_agent="human"`.
- `UserFactsWriter("human").delete(1)` removes the row.
- Grep test: `INSERT INTO user_facts`, `UPDATE user_facts`, `DELETE FROM user_facts` each appear exactly once in `store.py`.

### 10.7 What is explicitly not tested

- Browser rendering / CSS (no Playwright, no Selenium).
- Real subprocess launch (the `spawn_fn` seam avoids it).
- Tailscale networking (deployment concern).
- Performance / load (single user, single process).

### 10.8 Final human smoke test

Prepared by the implementer. Run once after all automated tests are green. Target duration: 10 minutes. The user never debugs.

1. `uv run python -m project0.control_panel` — panel starts, user opens `http://localhost:8090`.
2. Status shows `stopped`. Click **Start**. Status flips to `running` with a PID.
3. Navigate to **Profile** — existing YAML renders in the textarea. Edit `out_of_band_notes`. Save. Flash message confirms.
4. Click **Restart**. Status cycles `stopping → stopped → starting → running`.
5. DM Secretary in Telegram: `我最近在做什么？` — Secretary's reply references the edited note. *Proves YAML edits reach MAAS through restart.*
6. Navigate to **Facts** — existing facts render. Add a new fact via the form. Edit one. Deactivate one. Reactivate it. Hard-delete a throwaway test fact.
7. DM Manager: `我最喜欢吃什么？` — answer reflects a panel-added fact without MAAS restart. *Proves live SQLite sharing works.*
8. Navigate to **TOML** → `manager.toml`. Edit `transcript_window` from 10 to 5. Save. Restart. Send Manager a calendar question; no regression.
9. Navigate to **Personas** → `secretary.md`. Edit one line. Save. Restart. DM Secretary; verify the edit took effect in her voice.
10. Navigate to **.env**. Confirm values render verbatim (including `ANTHROPIC_API_KEY`). Change `ANTHROPIC_CACHE_TTL` from `ephemeral` to `1h`. Save. Restart. MAAS comes up cleanly.
11. Navigate to **Token Usage**. Verify the SVG bar chart shows a shape. Verify the three tables contain data from the smoke session. Verify the `cache_read` column is nonzero (cache discipline proof).
12. Click **Stop**. Status flips to `stopped`. DM Secretary from Telegram — no reply (MAAS is really down).

Any step failing is an implementer fix, not a user debugging session.

---

## 11. Acceptance Criteria

The sub-project is done when **all of the following hold at once**. No partial credit.

### A. Tests and checks

- **A.1.** `uv run pytest` green.
- **A.2.** `uv run mypy src/project0` zero errors.
- **A.3.** `uv run ruff check src tests` zero warnings.

### B. Package and entry point

- **B.1.** `src/project0/control_panel/` exists with the files listed in §9.
- **B.2.** `uv run python -m project0.control_panel` starts the panel, binds `0.0.0.0:8090`, and serves `GET /` with HTTP 200.
- **B.3.** The panel can start, observe, and stop a faked MAAS subprocess through the `spawn_fn` seam in automated tests.

### C. Editable surfaces

- **C.1.** `GET /profile` renders `data/user_profile.yaml`. `POST /profile` overwrites the file. Missing file is not a 500.
- **C.2.** `GET /toml/{name}` renders the matching `prompts/{name}.toml` for `name ∈ {manager, secretary, intelligence}`. Unknown name → 404.
- **C.3.** `GET /personas/{name}` renders the matching `prompts/{name}.md`. Unknown name → 404.
- **C.4.** `GET /env` renders `.env` verbatim, including secret values. `POST /env` overwrites.
- **C.5.** `POST` on all edit routes writes the file atomically (write-to-tmp + rename) so a panel crash mid-save cannot corrupt the target file.

### D. Layer D CRUD

- **D.1.** `GET /facts` lists active facts sorted newest-first. A toggle shows inactive facts.
- **D.2.** `POST /facts` with `fact_text` and optional `topic` creates a row with `author_agent="human"`.
- **D.3.** `POST /facts/{id}/edit` updates `fact_text` / `topic`. `ts` and `author_agent` are unchanged. `UserFactsWriter("secretary").edit(...)` raises `PermissionError`.
- **D.4.** `POST /facts/{id}/deactivate` and `POST /facts/{id}/reactivate` flip `is_active`.
- **D.5.** `POST /facts/{id}/delete` issues a hard DELETE. `UserFactsWriter("secretary").delete(...)` raises `PermissionError`.
- **D.6.** Grep assertion: `INSERT INTO user_facts`, `UPDATE user_facts`, `DELETE FROM user_facts` each appear exactly once in `store.py`.

### E. Supervisor

- **E.1.** State machine transitions match §7.1 in automated tests against the fake `spawn_fn`.
- **E.2.** `POST /maas/start`, `/stop`, `/restart` drive the state machine through the supervisor lock; double-POST does not double-spawn.
- **E.3.** Unexpected child exit transitions state to `crashed` and records `last_exit_code`.
- **E.4.** Stop timeout path triggers SIGKILL after 10s and reaches `stopped`.
- **E.5.** `main.py` exits cleanly on SIGTERM (verified; a handler is added if absent).

### F. SQLite WAL

- **F.1.** `PRAGMA journal_mode` returns `wal` after opening a connection via `store.py`.
- **F.2.** `PRAGMA synchronous` returns `1` (NORMAL).

### G. Token usage page

- **G.1.** `GET /usage` renders the SVG daily chart with exactly 30 `<rect>` elements when seeded with 30 days of data.
- **G.2.** The daily rollup table matches the `daily_rollup(30)` output row-for-row.
- **G.3.** The agent rollup table matches `agent_rollup(7)`.
- **G.4.** The recent-calls table contains exactly 50 rows when `llm_usage` has ≥50 rows.
- **G.5.** SVG `<title>` children contain the expected `YYYY-MM-DD: N tokens` strings.

### H. Scope discipline

- **H.1.** `git diff --stat origin/main` for this sub-project shows changes only in:
  - `src/project0/control_panel/` (new package)
  - `src/project0/store.py` (new methods, WAL pragma)
  - `src/project0/main.py` (SIGTERM handler if needed)
  - `tests/control_panel/` (new), `tests/test_store*.py` (amended)
  - `docs/superpowers/specs/2026-04-16-control-panel-design.md` (this file)
  - `docs/superpowers/plans/2026-04-16-control-panel.md` (next phase)
  - `README.md` (new section)
  - `pyproject.toml` (optional script entry)
- **H.2.** **No changes to `agent_memory`, `blackboard`, `chat_focus`, `messages`, `llm_usage` schema.**
- **H.3.** **No changes to the `intelligence_web/` package.** The two webapps are siblings; work on one does not touch the other.
- **H.4.** **No persona voice edits are committed in this sub-project.** The persona edit surface is exposed; actual persona changes belong to the deferred persona-pruning sub-project.

### I. Final smoke test

The 12-step smoke session in §10.8 passes end-to-end, including both a YAML edit roundtrip through restart and a Layer D edit that is visible to a running MAAS without restart.

If any of A–I fails, the sub-project is not done. No "close enough."

---

## 12. Design Decisions Worth Flagging for Future Sub-Projects

1. **Panel is the front door.** Future sub-projects that add a long-running surface (webapp, scheduler, another bot) should assume the panel supervises them, or explicitly opt out. The supervisor is single-child in v1; if a second managed process is needed, the state machine generalizes to a registry.
2. **`UserFactsWriter`'s authorized-author set is the extension point** for future writers (Learning agent, OpenClaw, etc.). Do not add parallel write paths; extend the allowlist.
3. **`edit` and `delete` on `UserFactsWriter` are `"human"`-only.** Secretary still has only soft-delete. Learning agent, when it arrives, decides whether it needs hard delete or stays on soft-delete + consolidation.
4. **SQLite WAL is now assumed by the panel.** Any future sub-project that reopens `store.py` must preserve the WAL pragma. A test locks this.
5. **Two siblings, not one merged webapp.** `intelligence_web` stays read-only and reader-audience; `control_panel` stays operator-audience. A future "unified webapp" merge is possible but not planned; if it happens, it's its own sub-project.
6. **No log view.** Debug is a server-side developer activity; the end-user surface stays clean. If a log view is ever added, it should be a separate page behind a clear "developer" link, not promoted to the main nav.
7. **No authentication.** Tailscale is the gate. If the panel ever needs to run outside Tailscale, a real auth story is required — and it should not be tacked on; it should be its own sub-project.
8. **In-memory supervisor state + panel-crash recovery gap** is accepted technical debt. PID-file reattach is the preferred future fix if the gap becomes real.
9. **No dollar-cost math in v1.** When pricing display is added, it goes into `LLMUsageStore`-adjacent code as a pure computation over token columns, not as a new table.

---

## 13. Roadmap Position

Per the master spec sequence, this is sub-project 3 — the WebUI control panel. After this, the next sensible moves are:

1. **This sub-project** — control panel (current).
2. **Learning agent** — full Layer D (consolidation, review cards, formal KB writes). Takes over `user_facts` writes from Secretary, or coexists; hard delete / edit stay `"human"`-only.
3. **Supervisor agent** — audit / evaluation authority; first real consumer of the deferred envelope-trace viewer.
4. **Envelope trace viewer** — additional page on the control panel reading `messages` with parent_id trees. Can ship as part of the Supervisor-agent sub-project or as its own small UI sub-project.
5. **Local LLM migration** — unrelated to the panel but reuses `llm_usage` telemetry for the cost comparison.
6. **Persona pruning** — uses the panel's persona edit surface for the first time with real voice changes.
7. **`.env` structured editor / secret masking** — if and when the panel moves beyond a single trusted user or outside Tailscale.

Each of the above is its own brainstorm → spec → plan → implementation cycle.
