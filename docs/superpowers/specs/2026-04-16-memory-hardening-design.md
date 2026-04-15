# Memory Layer Hardening + Token Cost Cut — Sub-Project Design

**Date:** 2026-04-16
**Parent project:** Project 0: Multi-agent assistant system
**Parent sub-project spec:** `docs/superpowers/specs/2026-04-13-multi-agent-skeleton-design.md`
**Pre-brainstorm notes:** `docs/superpowers/notes/2026-04-16-memory-hardening-prenotes.md`
**Sub-project scope:** Introduce the shared long-term memory surfaces the master spec promised, bundled with a focused set of token-cost improvements so the new memory content lands net-neutral or net-cheaper. Install the instrumentation that makes every future cost question answerable with one SQL query.

---

## 1. Purpose and Framing

The master spec defines six memory layers (A-F). After the skeleton, the Secretary, the Google Calendar layer, Manager, and the two Intelligence sub-projects shipped, the state of those layers is:

| Layer | Master spec purpose | Built? | Location |
|---|---|---|---|
| A | User profile and long-term preferences (static-ish) | ❌ | — |
| B | Shared blackboard | ✅ table exists, underused | `blackboard` SQLite table |
| C | Agent private working memory | ✅ | `agent_memory` SQLite table |
| D | Formal knowledge base (Learning agent's write surface) | ❌ | — |
| E | Audit/traces/scores | ✅ (reframed as derived from `messages`, not a separate write path) | `messages` SQLite table |
| F | Source cache (external material) | ✅ for current scope | `data/intelligence/reports/`, `data/intelligence/feedback/` |

This sub-project addresses Layer A and a narrow vertical slice of Layer D — specifically, the ability for agents to persist facts *learned from conversation with the user*. Layer B stays unchanged (activation deferred to the local-LLM sub-project). Layer D's broader scope (structured knowledge base, review cards, concept maps, consolidation passes) stays deferred to the dedicated Learning agent sub-project.

### Design stance

Two goals must be balanced:

1. **Long-term learning for product v1.0.** Agents should remember things the user tells them across conversations, across days, across restarts. Today they do not.
2. **Aggressive token cost reduction.** This is the dominant cost concern right now. Every design choice in this spec is evaluated against it, and when the two goals conflict, token cost wins the tiebreaker because it's the more urgent constraint.

The sub-project must land **net-neutral or net-cheaper** on a per-turn input-cost basis, measured against current production behavior. "Adding memory without making things slower or more expensive" is the explicit success criterion.

### Layer A, redefined

The master spec's Layer A described a fairly rich "user profile" — goals, planning preferences, reminder preferences, output style, work rhythm, interests, approval rules. That scope is too broad for what we actually need now. For this sub-project, Layer A is redefined as:

> A small, hand-edited static file containing identity-level facts that agents need every turn but that cannot reasonably be learned from conversation: birthday, fallback form of address for new agents, fixed standing preferences, a short free-form notes paragraph.

Dynamic learning about the user — hobbies, current projects, food preferences, things that make the user happy, things the user doesn't want to talk about — is **not** Layer A in this redefinition. That belongs in the new Layer D slice, because it's learned through conversation, not declared.

### Layer D slice, redefined

The master spec's Layer D is Learning agent's future home: structured knowledge entries, concept maps, review cards, consolidation passes. For this sub-project we steal only the narrowest possible slice:

> A single append-only table of short facts the Secretary learned about the user from conversation, readable by all agents, writable only by Secretary via a tool call.

No consolidation. No dedup. No contradiction detection. No knowledge structure beyond "short sentence + optional topic tag." These are Learning agent's problems and will be addressed when Learning ships. The current slice is sized to bootstrap the *experience* of long-term learning without taking on the governance burden of doing it well.

### Why Secretary as the sole writer

Three candidate write-authority models were considered:

1. **All agents write opportunistically.** Each agent decides in-the-moment whether to persist a fact. Rejected: opens governance questions (duplicate writes, conflicting facts, inter-agent race conditions) that distract from the sub-project's core goals.
2. **Read-only store, hand-edited YAML.** No agent write path. Rejected: not really "learning" — indistinguishable from a config file until Learning agent ships, and doesn't exercise the write path at all, so interesting bugs would surface only later.
3. **Single writer: Secretary.** The agent whose job is already to pay conversational attention to the user gains a `remember_about_user` tool. Other agents read the resulting facts but cannot write. Selected.

The Secretary-only design reduces the governance problem to "Secretary decides," avoids three agents racing to write the same fact, and mirrors how a proper Learning pipeline would work anyway (read-heavy on the line agents, write gated to a specialist). The schema is forward-compatible with a future multi-writer or Learning-replaces-Secretary design — nothing about "Secretary-only for now" paints us into a corner later.

---

## 2. Architecture and Data Flow

No new processes, no new threads, no new services. Everything lives inside the existing single-process asyncio model. New pieces hang off existing seams in `main.py` (composition root), `store.py` (trust boundary), `llm/provider.py` (single instrumentation point), and each agent's entry module.

### 2.1 Composition root wiring

```
main.py  (unchanged flow, new wiring)

  load_settings ──▶ load UserProfile from data/user_profile.yaml (new)
                ──▶ open Store (new tables created by schema init)
                ──▶ build LLMUsageStore on the same connection (new)
                ──▶ build AnthropicProvider(usage_store=..., cache_ttl=...) (new args)

  for each agent:
      build with:
          persona                 (unchanged)
          AgentMemory             (unchanged)
          UserProfile block       (new, read)
          UserFactsReader         (new, read)
          UserFactsWriter         (new, Secretary only)
          LLMProvider             (wrapped to record usage on every call)
```

### 2.2 System prompt layout discipline (two cache breakpoints)

Every agent's `system` block is assembled in a strict order with **two `cache_control` breakpoints**, so a Secretary fact-write busts only the smaller second segment and leaves the large stable segment warm:

```
Segment 1 — large, rarely busts:
  1. persona.core
  2. persona.mode_section     (listener / addressed / dm / reminder / agentic)
  3. tool specs               (for Manager and Intelligence; empty for Secretary)
  4. UserProfile block        ← NEW, busts only on YAML edit + restart
[cache_control: ephemeral] ← BREAKPOINT #1

Segment 2 — small, busts on Secretary writes:
  5. UserFacts block          ← NEW, busts 1-3 times/day
[cache_control: ephemeral] ← BREAKPOINT #2

messages[] — always volatile, never cached:
  6. {role: "user", content: scene + transcript + current turn}
```

Anthropic's cache is a prefix cache, and each `cache_control` marker creates a cache hit for everything up to that point. With two breakpoints, Anthropic stores two cache entries per agent: one covering items 1-4 and a longer one covering items 1-5. A byte change in segment 2 (a new user fact) invalidates only the second entry; subsequent calls read segment 1 from cache (at ~0.1×) and pay cache-creation (~1.25×) only on the delta covered by segment 2.

**Why this matters at Secretary's write rate:** at 1-3 writes per day across 3 agents, placing the breakpoint *before* facts saves roughly `3250 tokens × 1.15 multiplier_delta × 3 agents × 2 busts/day ≈ 22k token-equivalents per day`, which is meaningful at current usage.

**The invariant encoded as a test:** for each agent, building the system prompt twice with only a `messages[]` difference must produce byte-identical Segment-1 bytes *and* byte-identical Segment-2 bytes. Two byte-comparison assertions per agent. If a future sub-project accidentally puts volatile content into either segment, the test fails. The test also asserts that exactly two `cache_control` markers exist, at the expected positions.

**Why tool specs live in Segment 1, not Segment 2:** tool specs change only on code deploy, which already requires a restart. Placing them in the stable segment means Manager and Intelligence get the full stability benefit. Profile also stays in Segment 1 because YAML edits require a restart too, so the per-turn cache hit rate on Segment 1 should be ~100% during a running session.

### 2.3 How a `remember_about_user` tool call flows

Secretary currently uses single `complete()` calls on all four of her entry paths (listener / addressed / DM / reminder). Giving her a write tool means adding a bounded tool-use path:

```
User → Secretary (whatever entry path)
  │
  ▼
complete() with tools=[remember_about_user]
  │
  ├── response contains no tool call ──▶ reply text is the final reply
  │
  └── response contains a tool call:
        │
        ▼
        UserFactsWriter.add(fact_text, topic)
        │
        ▼
        INSERT into user_facts
        │
        ▼
        tool_result: {"ok": true, "fact_id": 42}
        │
        ▼
        complete() a second time, with the tool_result in messages
        │
        ▼
        reply text is the final reply
```

Maximum one tool round-trip per turn. This is a bounded version of the agentic loop that Manager and Intelligence already use, not a full iterative loop. A full loop is unnecessary because Secretary has only one tool.

Implementation note: Secretary's existing `_addressed_llm_call`, `_handle_dm`, `_handle_reminder`, and `_listener_llm_call` functions all converge on a `self._llm.complete(...)` call. A shared helper in `secretary.py` wraps the one-tool-one-round-trip pattern and is called from each path. The tool is exposed on every path where Secretary replies. On the listener path, if the model emits the `[skip]` sentinel *and* a tool call, the write persists and the reply is suppressed — Secretary can notice something worth remembering even on a turn she decides not to speak.

### 2.4 How instrumentation flows

`AnthropicProvider.complete` is the single write site for `llm_usage`. After every successful API response:

1. Extract `response.usage.input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`.
2. Append one row to `llm_usage` via `LLMUsageStore.record(...)`.
3. Emit one `logger.info` line with the same fields, plus the `envelope_id` and `purpose` labels from the caller.

Failed API calls (exceptions) are not recorded. The usage table is a record of actually-billed calls only. The existing exception-logging path is unchanged.

`envelope_id` is passed explicitly as a new required kwarg on `complete()` (or `None` for calls outside a turn-handling context). This is the `messages.id` of the envelope being processed, so every row in `llm_usage` can be joined back to its originating envelope. For pulse ticks and report generation the value is `None`. ContextVar-based magic was considered and rejected — explicit kwargs are clearer, more testable, and easier to grep.

### 2.5 What is deliberately not changing

- **Orchestrator routing logic:** untouched.
- **Envelope schema:** untouched. Token cost / latency go to `llm_usage`, not into envelopes — consistent with skeleton §8 decision #1 ("messages are first-class storage; audit is derived").
- **`agent_memory` table and API:** untouched.
- **`blackboard` table and API:** untouched. (Activation deferred to local-LLM sub-project.)
- **`chat_focus` table:** untouched.
- **`messages` table:** untouched. `llm_usage.envelope_id` is a logical FK from the new table to the existing one; the existing table gains no columns.
- **Pulse path, calendar tools, Intelligence report generation, the webapp:** untouched except for the Intelligence Q&A tool-surface changes in §4.3.
- **Persona markdown files:** untouched (see §7.3 below).

---

## 3. Storage Schema, APIs, Trust Boundary

Three new surfaces added to `store.py`. Zero changes to existing tables.

### 3.1 Layer A — `user_profile.yaml` (file, no table)

**Location:** `data/user_profile.yaml` (gitignored). Example committed as `data/user_profile.example.yaml`.

**Shape:**
```yaml
# All fields optional. Unknown top-level keys are ignored (forward-compatible
# with future Learning / OpenClaw extensions).

address_as: "主人"              # fallback only — each agent's persona owns her
                                # own form of address and overrides this. Used
                                # when a new agent (e.g. from OpenClaw) joins
                                # the system without a baked-in address form.

birthday: "1995-03-14"          # ISO 8601 date, optional

fixed_preferences:              # short free-text bullets; keep ≤ 5
  - "说话简洁，不要太啰嗦"
  - "不喜欢凌晨打扰"

out_of_band_notes: |            # one short paragraph of standing context
  我在做 MAAS 这个多 agent 系统项目。
```

**API:**
```python
class UserProfile:
    def __init__(self, path: Path) -> None: ...
    @classmethod
    def load(cls, path: Path) -> "UserProfile": ...
    def as_prompt_block(self) -> str:
        """Render as the Chinese-language bullet block inlined into every
        agent's cached system prompt. Returns empty string if the profile
        file is missing — absence is not an error."""
```

**Load-time behavior:**
- **Missing file** → empty profile, empty prompt block, log warning once at startup. Not fatal. The system must run cleanly on a fresh checkout where the user has not yet written the YAML.
- **Malformed YAML** → fatal error at startup with the file path and parser error message. Fail loud on corruption.
- **Unknown top-level keys** → ignored with a per-key warning.
- **Invalid date / non-list `fixed_preferences` / non-string `out_of_band_notes`** → fatal with the specific field name.
- **No hot reload.** Profile is loaded once at startup. Editing the YAML requires a restart. Documented in the README.

**Rendered prompt block (Chinese, exact wording lives in the renderer):**
```
关于用户（静态资料，由用户手动维护）：
- 称呼: 主人
- 生日: 1995-03-14
- 固定偏好:
  · 说话简洁，不要太啰嗦
  · 不喜欢凌晨打扰
- 备注: 我在做 MAAS 这个多 agent 系统项目。
```

**Trust boundary:** no write path exposed to Python. The only way to change the profile is to edit the file and restart. Profile is declarative identity, not conversational learning — changes should be deliberate and visible in the editor, not a side effect of a tool call.

### 3.2 Layer D slice — `user_facts` table

```sql
CREATE TABLE user_facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,            -- ISO 8601 UTC
    author_agent  TEXT    NOT NULL,            -- always 'secretary' for v1
    fact_text     TEXT    NOT NULL,            -- one sentence
    topic         TEXT,                        -- optional free tag
    is_active     INTEGER NOT NULL DEFAULT 1   -- 1=active, 0=soft-deleted
);
CREATE INDEX ix_user_facts_active ON user_facts(is_active, ts);
```

**Schema notes:**
- **No uniqueness constraint.** Duplicate writes are allowed. Dedup is Learning agent's job.
- **No foreign keys.** Facts are self-contained identity claims; they do not reference a specific envelope.
- **`is_active` is soft delete.** Required so Learning-agent consolidation passes can later see what was superseded. Also lets Secretary self-correct a wrong fact in a later turn without losing the audit trail.
- **No `superseded_by` column yet.** When Learning ships, it can extend this table or add a revisions table. Not this sub-project's problem.

**Reader API (any agent):**
```python
class UserFactsReader:
    def __init__(self, agent_name: str) -> None:
        self._agent = agent_name
        # Stored for future per-agent topic filtering. In v1 all agents
        # see all active facts.

    def active(self, limit: int = 30) -> list[UserFact]:
        """Return active facts, newest first."""

    def as_prompt_block(self, max_tokens: int = 600) -> str:
        """Render as a Chinese bullet block for the cached system prompt.
        Drops oldest active facts from the rendered output (not from
        storage) when the rendered length would exceed max_tokens. The
        cap is a hard prompt-size limit; when it triggers for real, it
        signals Learning-agent consolidation is overdue."""
```

**Writer API (Secretary only):**
```python
class UserFactsWriter:
    def __init__(self, agent_name: str) -> None:
        if agent_name != "secretary":
            raise PermissionError(
                f"user_facts writer not allowed for agent={agent_name!r}; "
                "only 'secretary' may write user facts in this sub-project"
            )
        self._agent = agent_name

    def add(self, fact_text: str, topic: str | None = None) -> int:
        """Insert a new fact. Returns the new row id. ts and author_agent
        are set by this method — callers cannot spoof identity or
        backdate writes."""

    def deactivate(self, fact_id: int) -> None:
        """Soft-delete by id. Used by Secretary if a later turn realizes
        a previous fact was wrong. Hard delete is not exposed."""
```

**Trust boundary properties:**

1. **Writer construction is the gate.** `UserFactsWriter("manager")` raises `PermissionError` synchronously at construction. There is no path to a Writer instance for a non-Secretary agent.
2. **The orchestrator is the only constructor.** `main.py` builds one `UserFactsWriter("secretary")` and hands it only to Secretary's constructor. No other agent ever sees a reference.
3. **`author_agent` is written by the API, not passed in.** `add()` has no `author` parameter. Callers cannot spoof identity.
4. **The `remember_about_user` tool is only exposed to Secretary.** The tool spec is in Secretary's `tools=[...]` list; Manager and Intelligence do not see it in their system prompts. Double protection: even if Manager somehow obtained a writer reference, she would have no way to trigger it via LLM tool call.
5. **No raw SQL outside store.py.** Agent code touches `UserFactsReader.active()`, `UserFactsReader.as_prompt_block()`, `UserFactsWriter.add()`, `UserFactsWriter.deactivate()`. Nothing else. Same discipline as `AgentMemory` from the skeleton.
6. **`all_including_inactive()` exists for future Supervisor/manual inspection** but is not called from any live agent in this sub-project.

### 3.3 `llm_usage` table (operational telemetry)

```sql
CREATE TABLE llm_usage (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                          TEXT    NOT NULL,
    agent                       TEXT    NOT NULL,
    model                       TEXT    NOT NULL,
    input_tokens                INTEGER NOT NULL,
    cache_creation_input_tokens INTEGER NOT NULL,
    cache_read_input_tokens     INTEGER NOT NULL,
    output_tokens               INTEGER NOT NULL,
    envelope_id                 INTEGER,                -- logical FK to messages.id; nullable
    purpose                     TEXT    NOT NULL
);
CREATE INDEX ix_llm_usage_ts       ON llm_usage(ts);
CREATE INDEX ix_llm_usage_agent    ON llm_usage(agent, ts);
CREATE INDEX ix_llm_usage_envelope ON llm_usage(envelope_id);
```

**Design notes:**
- **`envelope_id` is nullable** — not every LLM call is triggered by a user envelope. Daily report generation, pulse ticks, and tests have no envelope.
- **`envelope_id` is a logical FK, not a SQL FK.** Enabling SQLite foreign key enforcement globally is an independent decision that doesn't belong in this sub-project; the nullable integer column is enough for join queries and the integrity is enforced at the API layer.
- **`purpose` is an open string** — callers pass `"reply"`, `"listener"`, `"tool_loop"`, `"report_gen"`, `"qa"`, etc. Enables the future WebUI page to group by operation type.
- **`agent` is NOT necessarily the Python agent class name.** Daily report generation uses `intelligence_summarizer` as the agent label, distinct from Q&A which uses `intelligence`. This lets a single query answer "how much did daily reports cost this month?" without string filtering on model or purpose.
- **No cost-dollar column.** Pricing can change per model and per tier; store token counts and compute dollars at the WebUI layer. The durable record is tokens.
- **Append-only.** No UPDATE, no DELETE. Consistent with `messages`.
- **No retention policy.** At casual usage (tens of rows per day), a year of data is trivial for SQLite. Retention is a problem for later if it ever becomes one.

**API:**
```python
class LLMUsageStore:
    def __init__(self, conn: sqlite3.Connection) -> None: ...

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
        """Append one usage row. Called exclusively from inside
        AnthropicProvider.complete after a successful API response."""

    def summary_since(self, ts: str) -> list[dict]:
        """Aggregated rollup: per-agent totals since ts. Used by the
        future WebUI token-usage page. Not called from agent code."""
```

**Trust boundary:** `record()` is technically public but by convention is called only from the provider layer. `summary_since()` is read-only. Agent code calls neither.

### 3.4 `store.py` shape after this sub-project

Net additions, no removals:
- `UserProfile` class (~30 lines)
- `UserFactsReader` class (~40 lines)
- `UserFactsWriter` class (~30 lines)
- `LLMUsageStore` class (~40 lines)
- Schema creation for `user_facts` and `llm_usage` in the existing init (~10 lines)

Estimated `store.py` size post-sub-project: ~600-700 lines, up from ~400. Still below the ~1000-line soft limit where splitting into a `store/` package would become urgent. If it crosses 800 in a future sub-project, split then.

---

## 4. Token Cut Mechanics

Four concrete changes, each with the mechanism, expected saving, and risk.

### 4.1 Cache-friendly system block layout with two breakpoints (prenotes #1)

**Mechanism:** Every agent's system prompt is assembled in the two-segment order specified in §2.2. Segment 1 contains persona + mode + tool specs + UserProfile with a `cache_control: ephemeral` breakpoint at its end. Segment 2 contains only UserFacts with a second `cache_control: ephemeral` breakpoint. Transcript, scene, current-turn user message, and tool-loop intermediate state live in `messages[]`, never in either segment.

**Expected saving:**
- *Structural win:* the cuts in §4.2 and §4.3 only *stay* saved across turns if volatile content does not leak into the cached prefix. This discipline is the enforcement.
- *Direct win from two breakpoints:* at 1-3 fact-writes per day × 3 agents, splitting the cached prefix into a large stable segment + a small volatile-ish segment saves roughly 22k token-equivalents per day over the single-breakpoint layout. The saving is proportional to the size of Segment 1 and the fact-write rate.

**Invariant test:** for each of Secretary / Manager / Intelligence:
1. Build the system prompt twice with only a `messages[]` difference; assert Segment 1 bytes and Segment 2 bytes are independently identical.
2. Assert exactly two `cache_control` markers exist in the expected positions.
3. Assert no volatile content (any string derived from `env.body`, `transcript`, etc.) appears in either segment.

Failing test = violation of the discipline.

**Risk:** low. One-time layout task, locked with the test above.

### 4.2 Manager transcript window 20 → 10 (prenotes #3, amended)

**Mechanism:** `prompts/manager.toml` `[context] transcript_window = 10`. Secretary stays at 20 (explicit user decision — conversational continuity matters most for her). Intelligence already uses 10.

**Expected saving:** Manager's transcript turns average ~100 tokens each. 10 fewer turns = ~1000 tokens removed from every Manager call's volatile section.

This is the highest-value single cut in the sub-project. **Transcript tokens are volatile — outside the cache, paid at full rate every turn.** A 1000-token cut on volatile input is worth roughly 10× a 1000-token cut on cached content, because cached content is read at ~10% of full price. Shrinking Manager's volatile transcript is where most of the real dollar savings come from.

**Why Manager specifically:** calendar operations are mostly one-shot ("add an event tomorrow 3pm" does not need turn 15 from earlier today). Manager's long-range memory lives in Google Calendar itself, not in transcript. Shrinking the window to 10 loses near-nothing.

**Why Secretary stays at 20:** conversational continuity is her core value. Locked in.

**Why Intelligence stays at 10:** already 10.

**Risk:** low. If Manager starts missing earlier context, the signal is obvious ("she forgot what we agreed on two hours ago") and the fix is one line in the TOML. Reversible in under a minute.

**Test:** per-TOML-file assertion that the value is exactly 10 for Manager, 20 for Secretary, 10 for Intelligence. Prevents drift.

### 4.3 Intelligence report slim + `get_report_item` tool (prenotes #6)

**Mechanism:** Intelligence's Q&A path currently injects the full latest `DailyReport` JSON (~2500 tokens) into the system prompt on every call. Replace with a compact headline index (~500 tokens) and expose a new tool for on-demand deep-dive.

**New injected shape:**
```
今天的日报索引 (2026-04-16):
  [r01] OpenAI 发布 GPT-5.5，主要升级是……
  [r02] Anthropic 推出 Claude 4.6……
  [r03] Google DeepMind 新论文……
  ...
```

**New tool (Intelligence-only):**
```python
ToolSpec(
    name="get_report_item",
    description="Fetch the full content of a single item from today's daily "
                "report by its id. Use when the user asks to dig deeper on a "
                "specific item from the headline list.",
    input_schema={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "e.g. 'r01'"},
            "date": {"type": "string", "description": "YYYY-MM-DD; defaults to today"},
        },
        "required": ["item_id"],
    },
)
```

Tool implementation reads from `data/intelligence/reports/YYYY-MM-DD.json` — the same files the webapp already reads. No new storage.

**Expected saving:** ~2000 volatile-input tokens per Intelligence Q&A turn that does not deep-dive into specific items. Like Manager's transcript cut, this is a volatile cut, so per-token value is ~10× cached-content value.

**Trade-off:** turns that do deep-dive into multiple items pay one tool round-trip per item fetched. Break-even: the saving wins as long as the average Q&A turn deep-dives into fewer than ~4 items. Realistic usage is 0-1 deep-dive items per turn.

**Risk — explicit:** if the headline list is too terse, the model cannot answer even general questions without a tool call, so every turn pays a round-trip. The `[id] headline` format gives enough signal to answer "what's the most important news today?" directly from the injected index. Going below this threshold (e.g., cutting headlines to 15 characters) would break the design.

**Test:** seed a 12-item report, assert Intelligence's Q&A system prompt contains only the `[id] headline` form and the rendered report section is ≤ 700 tokens.

**Deletion:** the old *Q&A-time* full-report injection code path is *deleted*, not gated behind a config flag. To be explicit about what is and is not deleted:
- **Deleted:** the code that inlines the full `DailyReport` JSON into Intelligence's Q&A system prompt on every chat turn.
- **Unchanged:** the daily report *generation* pipeline in `intelligence/generate.py` (morning Opus + extended thinking run). Reports continue to be generated and written to `data/intelligence/reports/YYYY-MM-DD.json` exactly as before.
- **Unchanged:** the webapp that reads those report files and renders them in the browser.

Escape hatches rot, so the Q&A injection path goes away entirely rather than being gated behind a config flag.

### 4.4 Extended 1-hour cache TTL — env-toggled, default off

**Mechanism:** new setting `ANTHROPIC_CACHE_TTL` in `.env` and `config.py`. Valid values: `ephemeral` (default, current behavior, ~5 min) or `1h`. `AnthropicProvider.complete` constructs the `cache_control` marker accordingly:

```python
cache_control = (
    {"type": "ephemeral", "ttl": "1h"}
    if settings.anthropic_cache_ttl == "1h"
    else {"type": "ephemeral"}
)
```

**Why default off:** the 1h TTL has a higher cache-creation multiplier on first call. Break-even depends on usage-gap patterns that cannot be estimated without live data. Shipping it as opt-in lets the user flip it after the WebUI token monitor lands and make an informed decision.

**Validation:** invalid values (e.g., `5m`, empty string) raise at `config.load_settings` time, not at first LLM call. Fail fast on misconfiguration.

**Risk:** near-zero. One env var, two possible values, reversible in one line.

### 4.5 Token budget accounting

Approximate per-turn input cost for a typical warm-cache Manager turn:

| Component | Before | After | Delta |
|---|---|---|---|
| Persona (cached, read at 0.1×) | 3000 × 0.1 = 300 | 3000 × 0.1 = 300 | 0 |
| UserProfile (cached, new) | 0 | 250 × 0.1 = 25 | **+25** |
| UserFacts (cached, new) | 0 | 300 × 0.1 = 30 | **+30** |
| Tool specs (cached) | 2000 × 0.1 = 200 | 2000 × 0.1 = 200 | 0 |
| Transcript (volatile, paid at 1.0×) | 2000 × 1.0 = 2000 | 1000 × 1.0 = 1000 | **−1000** |
| Scene + current turn (volatile) | 150 | 150 | 0 |
| **Effective input cost, warm cache** | **2650** | **1705** | **−945 (−36%)** |

Intelligence Q&A warm-cache turn is similar in structure, with the big win on report pruning:

| Component | Before | After | Delta |
|---|---|---|---|
| Persona + tool specs (cached) | ~500 | ~500 | 0 |
| UserProfile + UserFacts (cached, new) | 0 | ~55 | **+55** |
| Report injection (volatile) | 2500 × 1.0 = 2500 | 500 × 1.0 = 500 | **−2000** |
| Transcript (volatile) | ~1000 | ~1000 | 0 |
| **Effective input cost, warm cache** | **~4000** | **~2055** | **−1945 (−49%)** |

Secretary warm-cache turn:

| Component | Before | After | Delta |
|---|---|---|---|
| Persona (cached) | ~1500 × 0.1 = 150 | ~1500 × 0.1 = 150 | 0 |
| UserProfile + UserFacts (cached, new) | 0 | ~55 | **+55** |
| Transcript (volatile) | ~2000 | ~2000 | 0 |
| **Effective input cost, warm cache** | **~2150** | **~2205** | **+55 (+2.5%)** |

Secretary is the only agent where the sub-project costs slightly more per turn (she gains conversational memory at a small cache-read price). The absolute increase is ~55 effective tokens; the product value is the long-term memory goal itself.

**Cold-cache numbers are worse** because the entire cached prefix is paid at the cache-creation multiplier (~1.25×) rather than the read multiplier (~0.1×). A cold cache is a one-time cost and amortizes across every subsequent call within the 5-minute (or 1-hour with the toggle) window.

**The single most important number in this section is the −1000 on Manager's transcript.** That is the cut carrying most of the real savings. If the scope had to shrink further, that line is the one to preserve.

---

## 5. Instrumentation

Instrumentation lands inside this sub-project, alongside the cuts. There is no separate baseline-measurement phase — the user explicitly opted to ship cuts immediately and watch the effect via live logging and the future WebUI page.

### 5.1 `AnthropicProvider.complete` — the only write site

```python
async def complete(
    self,
    *,
    system: str | list[dict],
    messages: list[Msg],
    max_tokens: int,
    tools: list[ToolSpec] | None = None,
    # NEW required kwargs:
    agent: str,
    purpose: str,
    # NEW optional kwarg:
    envelope_id: int | None = None,
) -> str | ToolUseResult:
    ...
    response = await self._client.messages.create(...)

    # After a successful response, record usage.
    self._usage_store.record(
        agent=agent,
        model=self._model,
        input_tokens=response.usage.input_tokens,
        cache_creation_input_tokens=getattr(
            response.usage, "cache_creation_input_tokens", 0
        ) or 0,
        cache_read_input_tokens=getattr(
            response.usage, "cache_read_input_tokens", 0
        ) or 0,
        output_tokens=response.usage.output_tokens,
        envelope_id=envelope_id,
        purpose=purpose,
    )
    log.info(
        "llm call agent=%s model=%s in=%d cc=%d cr=%d out=%d env=%s purpose=%s",
        agent, self._model,
        response.usage.input_tokens,
        getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        response.usage.output_tokens,
        envelope_id if envelope_id is not None else "-",
        purpose,
    )

    return ...  # existing return logic unchanged
```

Three properties worth noting:
1. **`agent` and `purpose` are required kwargs.** No defaults. A missing kwarg is a `TypeError` at the call site. This enforces per-call labeling and prevents mystery rows.
2. **Recording happens on success only.** A failed API call has no meaningful `usage` object. Failed calls remain separately logged (unchanged behavior) but are not persisted to `llm_usage`.
3. **`getattr(..., 0) or 0`** handles both the "field missing on some response types" case and the "field present but None on calls without caching" case. Both collapse to 0.

### 5.2 Call sites to update

Every existing `self._llm.complete(...)` call gains `agent=` and `purpose=` kwargs, and `envelope_id=` where available:

| File | Method | `agent` | `purpose` | `envelope_id` |
|---|---|---|---|---|
| `secretary.py` | `_listener_llm_call` | `"secretary"` | `"listener"` | `env.id` |
| `secretary.py` | `_addressed_llm_call` | `"secretary"` | `"reply"` | `env.id` |
| `secretary.py` | `_handle_reminder` | `"secretary"` | `"reminder"` | `env.id` |
| `manager.py` | agentic loop | `"manager"` | `"tool_loop"` | `env.id` |
| `intelligence.py` | Q&A loop | `"intelligence"` | `"qa"` | `env.id` |
| `intelligence/generate.py` | daily report summarizer (Opus + extended thinking) | `"intelligence_summarizer"` | `"report_gen"` | `None` |

**Tool-loop follow-up calls reuse the initiating path's `purpose` label.** When Secretary's listener path invokes the tool loop and the model emits a `remember_about_user` call, the second `complete()` call (sent after the tool result) is still labeled `purpose="listener"`. The loop does not introduce new labels. Same pattern for Manager's `tool_loop` and Intelligence's `qa` paths where they already iterate.

The `intelligence_summarizer` / `intelligence` split is deliberate: daily report generation uses a different model (Opus vs Sonnet) and a very different cost profile from interactive Q&A. Tagging them as distinct "agents" in the usage table lets a single query answer "how much did daily reports cost this month?" without string filtering on model.

### 5.3 Composition-root wiring

```python
# main.py
store = Store(settings.store_path)
usage_store = LLMUsageStore(store.conn)
llm = AnthropicProvider(
    api_key=settings.anthropic_api_key,
    model=settings.llm_model,
    cache_ttl=settings.anthropic_cache_ttl,
    usage_store=usage_store,
)
```

`AnthropicProvider` holds `usage_store` as a field and uses it on every successful response. `FakeProvider` (used in tests) accepts an optional `usage_store` parameter defaulting to `None` — existing tests continue to work without passing one; new tests that specifically exercise usage recording pass a real in-memory `LLMUsageStore`.

### 5.4 What this sub-project does NOT build

- **Aggregation dashboards.** `summary_since()` exists; the WebUI that reads it is next sub-project.
- **Alerting on cost thresholds.** Supervisor-layer concern.
- **Sampling.** Every call is recorded.
- **Retention / rotation.** Kept forever for now.

---

## 6. Scope, Non-Goals, Acceptance Criteria

### 6.1 In scope

1. `data/user_profile.yaml` + `UserProfile` class + composition-root loading.
2. `user_facts` SQLite table + `UserFactsReader` + `UserFactsWriter` + Secretary-only construction enforcement.
3. `remember_about_user` tool exposed to Secretary only + a bounded one-tool-one-round-trip path on every Secretary entry path.
4. `llm_usage` SQLite table + `LLMUsageStore` API + wiring in `AnthropicProvider.complete`.
5. Per-call stderr log line from `AnthropicProvider.complete` in the format specified in §5.1.
6. Every existing `llm.complete(...)` call site updated with the `agent=` and `purpose=` labels specified in §5.2, and `envelope_id=` where available.
7. `prompts/manager.toml` transcript_window 20→10 (Secretary stays 20; Intelligence stays 10).
8. Intelligence Q&A: replace full-report injection with headline-index form + `get_report_item` tool. Delete the full-report injection code path.
9. `ANTHROPIC_CACHE_TTL` env var (`ephemeral` default, `1h` opt-in) with validation at startup.
10. Cache-friendly system-block layout discipline per §2.2, locked by the byte-identical-prefix invariant test.
11. `.env.example` and `data/user_profile.example.yaml` updated/added with documented defaults.

### 6.2 Non-goals (explicit, so future-you doesn't re-litigate)

1. **Persona pruning** (prenotes #2) — the biggest single token win, but deferred to its own focused sub-project. No markdown persona files are edited in this sub-project. Rationale: section-level pruning needs careful section-by-section review that distracts from the structural work here.
2. **Transcript summarization** (prenotes #5) — deferred to the local-LLM sub-project, where near-zero inference cost changes the break-even math. Recorded in the roadmap for that later sub-project.
3. **Lazy memory access via tools for profile/facts** (prenotes #4) — rejected. Lazy access pays off only when the gated content is large and irrelevant on most turns; profile and facts are the opposite (small, needed every turn for continuity). The one place lazy access does make sense — Intelligence's full report — is already handled by §4.3.
4. **Tool-spec pruning** (prenotes #7) — rejected. Tool specs are in the cached prefix and cost ~10% of their nominal tokens per turn after the first call. Premature at current tool counts. Revisit when Manager passes ~10 tools.
5. **Baseline measurement phase.** Skipped by user decision. Instrumentation and cuts ship together; the user will read live logs and the future WebUI page rather than compare before/after aggregates.
6. **Persona voice changes.** Aside from the pre-work naming of Secretary as 苏晚 (committed separately before this sub-project), no persona voice changes are in scope.
7. **Layer B blackboard activation.** Deferred with #5.
8. **Secretary listener redesign, model tiering, skip-mechanism removal.** Rejected after analysis showed the skip mechanism is a single-call pattern, not a two-call classify-then-reply, so removing it does not save tokens.
9. **Postgres migration.** Deferred until WebUI creates real concurrent writers.
10. **Learning agent, Supervisor agent, OpenClaw integrations.** Their own sub-projects.
11. **Automatic dedup, contradiction detection, consolidation of user_facts.** Learning agent's job.
12. **Dollar-cost columns or cost math.** Token counts are the durable record; dollars are a WebUI-layer concern.

### 6.3 Acceptance criteria

The sub-project is done when **all of the following hold at once.** No partial credit.

#### A. Tests and checks

- **A.1.** `uv run pytest` green, zero failures. All new tests from §A through §I below present.
- **A.2.** `uv run mypy src/project0` zero errors.
- **A.3.** `uv run ruff check src tests` zero warnings.

#### B. Schema and storage

- **B.1.** `data/store.db` created from a fresh `rm` includes `user_facts` and `llm_usage` tables with exactly the columns and indexes in §3.2 and §3.3.
- **B.2.** `data/user_profile.example.yaml` is committed with all four optional fields populated with believable placeholder values, and `UserProfile.load()` parses it without error.
- **B.3.** `data/user_profile.yaml` is in `.gitignore`.
- **B.4.** Existing `agent_memory`, `blackboard`, `chat_focus`, `messages` tables are byte-identical in schema to pre-sub-project. Verified by a schema-dump comparison test.

#### C. Trust boundary — enforced, not merely stated

- **C.1.** `UserFactsWriter("manager")` raises `PermissionError` at construction. Tested.
- **C.2.** `UserFactsWriter("intelligence")` raises `PermissionError` at construction. Tested.
- **C.3.** `UserFactsWriter("secretary").add("test", topic=None)` writes a row with `author_agent="secretary"` and a server-set `ts` the caller cannot override. Tested.
- **C.4.** Manager's assembled system prompt does NOT contain the string `remember_about_user`. Intelligence's does NOT contain it. Only Secretary's does. Tested by string assertion on the assembled prompt text.
- **C.5.** `store.py` exposes no API that lets agent code write directly to `user_facts` bypassing `UserFactsWriter`. Verified by a grep-based test: the SQL string `INSERT INTO user_facts` appears in exactly one place in `store.py`.

#### D. Cache-layout discipline

- **D.1.** For each of Secretary / Manager / Intelligence, building the system prompt twice with only a `messages[]` difference produces byte-identical Segment-1 bytes **and** byte-identical Segment-2 bytes. Two independent byte-comparison assertions per agent. The §4.1 invariant test passes.
- **D.2.** Exactly two `cache_control` markers exist in each agent's assembled system block, at the expected positions (after profile, after facts). Tested.
- **D.3.** Segment 1 contains persona + mode + tool specs + profile, in that order. Segment 2 contains only the user_facts block. Tested by asserting block order.
- **D.4.** No volatile content (transcript, scene, current-turn user message, tool-loop intermediate state, anything derived from `env.body`) appears in either segment. Tested.

#### E. Token cut mechanics

- **E.1.** `prompts/manager.toml` `transcript_window = 10`. Tested by TOML read.
- **E.2.** `prompts/secretary.toml` `transcript_window = 20`. Unchanged. Tested to prevent accidental drift.
- **E.3.** `prompts/intelligence.toml` `transcript_window = 10`. Unchanged. Tested.
- **E.4.** Intelligence Q&A system prompt, given a seeded 12-item report, contains only the `[id] headline` form. Total report section ≤ 700 rendered tokens. Tested.
- **E.5.** Intelligence exposes a `get_report_item` tool; fetching a known item by id returns the full item dict. Tested end-to-end with a fake report file on disk.
- **E.6.** The old full-report injection code path is deleted, not gated. Grep in tests asserts no function/flag named `inject_full_report` or similar remains.

#### F. Instrumentation

- **F.1.** Every existing `llm.complete(...)` call site from §5.2 has been updated to pass `agent=` and `purpose=`. Missing a kwarg is a `TypeError` — a single test per call site confirms the labels, which mechanically proves the updates exist.
- **F.2.** Running a fake Secretary listener turn against a `FakeProvider` wrapping a real in-memory `LLMUsageStore` produces exactly one `llm_usage` row with `agent="secretary"`, `purpose="listener"`, matching `envelope_id`, and four non-negative token columns.
- **F.3.** `intelligence_summarizer` is the agent label for daily report generation; `intelligence` is the label for Q&A. Tested via the per-call-site pattern.
- **F.4.** `AnthropicProvider.complete` emits exactly one stderr log line per successful call in the §5.1 format. Tested via caplog assertion.
- **F.5.** Failed API calls do NOT write rows to `llm_usage`. Tested by making the fake client raise.

#### G. Cache TTL toggle

- **G.1.** `.env.example` documents `ANTHROPIC_CACHE_TTL=ephemeral` (default) with a comment mentioning `1h` as the opt-in value.
- **G.2.** Invalid values (e.g., `5m`, empty string) raise at `config.load_settings` time with a message naming the variable. Tested.
- **G.3.** Default value is `ephemeral` — no behavior change from current production unless the user opts in. Tested.
- **G.4.** When set to `1h`, `AnthropicProvider.complete` passes `{"type": "ephemeral", "ttl": "1h"}` as the cache_control marker. Tested by asserting the argument dict sent to a mocked Anthropic client.

#### H. Layer A (user profile) behavior

- **H.1.** Missing `data/user_profile.yaml` → MAAS starts, warning logged once, agents' prompts contain an empty profile block (or no profile block at all — renderer's choice). Tested.
- **H.2.** Malformed `user_profile.yaml` → MAAS raises at startup with the path and parser error. Tested.
- **H.3.** Each agent's persona `address_as` (baked into the individual persona markdown) overrides the profile's `address_as` field at runtime. Tested by asserting that Secretary's system prompt does not contain the profile's fallback `address_as` string when her persona already uses `老公`. This confirms the "new-agent-only fallback" semantics.
- **H.4.** Unknown top-level keys in the YAML are ignored with a warning. Tested.

#### I. Layer D slice (user_facts) behavior

- **I.1.** Secretary can `remember_about_user(fact_text="生日3月14日", topic="personal")` via the bounded tool-loop path. The row appears in `user_facts` with `author_agent="secretary"`. Tested end-to-end with a `FakeProvider` that scripts the tool call.
- **I.2.** After a write, all three agents (Manager / Secretary / Intelligence) see the new fact rendered in their next system prompt via `UserFactsReader.as_prompt_block()`. Tested.
- **I.3.** `UserFactsReader.as_prompt_block()` with 50 active facts produces ≤ 600 tokens of rendered output. The oldest active facts are dropped from rendering (not storage). Tested with a seeded table of 50 short facts.
- **I.4.** `deactivate(fact_id)` sets `is_active=0`; the deactivated fact no longer appears in `as_prompt_block()`. Tested.
- **I.5.** Soft-deleted facts remain in the table and are visible via `all_including_inactive()`. Tested.

#### J. Final human smoke test (one prepared session)

Automated tests cover everything mechanically verifiable. The only thing that requires human eyes is the cross-agent, Telegram-integrated end-to-end experience. The implementer prepares the environment fully before handing the smoke test to the user.

**What the implementer prepares:**

1. All A-I tests green.
2. `data/store.db` wiped to a clean state.
3. `data/user_profile.yaml` pre-populated with believable seed values (`address_as`, `birthday`, `fixed_preferences`, `out_of_band_notes`) so the user does not have to write it.
4. `.env` already has `ANTHROPIC_CACHE_TTL=ephemeral` set.
5. A helper script `scripts/smoke_memory.sh` that, after the smoke session, runs three SQLite queries and prints readable output:
   - Recent `user_facts` rows with author, topic, fact text, is_active.
   - `llm_usage` rollup per agent for the smoke session window (sum of each token column grouped by agent and purpose).
   - Cache-read vs cache-create ratio for the session, to prove cache discipline is working.
6. MAAS started and all three bots polling.

**The smoke test steps (user actions, target <15 minutes total):**

1. **(DM Secretary)** Send `记住我最喜欢的食物是寿司`. Confirm Secretary replies naturally and does not break character.
2. **(DM Manager)** Send `我最喜欢吃什么？`. Confirm Manager answers referencing 寿司. *This is the learning-across-agents proof.*
3. **(Group chat)** Send `@manager 明天有什么事？`. Confirm calendar readback still works unchanged.
4. **(Group chat)** Send `@intelligence 今天最要紧的是什么？`. Confirm Intelligence answers from the slim headline index without calling `get_report_item`.
5. **(Group chat)** Send `@intelligence 第一条具体讲什么？`. Confirm Intelligence calls `get_report_item`, fetches the full item, and answers with detail. *This is the lazy-report-access proof.*
6. **(Terminal)** Run `scripts/smoke_memory.sh`. Confirm:
   - A `user_facts` row containing 寿司 with `author_agent='secretary'`.
   - `llm_usage` rows across all three agents with nonzero `cache_read_input_tokens` on subsequent calls, proving cache discipline.
   - Rollup token counts are in a reasonable range.

If any step fails, the user reports the failure and the implementer fixes it. The user is never asked to debug.

#### K. Scope discipline

- **K.1.** `git diff --stat origin/main` shows changes only in:
  - `src/project0/store.py`
  - `src/project0/llm/provider.py`
  - `src/project0/config.py`
  - `src/project0/main.py`
  - `src/project0/agents/secretary.py`, `manager.py`, `intelligence.py`
  - `src/project0/intelligence/generate.py` (summarizer call-site labels)
  - `prompts/manager.toml` (transcript_window only)
  - `tests/` (new + amended)
  - `docs/superpowers/specs/2026-04-16-memory-hardening-design.md` (this file)
  - `docs/superpowers/plans/2026-04-16-memory-hardening.md` (written in the next phase)
  - `data/user_profile.example.yaml` (new)
  - `scripts/smoke_memory.sh` (new)
  - `.env.example`
  - `.gitignore`
  - `README.md` (a small update to the Roadmap section)
- **K.2.** **No persona markdown files are edited in this sub-project.** The persona pruning pass is a separate future sub-project. The pre-work commit naming Secretary 苏晚 is explicitly outside this sub-project's diff (it landed before the sub-project began).
- **K.3.** **No changes to `blackboard`, `chat_focus`, `messages` schema, or envelope schema.** Enforced by the schema-immutability test in B.4.

If any of A-K fails, the sub-project is not done. No "close enough."

---

## 7. Design Decisions Worth Flagging for Future Sub-Projects

1. **Layer A as redefined in this spec is narrower than the master spec described.** The master spec's Layer A included planning preferences, approval rules, decision authority — this sub-project reduces Layer A to identity-level static facts and moves the dynamic "understanding of the user" into the new Layer D slice. When Learning agent ships and the broader Layer D arrives, the Layer A / Layer D boundary may need to be revisited. For now, the rule is: **Layer A = declared, Layer D = learned.**
2. **Secretary is the sole writer to `user_facts` in v1.** When Learning agent ships, Learning may become the primary writer and Secretary may drop the tool, or Secretary may keep the tool as a "fast path" for facts mentioned in the immediate conversation. Either evolution is supported by the current schema — `author_agent` is already a column.
3. **`llm_usage` is the durable telemetry substrate for the future WebUI token-usage page and for Supervisor's future cost analysis.** Do not add a parallel telemetry store. Enrich `llm_usage` instead (e.g., add a `latency_ms` column when latency matters).
4. **The two-segment cache layout in §2.2 is a hard invariant.** Any future sub-project that adds content to the system prompt must respect the two-breakpoint split: durable content (persona, tool specs, profile) belongs in Segment 1; content that busts on a conversational cadence (facts today, perhaps Learning-agent summaries later) belongs in Segment 2; volatile per-turn content stays in `messages[]`. The invariant test is the guard. Do not weaken it. Additional breakpoints beyond two are allowed if a later sub-project has a principled reason for a third segment (Anthropic supports up to four), but require an explicit spec section justifying the choice.
5. **`envelope_id` on `llm_usage` is a logical FK only.** Do not promote it to a real SQL FK without a global decision about SQLite foreign key enforcement in `store.py`.
6. **`ANTHROPIC_CACHE_TTL` is env-controlled, not per-agent.** If a future sub-project needs per-agent TTL, add a second env var or a config section rather than plumbing the setting through every call site.
7. **`user_facts` has no dedup, no contradiction detection, no consolidation.** This is accepted technical debt for v1 and is Learning agent's responsibility. Do not add partial solutions piecemeal.
8. **Persona markdown files are untouched in this sub-project.** A focused persona-pruning sub-project (the deferred prenotes #2) owns all voice changes and token-cut work on those files.

---

## 8. Roadmap Position

Per the master spec §9 sequence, this sub-project is **"sub-project 2 — memory layer hardening."** After this, the sensible ordering is:

1. **This sub-project** — memory hardening + token cost cut (current).
2. **WebUI control panel** — reads `messages` and `llm_usage`, renders trace trees, adds the token-usage monitoring page.
3. **Learning agent** — full Layer D (consolidation, review cards, formal KB writes). Takes over `user_facts` writes from Secretary, or coexists as a consolidation pass.
4. **Supervisor agent** — audit/evaluation authority, analytic views on `messages` and `llm_usage`.
5. **Local LLM migration** — bundle prenotes #5 (transcript summarization) here. Once inference is local, summarization cost collapses and makes transcript summarization a default-on feature.
6. Further out: persona pruning (deferred prenotes #2), tool gateway hardening, Postgres migration when concurrent writers become real.

Each of the above gets its own brainstorm → spec → plan → implementation cycle.
