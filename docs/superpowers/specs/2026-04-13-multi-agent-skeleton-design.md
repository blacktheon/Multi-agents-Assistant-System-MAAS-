# Multi-Agent Assistant — Skeleton Sub-Project Design

**Date:** 2026-04-13
**Parent project:** Project 0: Multi-agent assistant system
**Sub-project scope:** Minimal end-to-end skeleton. Proves the orchestrator, memory isolation, shared blackboard, envelope contract, and Telegram group routing. No real intelligence, no LLM calls, no WebUI.

---

## 1. Purpose and Framing

The parent project defines a five-agent personal assistant (Manager, Secretary, Intelligence, Learning, Supervisor) with strict separation of coordination authority (Manager) from inspection authority (Supervisor), isolated per-agent working memory, a shared blackboard, Telegram as the chat frontend, and a planned WebUI control panel.

That parent project is too large for a single spec. It decomposes into roughly seven independent sub-projects:

1. **Skeleton** (this spec): orchestrator + two agent stubs + Telegram wiring, proving the contracts.
2. Memory & storage layer hardening.
3. Telegram chat frontend with per-agent bots and group focus.
4. WebUI control panel.
5. Tool gateway (Twitter/X ingestion, WeChat article fetch, etc.).
6. Individual real agent implementations (Manager, Secretary, Intelligence, Learning, Supervisor), each non-trivial.
7. Audit/metrics/supervision pipeline.

This spec covers sub-project 1 only. Its purpose is to **lock down the contracts** (envelope schema, memory isolation, routing rules, storage schema) that every subsequent sub-project depends on. The contracts are the load-bearing output; the running code is just proof the contracts compose.

### Design stance

The skeleton must be **visibly, embarrassingly unintelligent**. If after running it you think "this is kind of impressive," the skeleton has failed — it means product code has leaked into scaffolding and the contract/implementation boundary is gone. Stub replies should literally say `[stub] acknowledged:` so nobody, including future-you, mistakes the skeleton for a step toward the real product. The real product starts in sub-project 6.

---

## 2. Architecture Overview

Single Python process running an asyncio event loop. Everything persists to a single SQLite file. No LLM calls are executed (though the Anthropic client is imported and its key is validated at startup). No LangGraph anywhere. No WebUI.

```
┌──────────────────────────────────────────────────────────────┐
│ main.py  (asyncio event loop)                                │
│                                                              │
│   ┌─────────────────┐   ┌─────────────────┐                  │
│   │ telegram bots   │   │ orchestrator    │                  │
│   │ (N pollers,     │──▶│ (dispatcher:    │                  │
│   │  one per agent) │   │  focus + route) │                  │
│   │ skeleton N=2    │   └────────┬────────┘                  │
│   └────────▲────────┘            │                           │
│            │                     ▼                           │
│            │            ┌─────────────────┐                  │
│            │            │ agent registry  │                  │
│            │            │  manager_stub   │                  │
│            │            │  intel_stub     │                  │
│            │            └────────┬────────┘                  │
│            │                     │                           │
│            │                     ▼                           │
│            │            ┌─────────────────┐                  │
│            └────────────│ send-reply API  │                  │
│                         │ (per-agent bot) │                  │
│                         └────────┬────────┘                  │
│                                  ▼                           │
│                         ┌─────────────────┐                  │
│                         │  SQLite store   │                  │
│                         │  4 tables       │                  │
│                         └─────────────────┘                  │
└──────────────────────────────────────────────────────────────┘
```

### Key shape commitments

- **Each Telegram bot is its own async task** inside the shared event loop. `python-telegram-bot` `Application` instances run concurrently. They share nothing except the store and the orchestrator dispatcher.
- **The orchestrator is plain async Python, roughly 150 lines.** No framework. Its only responsibilities are: resolve focus, call the right agent stub, persist envelopes, dispatch outbound replies through the correct bot.
- **The agent registry is a dict** `{name: callable}` in `agents/registry.py`. Adding a new agent is adding a row to the dict and a bot token to `.env`. No decorator-based auto-discovery.
- **No LangGraph at the orchestrator level.** LangGraph's strength is stateful multi-step *agent reasoning*, which does not exist in this sub-project. When a real agent is built in sub-project 6, that agent may embed its own LangGraph graph internally. The orchestrator stays plain Python forever.

### Process and persistence model

Single Python process. Single SQLite database file at `data/store.db`. Single shared `sqlite3` connection wrapped in an asyncio lock for all writes, because that is the simplest way to make the multi-bot dedup race impossible. Scale is one human user sending tens of messages per hour; the single-connection serialization overhead is negligible and the simplification is substantial.

SQLite will be swapped for Postgres in a later sub-project (when the WebUI lands and concurrent writers become a real concern). The `store.py` API surface is designed so that swap is a driver change, not an architecture change. Splitting into multiple processes or introducing a message broker is deliberately not planned — a personal multi-agent assistant does not have the throughput or fault-isolation problems that would justify a distributed system.

---

## 3. Storage Schema

Single SQLite file at `data/store.db`. Four tables. All access goes through `src/project0/store.py` — agent code never touches SQL directly. **`store.py` is a trust boundary.** Memory isolation is enforced in the Python API surface, not in the database (SQLite has no row-level security). Any change to `store.py` deserves extra review.

### Table 1 — `agent_memory` (private working memory)

```sql
CREATE TABLE agent_memory (
    agent_name   TEXT NOT NULL,
    key          TEXT NOT NULL,
    value_json   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,   -- ISO-8601 UTC
    PRIMARY KEY (agent_name, key)
);
```

Access API:

```python
class AgentMemory:
    def __init__(self, agent_name: str): ...
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any) -> None: ...
    def delete(self, key: str) -> None: ...
```

An `AgentMemory` instance is scoped to one agent at construction time and has no API to query other agents' rows. The orchestrator constructs and passes each agent its own instance. Isolation is enforced by construction.

### Table 2 — `blackboard` (shared collaboration surface)

```sql
CREATE TABLE blackboard (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    author_agent  TEXT NOT NULL,
    kind          TEXT NOT NULL,   -- 'task_summary', 'handoff_note', ...
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX ix_blackboard_created_at ON blackboard(created_at);
CREATE INDEX ix_blackboard_kind       ON blackboard(kind);
```

Access API:

```python
class Blackboard:
    def append(self, author: str, kind: str, payload: dict) -> int: ...
    def recent(self, limit: int = 50, kind: str | None = None) -> list[dict]: ...
```

Append-only. No update/delete. Every agent can read everything. The `author` value is passed in by the orchestrator from the dispatched agent's identity — agents cannot spoof each other. `kind` is an open string in the skeleton.

### Table 3 — `messages` (first-class message log)

```sql
CREATE TABLE messages (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 TEXT NOT NULL,
    source             TEXT NOT NULL,    -- 'telegram_group' | 'telegram_dm' | 'internal'
    telegram_chat_id   INTEGER,
    telegram_msg_id    INTEGER,
    from_kind          TEXT NOT NULL,    -- 'user' | 'agent' | 'system'
    from_agent         TEXT,             -- NULL when from_kind='user'
    to_agent           TEXT NOT NULL,
    envelope_json      TEXT NOT NULL,
    parent_id          INTEGER,          -- FK to messages.id for reply/handoff chains
    UNIQUE (source, telegram_chat_id, telegram_msg_id)
);
CREATE INDEX ix_messages_ts       ON messages(ts);
CREATE INDEX ix_messages_to_agent ON messages(to_agent);
CREATE INDEX ix_messages_parent   ON messages(parent_id);
```

The `UNIQUE` constraint is the dedup mechanism: because all bots in a group have privacy mode off (see section 5), every bot's poller sees every group message, and the first-writer-wins race is resolved at the DB layer. Later inserts of the same `(source, chat_id, msg_id)` raise `IntegrityError` and are dropped silently.

`parent_id` lets the WebUI (future sub-project) reconstruct the full tree of a single user request through delegation chains.

This table is the **first-class source of truth for audit/trace data**, diverging deliberately from the parent spec's Layer E. Agents do not write to a parallel audit table. The Supervisor sub-project will build analytical views on top of `messages`, not duplicate its data.

### Table 4 — `chat_focus` (routing state, per Telegram chat)

```sql
CREATE TABLE chat_focus (
    telegram_chat_id  INTEGER PRIMARY KEY,
    current_agent     TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
```

One row per group chat. The skeleton assumes a single human user, so there is no per-user dimension. Default agent on first message in a new chat is `manager`. Updated when (a) the user @mentions a different agent or (b) Manager delegates.

### Deliberately omitted from the skeleton

- **User profile layer (parent spec Layer A)** — added when Manager has real planning logic.
- **Formal knowledge base (Layer D)** — Learning sub-project.
- **Source cache (Layer F)** — Intelligence sub-project.
- **Audit/scores (Layer E) as separate storage** — reframed as derived from `messages`, not a separate write path.

---

## 4. Envelope Schema

The envelope is the single in-memory object that flows between the orchestrator and agents, and the object serialized into `messages.envelope_json`. Locking its shape now is the most important thing this skeleton does.

```python
@dataclass
class Envelope:
    # --- identity ---
    id: int | None                  # messages.id, assigned on persist
    ts: str                         # ISO-8601 UTC
    parent_id: int | None           # links delegation / reply chains

    # --- where it came from ---
    source: Literal["telegram_group", "telegram_dm", "internal"]
    telegram_chat_id: int | None
    telegram_msg_id: int | None
    received_by_bot: str | None     # which agent's bot token saw it first

    # --- who is talking ---
    from_kind: Literal["user", "agent", "system"]
    from_agent: str | None          # None when from_kind == "user"
    to_agent: str

    # --- the payload ---
    body: str
    mentions: list[str]             # parsed @mentions, in order

    # --- routing metadata ---
    routing_reason: Literal[
        "direct_dm",
        "mention",
        "focus",
        "default_manager",
        "manager_delegation",
        "outbound_reply",
    ]
```

### Illustrated flows

**Flow A — user types `hello` in the group, no @mention, focus is Manager (default).**

Two envelopes are persisted:

1. `source=telegram_group`, `from_kind=user`, `to_agent=manager`, `body="hello"`, `routing_reason=default_manager`, `parent_id=None`.
2. `source=internal`, `from_kind=agent`, `from_agent=manager`, `to_agent=user`, `body="[manager-stub] acknowledged: hello"`, `routing_reason=outbound_reply`, `parent_id=1`.

**Flow B — user types `any news today?` in the group. Manager keyword-matches and delegates.**

Four envelopes are persisted, in this tree (IDs reflect persistence order, which follows execution order in section 5):

```
#1 user → manager              (default_manager)                parent=None
 ├─ #2 manager → user          (outbound_reply, internal)       parent=#1
 │      body: "→ forwarding to @intelligence"
 └─ #3 manager → intelligence  (manager_delegation, internal)   parent=#1
     └─ #4 intelligence → user (outbound_reply, internal)       parent=#3
```

Envelope #2 (visible handoff message from Manager's bot to the group) and envelope #3 (internal forward of the original user text to Intelligence) are siblings, both children of the original user message #1. Envelope #4 (Intelligence's reply) is a child of the internal forward #3.

This tree shape generalizes to the future case where Manager summons multiple agents for one complex task: those become additional sibling children of #1, each with their own `outbound_reply` children. No schema change will be needed.

After this flow, `chat_focus.current_agent` for this chat is `intelligence`.

### Design notes on what the envelope does and does not contain

- **Outbound replies have `source=internal`.** The envelope describes where the message originated, not where it is delivered. An agent's reply originates internally even though it is delivered over Telegram. Outbound delivery is handled by `telegram_io.send_reply` and does not need a dedicated source value.
- **No `cost_usd` / `tokens_in` / `tokens_out` / `latency_ms` / `model_name` fields.** Agents that actually call LLMs can add an optional `agent_meta` dict field in a later sub-project without breaking existing envelopes.
- **No `private_thoughts` / `chain_of_thought`.** Agent scratch space lives in `agent_memory`, never in envelopes, never in the `messages` table.
- **No priority or urgency field.** Premature.
- **No `reply_to_msg_id` separate from `parent_id`.** `parent_id` covers both "replying to X" and "spawned by X".

---

## 5. Routing Rules

### Pipeline for an incoming Telegram update

```
Telegram update arrives at bot B
         │
         ▼
  (1) dedup: try to INSERT into `messages` with
      UNIQUE (source, telegram_chat_id, telegram_msg_id)
      → if IntegrityError, drop silently, done
         │
         ▼
  (2) allow-list check: reject unauthorized chat_id or user_id
         │
         ▼
  (3) classify source: telegram_dm vs telegram_group
         │
         ├──── DM path ─────────────────────┐
         │              to_agent = B (the agent who owns this bot)
         │              routing_reason = "direct_dm"
         │              (no focus update; DMs do not touch chat_focus)
         │
         └──── Group path ──────────────────┐
                      (4) parse @mentions from message text
                      (5) resolve target:
                          ─ if ≥1 valid @mention:
                              target = last mentioned agent
                              routing_reason = "mention"
                              UPDATE chat_focus.current_agent = target
                          ─ else if chat_focus has row for this chat_id:
                              target = chat_focus.current_agent
                              routing_reason = "focus"
                          ─ else (first message in this chat):
                              target = "manager"
                              routing_reason = "default_manager"
                              INSERT chat_focus (chat_id, "manager")
                                            │
                                            ▼
  (6) persist full envelope into `messages`
  (7) dispatch: call agent_registry[target](envelope)
  (8) handle the agent's return value (see Agent return contract)
```

### Agent return contract

```python
@dataclass
class AgentResult:
    reply_text: str | None      # if set, send as outbound_reply
    delegate_to: str | None     # if set, orchestrator delegates to this agent
    handoff_text: str | None    # the visible handoff message; required if delegate_to set
```

Exactly one of `reply_text` or `delegate_to` must be set. Both-or-neither is a programming error and the orchestrator raises `RoutingError`.

### Delegation execution (when `delegate_to` is set)

When Manager returns `delegate_to="intelligence"`, the orchestrator:

1. Posts `handoff_text` to the Telegram group as Manager's bot, and persists the outbound envelope with `routing_reason=outbound_reply`, `parent_id=original_user_msg.id`.
2. Constructs an internal envelope with `source=internal`, `from_agent=manager`, `to_agent=intelligence`, `routing_reason=manager_delegation`, `body=original_body`, `parent_id=original_user_msg.id`. Persists it.
3. Updates `chat_focus.current_agent = "intelligence"`.
4. Dispatches `intelligence_stub(internal_envelope)`.
5. Intelligence returns `AgentResult(reply_text=...)`. Orchestrator posts it via Intelligence's bot, persists as `outbound_reply`, `parent_id = the internal delegation envelope`.

### Delegation authority rule

**Delegation authority belongs exclusively to Manager.** If any non-Manager agent returns `delegate_to=...`, the orchestrator raises `RoutingError` and does not dispatch the delegation. This mirrors the parent spec's coordination/inspection separation and keeps the routing topology a strict tree of depth ≤ 2.

### Stub agent logic (skeleton)

```python
# manager_stub
async def manager_stub(env: Envelope) -> AgentResult:
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

# intelligence_stub
async def intelligence_stub(env: Envelope) -> AgentResult:
    return AgentResult(
        reply_text=f"[intelligence-stub] acknowledged: {env.body}",
        delegate_to=None,
        handoff_text=None,
    )
```

**The "news" keyword rule is the entire routing intelligence in the skeleton.** Manager performs no natural-language understanding. A message like `what's happening in the world?` does not match and is handled by Manager's stub echo. This is intentional so nobody mistakes the skeleton for real agent behavior. Real routing decisions will be made by the real Manager in sub-project 6 via LLM tool use.

### Telegram group and DM specifics

- **All bots have privacy mode disabled via BotFather `/setprivacy`.** Without this, bots in groups only see messages that @mention them or reply to them, which breaks sticky-focus follow-ups.
- **DMs do not trigger delegation.** If the user DMs Intelligence's bot, Intelligence answers directly. No focus tracking, no rerouting from DMs. This may be revisited in later sub-projects.
- **Media / stickers / voice / service messages** are logged as `body="[non-text]"` and routed normally. Media parsing is out of scope.
- **Dedup race.** Between a first bot's INSERT and a second bot's INSERT of the same Telegram update, the UNIQUE constraint resolves the race at the DB layer. The single shared SQLite connection + asyncio lock ensures the first INSERT is fully committed before the second is attempted.

### Allow-list enforcement

The orchestrator rejects any Telegram update whose `chat_id` is not in `TELEGRAM_ALLOWED_CHAT_IDS` **and** whose `user.id` is not in `TELEGRAM_ALLOWED_USER_IDS`. Both checks. Silently dropped — no error message is sent back to the originator. This is non-negotiable for a bot that will be exposed to the public Telegram network.

---

## 6. Project Layout and Tooling

### Directory layout

```
Project-0/
├── .env                        # bot tokens + anthropic key, gitignored
├── .env.example                # committed, no real values
├── .gitignore
├── README.md                   # "how to run the skeleton" only
├── pyproject.toml
├── uv.lock                     # committed
├── data/
│   └── store.db                # created on first run, gitignored
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-04-13-multi-agent-skeleton-design.md
├── src/
│   └── project0/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── store.py            # trust boundary
│       ├── envelope.py
│       ├── orchestrator.py
│       ├── telegram_io.py
│       └── agents/
│           ├── __init__.py
│           ├── registry.py
│           ├── manager.py
│           └── intelligence.py
└── tests/
    ├── conftest.py             # in-memory SQLite fixture
    ├── test_store.py
    ├── test_orchestrator.py
    ├── test_envelope.py
    └── test_end_to_end.py
```

### Tooling decisions

- **Python 3.12.** Widely available, good asyncio ergonomics, avoids 3.13's still-settling ecosystem.
- **`uv`** as package manager and runner. Fast, lockfile built in, replaces venv juggling.
- **`src/` layout** to prevent accidental imports of `project0` from the repo root during tests.
- **One module per concern.** `store.py` is the trust boundary. `orchestrator.py` is the routing algorithm. `telegram_io.py` is I/O only. If any of these grows past ~300 lines the skeleton has gone wrong.
- **Explicit agent registration** in `agents/registry.py`. No decorator-based auto-discovery — explicit registration is easier to debug.

### Dependencies

```toml
[project]
dependencies = [
    "python-telegram-bot >= 21.0",
    "anthropic >= 0.40",             # imported; unused in skeleton
    "pydantic >= 2.0",               # fake Telegram update models in tests; envelope itself is a plain @dataclass
    "python-dotenv >= 1.0",
]

[dependency-groups]
dev = [
    "pytest >= 8.0",
    "pytest-asyncio >= 0.24",
    "ruff >= 0.6",
    "mypy >= 1.10",
]
```

**Explicitly NOT added in the skeleton:** `langgraph`, `sqlalchemy`, `fastapi`, `letta`. Each of these belongs to a specific later sub-project.

### Config and secrets

Single `.env` file loaded by `config.py`:

```
TELEGRAM_BOT_TOKEN_MANAGER=xxx
TELEGRAM_BOT_TOKEN_INTELLIGENCE=xxx
TELEGRAM_ALLOWED_CHAT_IDS=-100123456789         # comma-separated
TELEGRAM_ALLOWED_USER_IDS=12345678              # comma-separated
ANTHROPIC_API_KEY=sk-ant-xxx                    # validated at startup, unused
STORE_PATH=data/store.db
LOG_LEVEL=INFO
```

**The allow-list environment variables are required, not optional.** `config.py` raises at startup if they are empty.

### LLM provider posture

The Anthropic client is imported, instantiated, and its key is validated at startup — but no agent in the skeleton calls it. The agent base class holds an `anthropic.AsyncAnthropic` instance as a field so real agents in sub-project 6 inherit it for free. Two reasons to pay this small cost now:

1. **Fail fast on config.** A missing or malformed key is caught immediately, not on the first message of the next sub-project.
2. **Reserve the integration seam.** When stubs are replaced with real agents later, no plumbing change is needed — only the function body changes.

The provider interface is intentionally thin so that a future migration from Anthropic to a local model (via Ollama or similar) is a configuration change, not a refactor. Do not use Anthropic-specific features in agent code that cannot be replicated locally unless the benefit is clearly worth the lock-in.

### Logging

Plain stdlib `logging`, one handler to stderr, level controlled by `LOG_LEVEL`. No structured logging — the `messages` table is the structured log. Stdlib logging is for operational noise ("bot started," "caught dedup race," allow-list rejections), not for message traces.

### Testing strategy

- Most tests use an in-memory SQLite (`:memory:`) via a pytest fixture.
- **`test_store.py`**: verifies that an `AgentMemory` instance scoped to `"manager"` cannot see rows written by an instance scoped to `"intelligence"`. Verifies the dedup unique-constraint behavior.
- **`test_orchestrator.py`**: uses fake agents (plain functions) and fake Telegram-update pydantic models. Exercises the full routing pipeline with no network.
- **`test_envelope.py`**: serialization round-trip for every `routing_reason` value.
- **`test_end_to_end.py`**: the single most important test. Simulates the "any news today?" flow end-to-end. Asserts the exact four-envelope tree shape with correct `parent_id` links appears in the `messages` table.
- **No tests hit real Telegram.** Manual smoke test with real bots in a real group chat is the only way to catch end-to-end Telegram integration bugs. This is documented in the README and encoded as acceptance criterion D.

### Git and CI

- Initialize git repo on project start. Commit the spec doc as first commit.
- No CI in the skeleton — premature for a single developer. Revisit when someone else touches the code.

---

## 7. Scope, Non-Goals, and Acceptance Criteria

### In scope — the skeleton must do all of these

1. `uv run python -m project0.main` starts the process, opens or creates `data/store.db`, starts two Telegram bot pollers (Manager + Intelligence), and idles on the asyncio loop.
2. Accept a user message in a Telegram group on the allow-list and route it through the orchestrator.
3. Default-route to Manager when no @mention is present and no prior focus exists.
4. Honor @mentions to switch focus to the mentioned agent.
5. Sticky focus persists across subsequent no-@mention messages in the same group, until the user @mentions someone else or Manager delegates.
6. Manager delegation triggered by the `"news"` keyword rule produces the full four-envelope tree: original user message, internal forward to Intelligence, visible handoff message from Manager's bot, Intelligence's reply.
7. The full envelope tree is persisted to `messages` with correct `parent_id` links. The tree can be reconstructed via `SELECT * FROM messages WHERE parent_id = ... ORDER BY ts`.
8. Memory isolation is enforced through the `AgentMemory` API — a stub that attempts to access another agent's memory fails with an exception. This is tested, not just asserted.
9. Delegation authority is enforced — if any non-Manager agent returns `delegate_to=...`, the orchestrator raises `RoutingError` and does not dispatch.
10. Multi-bot polling is deduplicated via the UNIQUE constraint on `(source, telegram_chat_id, telegram_msg_id)`. The same Telegram message seen by N bots produces exactly one envelope row.
11. DMs to either bot are routed directly to that agent with `routing_reason="direct_dm"`. Group focus state is not touched.
12. Unauthorized `chat_id` or `user_id` values are silently rejected by the allow-list check before routing.
13. `uv run pytest` passes green, including `test_end_to_end.py`.

### Out of scope — the skeleton must not do any of these

1. No real LLM calls. The Anthropic client is imported and its key validated, but no agent invokes it.
2. No real Manager intelligence. Routing is the `"news"` keyword rule.
3. No WebUI, no HTTP server, no REST/JSON endpoints. Telegram is the only interface.
4. No LangGraph anywhere.
5. No user profile, no knowledge base, no source cache, no audit scores.
6. No Secretary, Learning, or Supervisor agent stubs. Only Manager and Intelligence are wired.
7. No media handling. Stickers / photos / voice are logged as `[non-text]` and routed like text.
8. No rate limiting, retries, or circuit breakers on Telegram API calls. Errors are logged and the message is dropped.
9. No multi-user support. Single hardcoded user ID via the allow-list.
10. No production deployment. Runs on your machine. No Docker, no systemd, no cloud.
11. No schema migrations. The schema is created by `store.py` on startup; schema changes during skeleton phase mean deleting `data/store.db` and starting over. Documented in the README.
12. No observability beyond stdlib logging. No metrics, no tracing, no Prometheus. The `messages` table is the only observability surface.

### Acceptance criteria — the skeleton is done when all of the following hold at once

- **A.** `uv run pytest` passes with zero failures.
- **B.** `uv run mypy src/project0` passes with zero errors.
- **C.** `uv run ruff check src tests` passes with zero warnings.
- **D.** Manual smoke test in a real Telegram group containing the user and both bots:
  - **D.1.** Sending `hello` yields `[manager-stub] acknowledged: hello` from Manager's bot.
  - **D.2.** Sending `any news today?` yields a visible handoff message from Manager's bot, followed by `[intelligence-stub] acknowledged: any news today?` from Intelligence's bot.
  - **D.3.** Sending a follow-up `what else?` (no @mention) after D.2 routes to Intelligence, proving sticky focus.
  - **D.4.** Sending `@manager what's up` routes to Manager, proving @mention overrides focus, and subsequent no-@mention messages route to Manager again.
  - **D.5.** DMing Intelligence's bot with `hi there` yields `[intelligence-stub] acknowledged: hi there` without touching group focus state.
- **E.** The `messages` table after those smoke tests contains envelope trees that match the design's four-envelope structure for D.2, inspected manually with `sqlite3 data/store.db`.

If any of A–E fails, the skeleton is not done. No "close enough."

---

## 8. Design Decisions Worth Flagging for Future Sub-Projects

These are explicit commitments that later sub-projects should not casually break:

1. **Messages are first-class storage; audit is derived.** The Supervisor sub-project must not add a parallel `audit_events` write path. Enrich the envelope schema instead.
2. **Delegation authority is Manager-only.** Later sub-projects must not allow other agents to delegate among themselves unless this rule is explicitly reconsidered in a spec.
3. **`store.py` is a trust boundary.** Any code that bypasses its API and runs raw SQL against `agent_memory` breaks the isolation guarantee.
4. **The envelope's `source` field describes origin, not delivery path.** Do not add `telegram_outbound` as a fourth source value — outbound delivery is a `telegram_io` concern, not an envelope concern.
5. **The orchestrator stays plain Python.** LangGraph lives inside individual agents, not at the routing layer.
6. **Per-agent Telegram bot identity is a product requirement.** The final product needs direct 1:1 DM chat with each agent, and that implies distinct bot tokens. Any future design that centralizes on a single bot (e.g. "let's just use one bot with `/commands`") violates this.
7. **Manager owns calendar and appointment notes directly.** Secretary is a conversational companion, not a scheduler. The Manager↔Intelligence delegation in the skeleton (triggered by the `"news"` keyword) is contrived scaffolding to exercise the routing contract — the real product's Manager will make delegation decisions via LLM tool use over a much richer routing space, and it will handle calendar notes itself without delegating.
8. **Manager posts visible handoff messages when delegating.** Silent delegation was rejected because visible handoffs enable the user to reference specific messages when reporting issues to Supervisor.

---

## 9. What Comes After

Once the skeleton is accepted, the next sub-projects can be tackled in any order that matches priorities, but a sensible sequence is:

1. **First real agent — Secretary** (sub-project 6a). Replace the conversational surface with a real LLM-backed Secretary. Secretary is deliberately picked as the first real agent because it needs **only character design and conversation** — no tool use, no routing schemas, no external APIs. That isolates the LLM integration seam: if something breaks, it is the Anthropic plumbing itself (client construction, prompt assembly, streaming, error handling, token budgets, cache priming), not a tool-schema bug or a tool-response parse error. Secretary's scope is pure prompt engineering plus the agent-side read of `agent_memory` for persona/state continuity. Note that in the skeleton Secretary is not even wired as a stub — adding it at this step means registering a new agent in `agents/registry.py`, adding a bot token to `.env`, and writing the agent module. The orchestrator does not need to change.
2. **First real agent with delegation — Manager** (sub-project 6b). Replace `manager_stub` with a real LLM-backed Manager. This is where tool use enters the picture — Manager uses Anthropic tool calls to decide between handling directly and delegating to another agent. Design prompt, tool schemas, token budgets, failure handling, and the "what happens when the model hallucinates a nonexistent agent" case. Sub-project 6a's groundwork means the LLM plumbing is already proven; only the tool-use layer is new here.
3. **First real agent with external data — Intelligence** (sub-project 6c). Real Twitter/X ingestion, source cache (Layer F), de-duplication, tagging, candidate recommendations. First sub-project to require the tool gateway.
4. **Memory layer hardening** (sub-project 2). User profile layer (Layer A), richer blackboard semantics, possibly Postgres migration once concurrent writers (WebUI + agents) become a real concern.
5. **WebUI control panel** (sub-project 4). Reads `messages`, renders trace trees, approvals, config editing.
6. **Remaining agents** — Learning, Supervisor — each with its own spec. Learning is the formal knowledge base writer (Layer D); Supervisor is the audit/evaluation authority built on derived views of the `messages` table.
7. **Tool gateway hardening and audit/metrics pipeline** as cross-cutting concerns.

Each of those gets its own brainstorm → spec → plan → implementation cycle.

### Why Secretary first, concretely

Manager and Intelligence each introduce a new hard problem *on top of* the LLM integration: Manager brings tool use and routing-under-uncertainty, Intelligence brings an external API, a source cache, and data cleaning. If you make either of them the first real agent, a failure during development is ambiguous — "is this an LLM plumbing bug, or a tool-schema bug, or a tool-use prompt bug?" Secretary eliminates that ambiguity. If Secretary misbehaves, the bug is in one of four places: the Anthropic client setup, the system prompt, the message-to-envelope translation, or the agent's own use of `agent_memory`. That is a small enough surface area to debug confidently, and once it is working, it stays working as later agents inherit the same plumbing.

Secretary also happens to be the agent whose character matters most — the user-facing conversational companion — so investing in the prompt iteration loop early is valuable in its own right.
