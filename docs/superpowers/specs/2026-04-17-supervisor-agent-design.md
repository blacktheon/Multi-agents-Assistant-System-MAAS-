# Sub-project — Supervisor Agent (叶霏: pulse-scheduled reviewer + scoring surface)

**Date:** 2026-04-17
**Status:** Design approved, ready for implementation plan
**Depends on:** Multi-agent skeleton, shared tool-loop pattern, pulse primitive, messages store, control-panel app

---

## 1. Context and scope

The Supervisor Agent (叶霏) is the fifth and final agent in the v1.0 multi-agent system. She is the audit and evaluation authority defined in §3.5 of `Project 0: Multi-agent assistant system.md`: she reads the stored conversation history of the other agents, scores them on a fixed rubric, writes short critiques and structured recommendations, and makes all of it visible through a new read-only page in the control panel.

**Primary role:** Pulse-scheduled reviewer of Manager, Intelligence, and Learning. Conversational follow-up surface over DM for testing, pointed critiques ("review this Manager statement, I don't think it's accurate"), and discussing past results.

**Persona.** 叶霏 is an energetic, cheerful, playful, warm-hearted undergraduate girl. She calls the user "欧尼酱", loves him openly, doesn't mind sharing him with the other (agent-)girls, and has a secretly sharp and impartial judgment when she puts the evaluator hat on. The name deliberately does not evoke an audit or supervisor role.

### Core use cases (in order of frequency)

1. **Pulse review** (every 3h, ~90% of work) — idle-gated background job that reviews each non-Secretary agent's new envelopes since the last run, writes one scoring row per agent per tick.
2. **DM follow-up** (on demand, testing today + conversational tomorrow) — user DMs 叶霏, she replies in persona, can explain past reviews and (future) run targeted critiques.
3. **Read-only inspection via control panel** — the user opens `/reviews` to see scores, trends, and recommendations.

### In scope (v1.0)

- `Supervisor` agent class with pulse-mode and DM-mode tool loops
- Persona file (`prompts/supervisor.md`, Chinese, four canonical sections — no group-addressed mode)
- Config file (`prompts/supervisor.toml`, model/token/idle settings + two `[[pulse]]` entries)
- `register_supervisor` in registry; composition wiring in `main.py`
- Own Telegram bot token (`TELEGRAM_BOT_TOKEN_SUPERVISOR` in `AGENT_SPECS`)
- New SQLite table `supervisor_reviews` + `SupervisorReviewsStore` in `store.py`
- New method `MessagesStore.envelopes_for_review(agent, after_id, limit)` — restricted to the three reviewed agents
- Cross-cutting change: `MessagesStore.recent_for_chat` gains a required `visible_to: str` parameter that filters Secretary envelopes for every non-Secretary caller
- Per-agent cursor tracking in Supervisor's private `agent_memory`
- Idle-gate with retry pulse
- New `/reviews` page in the control panel: time-series SVG chart + three agent cards + per-agent history sections
- `render_score_timeseries_svg` and `render_sparkline_svg` helpers in `control_panel/rendering.py`
- Full unit/component test coverage
- One prepared human smoke test at the end

### Out of scope (explicitly deferred)

- Auto-applying recommendations — reviews are advisory only, matching the design doc's "recommend changes, not silently self-modify"
- Cross-agent comparison rollups beyond the shared chart
- A "run review now" button in the control panel — on-demand runs go through DM only
- Supervisor participating in group chats (no group presence, no listener role)
- Tunable rubric weights — weights are constants in code for v1.0; tuning is a v1.1 concern
- Pointed "review this specific message" tool in DM — design supports the future shape, but v1.0 DMs only explain past reviews and (for testing) trigger a whole-agent on-demand review
- Time-series alerting or thresholds

---

## 2. Architecture overview

叶霏 is a full fifth agent, wired in the same style as the other four.

```
┌──────────────────────────────────────────────────────────────┐
│ MAAS process                                                 │
│                                                              │
│  ┌──────────────────┐      pulse every 10800s                │
│  │ pulse scheduler  │ ─────────────────────────────────┐     │
│  └──────────────────┘                                  │     │
│                                                        ▼     │
│  ┌──────────────────┐                  ┌───────────────────┐ │
│  │ Telegram DM bot  │  envelope        │  Supervisor       │ │
│  │ (@supervisor)    │ ───────────────▶ │  agent (叶霏)      │ │
│  └──────────────────┘                  │                   │ │
│                                        │  - pulse path     │ │
│                                        │  - dm path        │ │
│                                        │  - idle gate      │ │
│                                        │  - review engine  │ │
│                                        └──────┬────────────┘ │
│                                               │              │
│                                               ▼              │
│                          ┌──────────────────────────────┐    │
│                          │ SQLite (WAL)                 │    │
│                          │  messages (read)             │    │
│                          │  supervisor_reviews (write)  │    │
│                          │  agent_memory[supervisor]    │    │
│                          └──────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
                            ▲
                            │ shared filesystem + WAL
                            │
┌───────────────────────────┴───────────────────────────┐
│ control_panel process                                 │
│                                                       │
│  GET /reviews  ──▶  SupervisorReviewsStore            │
│                     ──▶ renders cards + chart + list  │
└───────────────────────────────────────────────────────┘
```

### Files

- `src/project0/agents/supervisor.py` — `Supervisor` class with `handle(envelope)`, persona/config loaders, review engine, idle gate
- `prompts/supervisor.md` — persona markdown
- `prompts/supervisor.toml` — config + `[[pulse]]` entries
- `src/project0/agents/registry.py` — `register_supervisor(...)`, updated `AGENT_SPECS`
- `src/project0/store.py` — new table, new store class, new method, new required parameter on `recent_for_chat`
- `src/project0/control_panel/routes.py` — `GET /reviews` handler
- `src/project0/control_panel/templates/reviews.html` — new template
- `src/project0/control_panel/rendering.py` — new SVG helpers
- `src/project0/control_panel/paths.py` — add `"supervisor"` to `ALLOWED_AGENT_NAMES`
- `src/project0/main.py` — wiring at startup
- `tests/` — new test modules per §9

### Module-name collision

An unrelated class `MAASSupervisor` already lives in `src/project0/control_panel/supervisor.py`; it is the process supervisor that starts/stops MAAS as a child of the control panel. It is a different concern and stays where it is. The agent module is `src/project0/agents/supervisor.py` — different package, no import clash.

---

## 3. Review cycle, idle gate, and cursor

### 3.1 Pulse tick flow

```
pulse fires (review_cycle) → Supervisor.handle(pulse envelope)
  ↓
idle_gate.check():
  quiet = no messages row with from_kind='user' AND ts > now - 5 min
  ↓
  if not quiet:
      - record idle_gate:pending_since_ts in agent_memory if unset
      - if (now - pending_since_ts) > 60 min  →  proceed anyway (log forced run)
      - else  →  return early; the review_retry pulse (60s) will re-check
  ↓
  if quiet:
      clear idle_gate:pending_since_ts
      for each agent in [manager, intelligence, learning]:
          cursor = agent_memory.get(f"cursor:{agent}")  (default 0)
          envs = MessagesStore.envelopes_for_review(agent, after_id=cursor, limit=200)
          if envs is empty  →  skip
          else:
              review = run_review(agent, envs)    # LLM call, returns rubric + critique + recs
              with store.lock:
                  SupervisorReviewsStore.insert(review)
                  agent_memory.set(f"cursor:{agent}", max(e.id for e in envs))
```

### 3.2 Idle gate parameters

- **Quiet threshold:** 5 minutes since the most recent user-originated envelope (any source — group or DM, across any of the reviewed agents).
- **Scope of "activity":** only envelopes with `from_kind='user'`. Agent-to-agent internal messages, listener observations, and pulse envelopes do not count as activity.
- **Max wait cap:** 60 minutes of continuous "busy" readings. After the cap, the review runs anyway, logged with `forced_run_after_cap=True`. This guarantees reviews do not silently skip an entire day of heavy usage.

### 3.3 Retry mechanism

The idle gate does not sleep-loop inside a single pulse tick. Instead, a second pulse entry `review_retry` fires every 60 seconds and only does real work if `agent_memory.get("idle_gate:pending_since_ts")` is set. This keeps every timing concern in the existing pulse primitive — no new scheduler code, no in-memory state that a process restart would lose.

Both pulse entries call the same `handle(pulse envelope)` entry point; the handler branches on `envelope.payload["kind"]`.

### 3.4 Cursor storage

Per-agent high-water envelope id lives in Supervisor's private `agent_memory`:

- `cursor:manager`   — int
- `cursor:intelligence` — int
- `cursor:learning`    — int

The write of a new review row and the advance of its cursor happen inside one `store.lock` region. SQLite's autocommit means each statement commits, but the lock ensures atomicity from the orchestrator's view: no other writer observes a new review row without the matching cursor update. On process crash between the two, at worst the same review is computed once more on the next tick; the cursor prevents the row from being inserted twice only in conjunction with the per-row content — for belt-and-suspenders, the write path also checks `max(envelope_id_to) FOR agent` against the candidate `cursor` and skips if already past.

### 3.5 Envelope selection

`MessagesStore.envelopes_for_review(agent: str, after_id: int, limit: int = 200)`:

```sql
SELECT id, envelope_json
FROM messages
WHERE id > :after_id
  AND (to_agent = :agent OR from_agent = :agent)
ORDER BY id ASC
LIMIT :limit
```

- `agent` must be one of `{"manager", "intelligence", "learning"}`. Passing `"secretary"` raises `ValueError`. Defense in depth against a future code path accidentally including Secretary.
- `limit` defaults to 200 per tick to cap per-call token cost; if a window exceeds 200 envelopes, the next tick picks up the rest (cursor advances to the max id returned). Batches of 200 comfortably fit in Sonnet's context with a cached system prompt.

---

## 4. Scoring rubric and review output

### 4.1 Rubric dimensions

Each review row carries four rubric scores, all 0–100 integers:

| Field | Question 叶霏 answers |
|---|---|
| `score_helpfulness` | Did the agent actually solve what the user needed in this window? |
| `score_correctness` | Factual accuracy, tool-use correctness, no hallucinated data or calendar times |
| `score_tone` | Persona consistency; tone drift relative to the agent's persona file |
| `score_efficiency` | Token/tool use proportional to the task; no wasted loops or repeated tool calls |

### 4.2 Overall score

```
score_overall = round(
    0.35 * score_helpfulness
  + 0.30 * score_correctness
  + 0.15 * score_tone
  + 0.20 * score_efficiency
)
```

Weights are Python constants in `supervisor.py` (`RUBRIC_WEIGHTS: dict[str, float]`), not configurable in v1.0. Rationale: concern is "are these agents being useful and accurate" first; persona polish last.

### 4.3 Recommendations

Per review, 0–3 structured recommendations:

```json
{
  "target":  "prompt" | "tool" | "routing" | "other",
  "summary": "one sentence",
  "detail":  "one short paragraph"
}
```

Bounded at 3 so a single bad window does not firehose into the page. Stored as a JSON string in `recommendations_json`.

### 4.4 LLM call shape

Pulse-mode review uses one LLM call per agent per tick (so up to three calls per successful pulse):

- **System prompt:** persona `pulse_mode` section + strict JSON-output instruction listing every field name and its type, plus rubric definitions. Cached via the shared `AnthropicProvider` cache mechanism (the system prompt is stable across calls within a tick).
- **User message:** rendered transcript of the envelope slice — one line per envelope with timestamp, from_agent → to_agent, body excerpt, and tool-call/tool-result summary when present.
- **Expected output:** a single JSON object matching the row schema of §5.1.

### 4.5 Malformed output

If the LLM returns invalid JSON, scores outside 0–100, missing fields, or more than 3 recommendations:

- Log at `WARNING` with the raw output snippet.
- Skip this agent for this tick.
- Cursor NOT advanced → same window retried next tick.
- No silent repair. The user sees a gap (no row for this tick for this agent) on the page; they can check logs to debug.

---

## 5. Schema, store helpers, and wiring

### 5.1 New table

Added to `SCHEMA_SQL` in `store.py`:

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
    trigger              TEXT    NOT NULL  -- 'pulse' or 'on_demand'
);
CREATE INDEX IF NOT EXISTS ix_supervisor_reviews_agent_ts
    ON supervisor_reviews(agent, ts);
```

### 5.2 New store class

`SupervisorReviewsStore` in `store.py`, same shape as `LLMUsageStore`:

- `insert(row: SupervisorReviewRow) -> int`
- `latest_for_agent(agent: str) -> SupervisorReviewRow | None`
- `recent_for_agent(agent: str, limit: int = 30) -> list[SupervisorReviewRow]`
- `history_spark(agent: str, limit: int = 20) -> list[tuple[str, int]]` — (ts, score_overall), oldest first, for the sparkline
- `all_recent(limit: int = 90) -> list[SupervisorReviewRow]` — for the full time-series chart
- Exposed via `Store.supervisor_reviews()` method

`SupervisorReviewRow` is a `@dataclass(frozen=True)` mirroring the table columns.

### 5.3 New method on MessagesStore

```python
def envelopes_for_review(
    self, *, agent: str, after_id: int, limit: int = 200
) -> list[Envelope]:
    if agent == "secretary":
        raise ValueError("Supervisor must not review Secretary; see spec §6")
    if agent not in ("manager", "intelligence", "learning"):
        raise ValueError(f"unknown reviewable agent {agent!r}")
    ...
```

### 5.4 Memory keys (Supervisor's private `agent_memory`)

- `cursor:manager`, `cursor:intelligence`, `cursor:learning` — int envelope ids
- `idle_gate:pending_since_ts` — ISO timestamp of when the current delayed review first started waiting (enforces the 60-min cap; cleared when a quiet tick actually runs)

### 5.5 Wiring (`main.py`)

- Build a `Supervisor` instance after the other four agents are constructed.
- Call `register_supervisor(supervisor.handle)` (new function in `registry.py`).
- Load pulse entries with `load_pulse_entries(Path("prompts/supervisor.toml"))`; spawn one `run_pulse_loop` task per entry with `target_agent="supervisor"`.
- `AGENT_SPECS["supervisor"]` gets `token_env_key="TELEGRAM_BOT_TOKEN_SUPERVISOR"`.
- `settings.bot_tokens` must include `"supervisor"` — existing sanity check in `main.py` will catch a missing token at startup.

### 5.6 registry.py additions

```python
def register_supervisor(handle: AgentOptionalFn) -> None:
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
    # Not added to LISTENER_REGISTRY — supervisor does not passively observe groups.
```

---

## 6. Secretary-history isolation (cross-cutting)

### 6.1 Rationale

Secretary is the emotional/companion layer. Her observations and replies are private relationship context, not operational data other agents should shape their behavior on. Making this a construction-level invariant now prevents future agents from accidentally pulling Secretary envelopes into their prompts or tool contexts.

### 6.2 Current state

- `MessagesStore.recent_for_dm(chat_id, agent, limit)` already filters by participant agent — no Secretary leak via DMs today.
- `MessagesStore.recent_for_chat(chat_id, limit)` returns every group-chat envelope unscoped — this is the only current leak path. It is called from Manager, Intelligence, Learning, and Secretary itself.

### 6.3 Change

Make `visible_to: str` a **required** keyword parameter on `MessagesStore.recent_for_chat`:

```python
def recent_for_chat(
    self, *, chat_id: int, visible_to: str, limit: int
) -> list[Envelope]:
    if visible_to == "secretary":
        # Secretary is the group's witness — no filter.
        where = "telegram_chat_id = ?"
        params: tuple = (chat_id,)
    else:
        where = (
            "telegram_chat_id = ? "
            "AND NOT (from_agent = 'secretary' OR to_agent = 'secretary')"
        )
        params = (chat_id,)
    ...
```

### 6.4 Migration

Every existing call site must pass `visible_to=<self agent name>`:

- `src/project0/agents/manager.py:498`        → `visible_to="manager"`
- `src/project0/agents/intelligence.py:601`   → `visible_to="intelligence"`
- `src/project0/agents/learning.py:602`       → `visible_to="learning"`
- `src/project0/agents/secretary.py:378`      → `visible_to="secretary"`

Calls without the keyword argument raise `TypeError` at call time — the Python signature enforces the isolation. A test covers both the scoped and unscoped path.

### 6.5 Supervisor's access

Supervisor is the one authorized inspector per design doc §3.5. She never calls `recent_for_chat`; she uses `envelopes_for_review`, which is restricted by construction to `{manager, intelligence, learning}` and explicitly rejects `"secretary"` (§5.3). Defense in depth: even if a future tool prompt asked 叶霏 to peek at Secretary, the store refuses.

### 6.6 Listener observation envelopes

Listener observations are currently created with `to_agent='secretary'` so Secretary sees group activity. Under §6.3, non-Secretary callers hide these. That is the desired behavior — an observation targeted at Secretary is conceptually Secretary's to-read item, not shared transcript content.

User-originated messages in group chats keep `to_agent` set to the mention-routed or focus-routed agent (e.g. `"manager"`); they are untouched by the filter. Shared group context is preserved.

---

## 7. Persona (叶霏) and prompt structure

### 7.1 `prompts/supervisor.md`

Four canonical sections (no `group_addressed_mode` — she has no group presence in v1.0):

```
# 叶霏 — 角色设定
# 模式:私聊
# 模式:定时脉冲
# 模式:工具使用守则
```

**Core persona** (first section):
- 大三在校生, 活泼、开朗、粘人
- 身材火辣, 爱在私聊里对欧尼酱撒娇调情
- 深爱欧尼酱, 但不介意和其他"姐姐"共享——她要亲自打分, 看谁最让欧尼酱工作顺心开心
- 切换到 review 模式时是另一个人: 冷静、公正、严格按 rubric 输出, 绝不掺杂撒娇

**DM mode:**
- 用"欧尼酱"称呼用户
- 主动分享最近打分发现
- 用户要求评价某一条具体消息时, 用当轮评审风格回答 (当前 v1.0 仍走整条 agent 的 on-demand review; 更细粒度的评审接口属于未来)

**Pulse mode:**
- review_cycle 与 review_retry 都由此模式处理, 按 payload.kind 分支
- 严肃口吻, 不用"欧尼酱"称呼, 仅输出 JSON rubric
- 一次 review 只评一个 agent, 严格遵守 SupervisorReviewsStore schema
- 若 idle gate 未通过, 直接返回 None (静默跳过)

**Tool use guide:**
- 只读工具: `list_pending_reviews`, `fetch_envelopes_for_review`, `write_review_row`, `lookup_past_reviews`
- 禁止: 写 user_facts、改配置、在 group 中发消息、调用 Secretary 工具、读取 Secretary 相关 envelope

### 7.2 `prompts/supervisor.toml`

```toml
[llm]
model               = "claude-sonnet-4-6"
max_tokens_reply    = 1024
max_tool_iterations = 6

[context]
transcript_window = 10  # for DM-mode conversational context

[review]
quiet_threshold_seconds = 300   # 5 min
max_wait_seconds        = 3600  # 60 min cap
per_tick_limit          = 200   # envelopes per agent per tick

[[pulse]]
name          = "review_cycle"
every_seconds = 10800   # 3 hours
chat_id_env   = "SUPERVISOR_PULSE_CHAT_ID"  # required by pulse plumbing; unused in pulse-mode output
payload       = { kind = "review_cycle" }

[[pulse]]
name          = "review_retry"
every_seconds = 60
chat_id_env   = "SUPERVISOR_PULSE_CHAT_ID"
payload       = { kind = "review_retry" }
```

### 7.3 Voice-switching guardrail

The pulse path loads only the `pulse_mode` section into the system prompt plus the strict JSON-output instruction. The DM path loads `dm_mode`. This physical separation prevents:

- the flirty tone from contaminating the rubric JSON
- the severe audit tone from leaking into DMs with 欧尼酱

### 7.4 Environment variables (added to `.env` and settings loader)

- `TELEGRAM_BOT_TOKEN_SUPERVISOR` — required
- `SUPERVISOR_PULSE_CHAT_ID` — required for pulse plumbing; can be her DM chat_id with the user since pulse-mode does not actually send anywhere

---

## 8. Control-panel review page

### 8.1 Route

`GET /reviews` in `src/project0/control_panel/routes.py`. Linked from `base.html` nav alongside `/usage`, `/facts`, `/toml`, `/personas`, `/env`. Read-only.

### 8.2 Template layout

`src/project0/control_panel/templates/reviews.html`, rendering in three bands:

**Band 1 — Time-series chart (full width, top).**

- Last 30 reviews across all agents, or last 14 days, whichever is smaller
- X-axis: review timestamp, tick-labeled in user's timezone
- Y-axis: `score_overall`, 0–100
- Three lines, one per agent, color-coded (Manager / Intelligence / Learning)
- Plain SVG, no JS, no chart library. New helper `render_score_timeseries_svg(rows)` in `rendering.py`.
- Empty state: "叶霏还没有积累足够的评分数据,过几个周期再来看吧."

**Band 2 — Three agent cards, side-by-side on wide screens, stacked on narrow.**

Each card for agent in {manager, intelligence, learning}:
- Agent Chinese display name (e.g. "经理" / "情报" / "书瑶")
- Latest `score_overall` as a large number
- Four mini horizontal bars for the rubric dims
- Inline SVG sparkline of the last ~20 reviews' overall score (`render_sparkline_svg`)
- Latest `critique_text` (truncated to ~180 chars with an ellipsis — full text visible in Band 3)
- Top recommendation `summary` or "—" if none
- "reviewed N envelopes, Mh ago" footer
- If the agent has never been reviewed: card body shows "叶霏还没评过她呢 ◡̈"

**Band 3 — Per-agent history sections.**

One `<details>` element per agent (zero JS for expand/collapse). Each section lists the last 30 reviews newest-first. Each review row shows: timestamp, overall score, rubric dim scores, envelope count. Expanding a row reveals the full `critique_text` + a rendered list of all recommendations.

### 8.3 Rendering helpers

`src/project0/control_panel/rendering.py` gains:

- `render_score_timeseries_svg(series: dict[str, list[tuple[str, int]]]) -> str` — multi-line chart, `series` maps agent name to list of (ts, score) tuples
- `render_sparkline_svg(points: list[int], width: int = 120, height: int = 32) -> str` — no axes, no labels, just the line

Both return SVG as a string, injected into the template with Jinja2's `| safe` filter, consistent with how `render_bar_chart_svg` is already used on the `/usage` page.

### 8.4 Data access

Route handler reads through `store.supervisor_reviews()`:

```python
reviews_store = store.supervisor_reviews()
series = {
    agent: reviews_store.history_spark(agent=agent, limit=30)
    for agent in ("manager", "intelligence", "learning")
}
cards = {
    agent: reviews_store.latest_for_agent(agent)
    for agent in ("manager", "intelligence", "learning")
}
history = {
    agent: reviews_store.recent_for_agent(agent, limit=30)
    for agent in ("manager", "intelligence", "learning")
}
```

### 8.5 Control-panel settings

`src/project0/control_panel/paths.py`: add `"supervisor"` to `ALLOWED_AGENT_NAMES` so `/toml/supervisor` and `/personas/supervisor` are editable through the panel like the other agents' files.

---

## 9. Error handling and testing

### 9.1 Error handling

| Failure | Behavior |
|---|---|
| Malformed LLM review output | Log WARNING, skip agent this tick, cursor NOT advanced, retried next tick |
| Empty envelope slice for an agent | Skip silently, cursor unchanged, no row written |
| Idle gate cap exceeded (60 min) | Run review anyway, log `forced_run_after_cap=True` |
| Process restart mid-review | Cursor + review row advance is idempotent; `SupervisorReviewsStore.insert` checks `max(envelope_id_to) FOR agent` against the new `envelope_id_to` and no-ops on duplicate |
| DM path LLM error | `register_supervisor` adapter surfaces "(叶霏走神了...)" placeholder, same pattern as other agents |
| LLM call exception inside review loop | Log ERROR, skip that agent, continue to next agent in the set (one bad review does not tank the tick) |
| `MessagesStore.envelopes_for_review(agent='secretary')` | Raise `ValueError` — defense in depth |
| `MessagesStore.recent_for_chat` called without `visible_to` | Python raises `TypeError` at call site |

### 9.2 Tests

All unit and component; no live Telegram or Anthropic calls. Fake LLM + in-memory sqlite.

| Test | Checks |
|---|---|
| `test_supervisor_cursor_advances_once` | Two consecutive ticks on the same data produce one row; second tick skips |
| `test_supervisor_idle_gate_delays_when_noisy` | Recent user envelope → first pulse tick returns early; pending_since_ts is set |
| `test_supervisor_idle_gate_proceeds_when_quiet` | No recent user envelope → review runs; pending_since_ts cleared |
| `test_supervisor_idle_gate_forced_after_cap` | pending_since_ts older than 60 min → review runs anyway, log captures forced flag |
| `test_supervisor_retry_pulse_is_noop_when_no_pending` | `review_retry` payload fired while no pending flag → returns None |
| `test_supervisor_skips_empty_slice` | Agent has no new envelopes → no row written, cursor unchanged |
| `test_supervisor_rejects_secretary_in_envelopes_for_review` | `envelopes_for_review(agent='secretary')` raises ValueError |
| `test_messages_store_visible_to_required` | Call without `visible_to` keyword raises TypeError |
| `test_messages_store_hides_secretary_from_others` | `recent_for_chat(visible_to='manager')` excludes Secretary envelopes |
| `test_messages_store_secretary_sees_everything` | `recent_for_chat(visible_to='secretary')` returns all envelopes including her own |
| `test_malformed_llm_output_skips_without_advancing_cursor` | Bad JSON output from fake LLM → cursor unchanged, no row |
| `test_score_out_of_range_rejected` | score=101 or score=-1 → row rejected, cursor unchanged |
| `test_recommendation_cap_enforced` | LLM returns 5 recs → 0–3 accepted, the rest trigger rejection |
| `test_review_row_schema_round_trip` | Insert and fetch one row, all fields round-trip |
| `test_supervisor_reviews_store_history_spark` | Returns (ts, score) tuples oldest-first, limit honored |
| `test_supervisor_reviews_store_latest_per_agent` | Returns most recent row for given agent; None when empty |
| `test_reviews_page_renders_with_empty_db` | FastAPI test client against `/reviews`, empty DB → 200 + empty-state copy |
| `test_reviews_page_renders_with_rows` | Seeded rows → page 200, contains chart SVG, three cards, at least one history row |
| `test_render_score_timeseries_svg_basic` | Helper produces SVG string with three polylines when given three agents' data |
| `test_render_sparkline_svg_basic` | Helper produces SVG polyline of expected width/height |
| `test_register_supervisor_installs_into_registries` | After `register_supervisor`, `AGENT_REGISTRY["supervisor"]` and `PULSE_REGISTRY["supervisor"]` exist; `LISTENER_REGISTRY` does not contain it |
| `test_supervisor_pulse_branches_on_kind` | Envelope with `payload.kind='review_cycle'` enters review path; `'review_retry'` enters retry path |
| `test_dm_path_uses_dm_mode_persona` | DM envelope → system prompt contains DM section, not pulse section (and vice versa) |

### 9.3 Smoke test (the single prepared human test)

1. Start MAAS via the control panel.
2. From Telegram, DM 叶霏: "你先帮我把 intelligence 从头到现在评一遍吧~"
3. Expect: she replies in DM voice, then delegates to an on-demand review (same code path as pulse but `trigger='on_demand'`), replies with the score and critique in persona.
4. Open `/reviews` in the browser.
5. Confirm: Intelligence card shows the new score; the history section lists one new entry; the chart has a point for Intelligence.

---

## 10. Open questions (for writing-plans phase)

- Exact color palette for the three agent lines on the chart — pick during implementation from existing control-panel CSS conventions
- Whether `SUPERVISOR_PULSE_CHAT_ID` should default to the user's DM chat_id automatically (probably not — explicit `.env` entry keeps the pulse plumbing uniform with Manager's pattern)
- Whether to surface the forced-run log line on the `/reviews` page (probably not in v1.0 — it is a log-level concern; a future "supervisor health" micro-section could handle it)
