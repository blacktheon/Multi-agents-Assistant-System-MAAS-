# Sub-Project 6a — Secretary, the First Real LLM Agent

**Date:** 2026-04-13
**Parent project:** Project 0: Multi-agent assistant system
**Predecessor sub-project:** Skeleton (`2026-04-13-multi-agent-skeleton-design.md`)
**Sub-project scope:** Replace zero stubs and add one real LLM-backed agent — Secretary — along with the minimum supporting infrastructure (LLM provider abstraction, listener fan-out in the orchestrator, persona and config files). Secretary is the first agent in the project to make real Anthropic API calls.

---

## 1. Purpose and Framing

The skeleton sub-project locked down the contracts: envelope schema, memory isolation, routing rules, storage schema. It deliberately avoided any LLM calls so the contract layer would be the only thing being validated.

Sub-project 6a is the first to introduce real LLM integration. Secretary is picked as the first real agent for one reason: **its scope is pure prompt engineering plus persona, with no tool use, no external APIs, no routing decisions, and no formal knowledge writes.** That makes it the cleanest possible isolation of the LLM plumbing seam (client construction, prompt assembly, prompt caching, error handling, token budgets, persona-as-config). If something breaks during 6a, the bug is in one of a small set of places — Anthropic SDK setup, system prompt assembly, the agent's own use of `agent_memory`, or the new listener fan-out path — not tangled with tool-schema bugs or external-API failures.

Secretary in the final product is not an administrative scheduler despite the name. It is the user's conversational companion: warm, playful, teasing, supportive, character-driven. It listens to everything happening in the group chat and **opportunistically** chimes in when it has something genuinely worth saying. It also handles direct addressing (group `@mentions`, DMs) and acts as the warm-voice delivery channel for reminders that Manager hands to it.

### Design stance

Secretary in 6a should be **the first thing in this project that feels like a real product**. The skeleton was deliberately stupid (`[stub] acknowledged:` echoes); 6a is the first sub-project that should make you smile when you talk to it. The character work matters from day one.

At the same time, 6a must not leak product code into infrastructure. The LLM provider abstraction, the listener fan-out in the orchestrator, the new `Envelope.payload` field, and the persona/config file split are all infrastructure changes that future sub-projects will inherit. They must be designed to serve every future agent, not just Secretary.

---

## 2. Scope and Non-Goals

### In scope

- A new agent `secretary` registered in `agents/registry.py`.
- A new Telegram bot token `TELEGRAM_BOT_TOKEN_SECRETARY` and a third bot poller in `main.py`.
- A thin LLM provider abstraction (`src/project0/llm/provider.py`) with two implementations: `AnthropicProvider` (real, uses `claude-sonnet-4-6`, prompt caching enabled) and `FakeProvider` (tests).
- A "listener" fan-out path in the orchestrator: every group message goes to its focus target *and* in parallel to all registered listeners. Secretary is the first listener.
- One new `routing_reason` enum value: `"listener_observation"`.
- One new optional `Envelope` field: `payload: dict | None = None`, persisted as a new nullable `payload_json` column on `messages`.
- One new `MessagesStore` method: `recent_for_chat(chat_id, limit) -> list[Envelope]`, used by Secretary to load recent conversation context.
- A `prompts/secretary.md` persona document (in Chinese) and a `prompts/secretary.toml` config file (numeric thresholds, model names, sentinel patterns).
- All four Secretary entry paths working end-to-end: listener observation, group `@mention`, DM, Manager-directed reminder (the last is exercised in tests via a synthetic injected envelope; real Manager won't send these until 6b).
- All replies authored in Chinese.

### Out of scope

- No real Manager. `manager_stub` stays. The Manager-directed reminder path is exercised only via injected envelopes in tests; the real source for those envelopes lands in 6b.
- No tool use. Secretary makes only `complete()` calls; the provider interface intentionally does not yet expose tools.
- No streaming. Telegram doesn't natively stream and the buffering complexity buys nothing for a personal assistant.
- No agent base class. With one real agent there is no shared shape to extract; the base class arrives in 6b when Manager's surface area makes the commonalities visible.
- No retries, rate limiting, or circuit breakers on Anthropic calls. Errors are logged and the message is dropped, matching the skeleton's posture on Telegram errors. Robustness lands in a later cross-cutting sub-project.
- No schema migrations. The `payload_json` column is added by recreating the table at startup; the README will document "delete `data/store.db` when upgrading to 6a."
- No new agents besides Secretary. Learning, Supervisor, and the real Manager all stay where they are.

### What does not change from the skeleton

- The `Envelope` schema, except for the new optional `payload` field and the new `routing_reason` value.
- The `AgentResult` shape.
- The `messages` and `agent_memory` table schemas, except for the `payload_json` column on `messages`.
- The delegation-authority rule: only Manager can delegate. Listeners explicitly cannot delegate; if a listener returns `delegate_to=...`, the orchestrator raises `RoutingError`.
- The dedup mechanism (UNIQUE on `(source, chat_id, msg_id)`).
- Allow-list enforcement.
- The single-process, single-SQLite-file, single-shared-connection model.

---

## 3. LLM Provider Interface

`src/project0/llm/provider.py` — single small module, no inheritance hierarchy.

```python
@dataclass
class Msg:
    role: Literal["user", "assistant"]
    content: str

class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        system: str,           # cached prefix
        messages: list[Msg],   # volatile suffix
        max_tokens: int = 800,
    ) -> str: ...
```

### `AnthropicProvider`

Wraps `anthropic.AsyncAnthropic`. Constructed once in `main.py`'s composition root with the API key from `config.py`. Single model, defaulting to `claude-sonnet-4-6`, override-able via `LLM_MODEL` env var.

The `system` argument is sent as a single content block with `cache_control={"type": "ephemeral"}` so the persona prefix is cached. Volatile per-turn content (the transcript) is sent in the `messages` array and not cached. No streaming, no tool use, no extended thinking.

Returns `response.content[0].text`. Errors are logged and re-raised as `LLMProviderError`; Secretary's caller catches and logs them at the agent boundary, then drops the turn.

### `FakeProvider`

For tests only. Constructed with either:
- a list of canned responses returned in order, or
- a callable `(system: str, messages: list[Msg]) -> str` for tests that need to assert on inputs.

Records every call (`system`, `messages`, `max_tokens`) for later assertion.

### Why this stays thin

- **Prompt caching is a parameter on the call site, not a method on the interface.** Local-model providers will simply ignore the cache flag.
- **No `complete_with_tools` method yet.** Sub-project 6b adds it as a sibling method when Manager actually needs tool use, rather than guessing the shape now.
- **No streaming.** Adding it later is non-breaking (a new `stream()` method on the protocol). The future Ollama swap is a third implementation file with the same `complete()` signature, selected by `LLM_PROVIDER=ollama` env var.

---

## 4. Listener Fan-Out in the Orchestrator

The skeleton's orchestrator dispatches one envelope to one agent (focus or `@mention`). 6a adds a parallel listener path.

### Registry split

`agents/registry.py` exposes two separate dicts:

```python
agent_registry: dict[str, AgentCallable]      # routing targets
listener_registry: dict[str, AgentCallable]   # passive observers
```

In 6a:

```python
agent_registry = {
    "manager": manager_stub,
    "intelligence": intelligence_stub,
    "secretary": secretary.handle,    # also a routing target for @mention/DM
}

listener_registry = {
    "secretary": secretary.handle,    # same callable, observation entry path
}
```

Secretary appears in both registries because it has both addressed paths (target) and observation paths (listener). The same `handle()` method receives both kinds of envelopes and branches on `routing_reason`. Supervisor in 6f will register only in `listener_registry`.

### Fan-out pipeline

After steps 1–8 of the existing skeleton routing pipeline (dedup, allow-list, classify, mention parse, focus resolve, persist envelope, dispatch focus target, handle focus result), step 9 is added:

```
(9) listener fan-out
    if original envelope.source == "telegram_group":
        for listener_name, listener_fn in listener_registry.items():
            if listener_name == focus_target_name:
                continue          # already dispatched as the focus target
            sibling_env = Envelope(
                parent_id      = original_user_envelope.id,
                source         = "internal",
                from_kind      = "system",
                from_agent     = None,
                to_agent       = listener_name,
                body           = original.body,
                routing_reason = "listener_observation",
                payload        = None,
                ...
            )
            persist(sibling_env)
            result = await listener_fn(sibling_env)
            if result.delegate_to is not None:
                raise RoutingError(
                    "listeners cannot delegate"
                )
            if result.reply_text is not None:
                outbound = build_outbound(
                    parent_id = sibling_env.id,
                    from_agent = listener_name,
                    body       = result.reply_text,
                )
                persist(outbound)
                bot = bot_senders[listener_name]   # listener's own bot
                await bot.send(chat_id, result.reply_text)
```

### Listener fan-out semantics

- **Group only.** Listener fan-out fires only when `source == "telegram_group"`. DMs and internal envelopes do not trigger it. A user DMing Manager's bot does not produce a Secretary observation envelope — that would be Secretary eavesdropping on private conversation, violating the parent spec's section 3.2 visibility rules.
- **Skip-self.** If the listener name equals the focus target name (e.g., user types `@secretary` in group), the fan-out skips that listener so Secretary isn't double-dispatched on the same message.
- **Sequential.** In 6a, listeners run sequentially after focus dispatch. The listener path is dominated by the cooldown gate which short-circuits to zero work for most messages, and on the rare LLM call the user is not actively waiting on Secretary's chime-in. Parallelization is a later optimization if measurements ever show it matters.
- **Parent linkage.** The listener-observation envelope's `parent_id` points at the original user message. Any reply Secretary then sends has its `parent_id` pointing at the listener-observation envelope, not the original user message. This preserves the audit-tree shape and lets the WebUI later distinguish "Secretary chimed in passively" from "Manager delegated to Secretary."
- **No focus mutation.** Listener observation never updates `chat_focus`. Secretary chiming in does not steal the conversation; the user is still talking to whoever they were talking to before.
- **No delegation.** Listeners returning `delegate_to=...` is a programming error and raises `RoutingError`. This is enforced at the orchestrator boundary, not on the honor system.
- **Replies optional.** A listener returning `AgentResult(reply_text=None, ...)` has observed but chosen not to speak. No outbound is sent and no bot lookup is done. Future listeners without a user-facing Telegram bot (e.g., Supervisor in 6f) will always return `reply_text=None` and do their work via side effects — writing to audit storage, updating scores, etc. The fan-out path does not require every listener to have a bot token.

### Envelope schema additions

```python
@dataclass
class Envelope:
    # ... all existing fields unchanged ...
    payload: dict | None = None    # NEW, optional, default None

    routing_reason: Literal[
        "direct_dm",
        "mention",
        "focus",
        "default_manager",
        "manager_delegation",
        "outbound_reply",
        "listener_observation",    # NEW
    ]
```

`payload` is JSON-serialized into a new `payload_json TEXT NULL` column on `messages`. Used in 6a only for Manager-directed reminder envelopes (`payload={"kind": "reminder_request", ...}`); future sub-projects will use it for tool-call structured data, briefings, and so on.

---

## 5. The Secretary Agent

`src/project0/agents/secretary.py`. One file, one class.

```python
class Secretary:
    def __init__(
        self,
        llm: LLMProvider,
        memory: AgentMemory,             # scoped to "secretary"
        messages_store: MessagesStore,
        config: SecretaryConfig,         # loaded from secretary.toml
        persona: SecretaryPersona,       # parsed from secretary.md
    ): ...

    async def handle(self, env: Envelope) -> AgentResult: ...
```

`handle()` branches on `env.routing_reason`:

| `routing_reason` | Path | Cooldown? | Persona section |
|---|---|---|---|
| `listener_observation` | passive group observer | yes | listener mode |
| `mention` | user `@secretary` in group | no | group-addressed mode |
| `focus` | sticky focus is on Secretary in group | no | group-addressed mode |
| `direct_dm` | user DMs Secretary's bot | no | DM mode |
| `manager_delegation` with `payload.kind == "reminder_request"` | Manager-directed warm reminder | no | reminder mode |
| anything else | defensive no-op | — | — |

All five paths share one underlying helper that assembles `(system_prompt, messages, max_tokens)` and calls `llm.complete()`. The differences are: which persona section is active, what cooldown rules apply, what context is loaded.

### Path 1 — Listener (passive group observer)

1. **Cooldown check** (pure code, no LLM):
   - Read `agent_memory["last_reply_at_<chat_id>"]` (ISO timestamp; default = epoch).
   - Read `agent_memory["msgs_since_reply_<chat_id>"]` (int; default = 0) and `agent_memory["chars_since_reply_<chat_id>"]` (int; default = 0).
   - Increment: `msgs += 1`, `chars += weighted_len(env.body)`. Write back.
   - If `now - last_reply_at < t_min_seconds` → return no-op.
   - If `msgs < n_min_messages` → return no-op.
   - If `chars < l_min_weighted_chars` → return no-op.
   - All three thresholds must be exceeded. The `weighted_len` function counts CJK characters as 3 and ASCII as 1, so Chinese conversations trip the gate at the right semantic density (see section 7).
2. **Context load:** call `messages_store.recent_for_chat(chat_id, limit=transcript_window)` and format as a transcript with speaker labels (`user:`, `[other-agent: manager]:`, `secretary:`, etc.).
3. **One Sonnet call.** System prompt = `[character_core, listener_mode_section]` (cached prefix). User message = transcript.
4. **Skip detection.** Apply `strip().lower()` to the response. If the result either equals any pattern in `config.skip_sentinels.patterns` or **starts with** such a pattern followed by whitespace, punctuation, or end-of-string, treat it as a skip: log `secretary considered, passed` and return no-op. **Do not reset the cooldown counters** — an inspired moment 30 seconds later still gets a chance. The starts-with rule is defensive against the model producing `[skip] 这波没啥好说的` — the presence of trailing reasoning still means "skip."
5. **Otherwise:** return `AgentResult(reply_text=response)`. Reset all three cooldown counters: `last_reply_at = now`, `msgs = 0`, `chars = 0`.

### Path 2 — Group `@mention` or sticky focus

No cooldown gate. Load same transcript context. System prompt = `[character_core, group_addressed_mode_section]`. One Sonnet call, return the reply. Reset cooldown counters as a courtesy so the listener path doesn't immediately fire again on the next message.

### Path 3 — DM

No cooldown gate. Context = `messages_store.recent_for_chat(chat_id, limit=transcript_window)` for this DM chat only. System prompt = `[character_core, dm_mode_section]`. One Sonnet call, return the reply. Cooldown counters are per-`chat_id`, so DM activity does not affect group cooldown state.

### Path 4 — Manager-directed reminder

Triggered by an internal envelope with `from_agent="manager"`, `to_agent="secretary"`, `routing_reason="manager_delegation"`, and `payload={"kind": "reminder_request", "appointment": "...", "when": "...", ...}`.

In 6a, Manager is still a stub and will not actually send these. The path is exercised by tests via a synthetic injected envelope, and by a small `scripts/inject_reminder.py` helper for manual smoke testing.

No cooldown gate. System prompt = `[character_core, reminder_mode_section]`. The user message is built from `payload` fields ("Manager has asked you to remind the user about: `<appointment>`, scheduled `<when>`. Phrase warmly. Do not invent details Manager did not give you."). One Sonnet call, return the reply.

---

## 6. Persona and Config Files

Both files live under `prompts/` next to the source tree. Editing them does not require touching Python; the dev loop is "tweak persona file → restart process → test in real Telegram." Both are committed to git and reviewed in PRs alongside code changes.

### `prompts/secretary.md` — Chinese persona document

Single Markdown file, parsed at startup by splitting on `# 模式：` headers. Five sections:

```markdown
# 秘书 — 角色设定
（character core: voice, name, tone, do's and don'ts, written in Chinese）

# 模式：群聊旁观
（listener mode: instructions when fan-out path fires, including the
 literal `[skip]` sentinel rule, "don't force it" guidance, transcript
 format documentation）

# 模式：群聊点名
（group-addressed mode: user @mentioned secretary or focus is secretary）

# 模式：私聊
（DM mode: more open, more personal, still in character）

# 模式：经理委托提醒
（reminder mode: Manager has asked you to remind the user; phrase
 warmly; do not invent details Manager did not give you）
```

Character core is always present in the system prompt, so it stays in the cached prefix. The mode section is also stable per path, so the entire system prompt caches cleanly.

The character core is authored in Chinese because models maintain character voice more reliably in the language the system prompt is written in. A few system-level instructions inside the mode sections may stay in English (e.g., the literal `[skip]` sentinel rule) — Claude handles code-switched system prompts fine.

### `prompts/secretary.toml` — numeric and behavioral config

```toml
[cooldown]
t_min_seconds = 90
n_min_messages = 4
l_min_weighted_chars = 200      # weighted_len: CJK chars count 3, ASCII 1

[context]
transcript_window = 20          # last N messages loaded for context

[llm]
model = "claude-sonnet-4-6"
max_tokens_reply = 800
max_tokens_listener = 400       # listener replies should be short

[skip_sentinels]
# Listener-mode response is treated as "skip" if, after strip+lower,
# it matches any of these. Defensive against the model translating
# the sentinel or wrapping it in full-width brackets.
patterns = [
    "[skip]",
    "[跳过]",
    "【skip】",
    "【跳过】",
    "（skip）",
    "（跳过）",
]
```

Loaded once at startup into a `SecretaryConfig` dataclass. Changes require a process restart. Validation: missing keys raise at startup, not at first message.

---

## 7. Chinese Language Considerations

Anthropic Sonnet handles Chinese well; the model itself is not a concern. The concrete issues that arise from making Secretary speak Chinese:

1. **The `[skip]` sentinel must be defended against translation and full-width brackets.** The model, generating in Chinese, will be tempted to output `[跳过]`, `【skip】`, `（跳过）`, etc. Mitigation: in the listener-mode persona section, pin the sentinel as the literal ASCII string `[skip]` with explicit instructions; on the parsing side, accept multiple variants defensively via the `skip_sentinels.patterns` config list. Belt and suspenders.
2. **Cooldown character thresholds need Chinese-aware counting.** A 200-character English message is ~40 words; a 200-character Chinese message is ~130–150 words of equivalent content. Raw `len()` would make Chinese conversations trip the gate ~3x slower than English ones, which is wrong — Chinese carries more meaning per character, so it should trip the gate **faster**, not slower. Solution: a `weighted_len(s) -> int` helper that counts each CJK character as 3 and each ASCII character as 1. The default `l_min_weighted_chars = 200` then means roughly the same conversational density in either language.
3. **Persona prompt is authored in Chinese.** Models maintain character voice more reliably in the language the system prompt is written in. `prompts/secretary.md` is a Chinese document, not an English document translated at runtime.
4. **`@mention` parsing is unaffected.** Telegram usernames are ASCII (`@secretary`), so the existing mention parser works. Mentioning agents by Chinese display name (`@秘书`) is a separate parser concern and is out of scope for 6a.
5. **Future local-model swap must pick a Chinese-capable model.** When the Anthropic→local-model migration happens, the local model needs Chinese quality close to Sonnet. Qwen 2.5 / Qwen 3 family is the obvious candidate; Llama 3.x is noticeably weaker in Chinese. This must be flagged when the local-model swap sub-project is brainstormed — do not pick on English benchmarks alone.

Things that are **not** affected by Chinese: SQLite (UTF-8 by default), Telegram (UTF-8 throughout), Python `len()` on strings (counts characters not bytes), prompt caching (operates on tokens, language-neutral), the `messages` table schema.

---

## 8. Storage Changes

Three changes to `src/project0/store.py`:

1. **`messages` table:** add `payload_json TEXT` nullable column.
   ```sql
   ALTER not used — the skeleton has no migration framework. The table
   is recreated on startup. README will document "delete data/store.db
   when upgrading to 6a." Consistent with the skeleton's "no migrations"
   non-goal.
   ```
2. **`Envelope` dataclass:** add `payload: dict | None = None` field, with `json.dumps`/`json.loads` in `store.append_message()` and `store.fetch_messages()`.
3. **`MessagesStore.recent_for_chat(chat_id: int, limit: int) -> list[Envelope]`** — new method. Returns the most recent envelopes for the given Telegram `chat_id`, oldest-first, for transcript loading. Implementation: `SELECT * FROM messages WHERE telegram_chat_id = ? ORDER BY ts DESC LIMIT ?`, then reverse in Python before returning.

`agent_memory` is unchanged. Secretary uses the existing `get`/`set`/`delete` API for cooldown counters, last-reply timestamps, and any persona state it accumulates (e.g., notes about the user that come up in conversation). Per-chat namespacing is done by the agent in key naming (`last_reply_at_<chat_id>`), not by the store.

---

## 9. Project Layout Additions

```
Project-0/
├── prompts/                          # NEW
│   ├── secretary.md                  # Chinese persona document
│   └── secretary.toml                # numeric/behavioral config
├── scripts/                          # NEW
│   └── inject_reminder.py            # manual smoke-test helper for path 4
├── src/project0/
│   ├── llm/                          # NEW
│   │   ├── __init__.py
│   │   └── provider.py               # LLMProvider, AnthropicProvider, FakeProvider
│   └── agents/
│       └── secretary.py              # NEW
└── tests/
    ├── test_llm_provider.py          # NEW
    ├── test_secretary.py             # NEW
    └── test_orchestrator_listener_fanout.py    # NEW
```

No new top-level dependencies — `anthropic` is already in `pyproject.toml` from the skeleton. `tomllib` is in the stdlib (Python 3.12) for parsing `secretary.toml`.

---

## 10. Configuration

New env vars added to `.env` and `.env.example`:

```
TELEGRAM_BOT_TOKEN_SECRETARY=xxx
LLM_PROVIDER=anthropic              # anthropic | fake (tests) | ollama (future)
LLM_MODEL=claude-sonnet-4-6         # override model id
```

`config.py` adds `TELEGRAM_BOT_TOKEN_SECRETARY` to its required-token list (the skeleton's recent refactor derives required tokens from `AGENT_SPECS`, so this is automatic once Secretary is registered).

`ANTHROPIC_API_KEY` was already required and validated at startup by the skeleton — no change.

---

## 11. Testing Strategy

Three new test files. All existing skeleton tests must stay green.

### `tests/test_llm_provider.py`

- `FakeProvider` with a list of canned responses returns them in order.
- `FakeProvider` with a callable produces dynamic responses and records calls.
- `AnthropicProvider` constructs without making any network call (the SDK client is mocked at the import boundary).
- `AnthropicProvider.complete()` passes `cache_control` on the system block (verified via the mocked SDK call args).
- **No test hits the real Anthropic API.** Real-API verification is the manual smoke test in section 12.

### `tests/test_secretary.py`

Unit tests with `FakeProvider`. Covers all four paths plus the cooldown weighting helper.

- Cooldown not yet open → no LLM call, returns no-op.
- Cooldown open + fake response `"[skip]"` → no reply, **cooldown counters not reset**.
- Cooldown open + fake response `"嘿你今天怎么这么努力"` → reply persisted, **cooldown counters reset**.
- Cooldown open + fake response `"【跳过】"` → treated as skip via the sentinel-pattern list.
- @mention path → always replies, no cooldown check.
- DM path → always replies, no cooldown check, separate cooldown namespace from group (verified by interleaving group and DM messages and asserting counters do not contaminate each other).
- Manager-directed reminder: synthetic envelope with `from_agent="manager"`, `routing_reason="manager_delegation"`, `payload={"kind": "reminder_request", "appointment": "项目评审", "when": "明天下午3点"}` produces a reply that incorporates the reminder content.
- `weighted_len("hello")` → 5; `weighted_len("你好")` → 6; `weighted_len("hello 你好")` → 12. Direct unit test of the weighting function.
- `agent_memory` durability: write a cooldown counter, construct a fresh `Secretary` instance against the same store, verify the counter is read back correctly. (The cooldown must survive process restart in the real product.)

### `tests/test_orchestrator_listener_fanout.py`

Extends the existing orchestrator tests with the new fan-out behavior.

- Group message → focus target receives the envelope AND Secretary listener receives a `listener_observation` sibling envelope. Both persisted with correct `parent_id` links pointing at the original user message.
- DM → only the focus target (the DM'd bot) is invoked. No listener fan-out fires.
- Listener returning `delegate_to=...` → orchestrator raises `RoutingError`.
- Listener that is itself the focus target (e.g., user types `@secretary` in group) → not double-dispatched; only the focus path fires; no `listener_observation` envelope is created.
- Listener reply persistence: when the listener returns `reply_text=...`, the outbound envelope's `parent_id` points at the listener-observation envelope, not at the original user message.

---

## 12. Acceptance Criteria

The skeleton's A–E criteria still apply (pytest, mypy, ruff, manual Telegram smoke, messages-table inspection). 6a adds:

- **F.** `uv run pytest` passes including all new Secretary, listener, and provider tests. Zero failures.
- **G.** Manual smoke test in a real Telegram group with all three bots (Manager, Intelligence, Secretary) present, with a real `ANTHROPIC_API_KEY`:
  - **G.1.** Send several short messages over a few minutes; verify Secretary stays silent until the cooldown opens.
  - **G.2.** Once the cooldown is open, verify Secretary either chimes in (in Chinese, in character) or stays silent (LLM returned a skip sentinel). Repeat several times to observe both outcomes.
  - **G.3.** Send `@secretary 你好` in the group — verify immediate Chinese reply with no cooldown gating.
  - **G.4.** DM Secretary's bot directly with `你今天怎么样` — verify a reply with the more personal DM tone.
  - **G.5.** Run `uv run python scripts/inject_reminder.py "项目评审" "明天下午3点"` — verify Secretary delivers a warm Chinese reminder that incorporates those details.
  - **G.6.** Inspect the `messages` table with `sqlite3 data/store.db`. Verify: one `listener_observation` envelope per group message; one `outbound_reply` from Secretary per actual chime-in; correct `parent_id` chains; the Manager-directed reminder envelope carries a non-null `payload_json`.
- **H.** Prompt cache hit rate visible in Anthropic API logs is ≥ 70% after a handful of turns. This is a sanity check that caching is wired correctly, not a hard SLA.

If any of A–H fails, 6a is not done.

---

## 13. Decisions Worth Flagging for Future Sub-Projects

1. **Listeners are a first-class concept in the orchestrator.** Supervisor in 6f registers as a listener with a one-line addition to `listener_registry`; do not invent a parallel observation mechanism.
2. **`Envelope.payload` is the structured-extension field.** Future sub-projects with new envelope kinds (Manager tool calls, Intelligence briefings, Learning ingestion requests) put their structured data here, not in new top-level envelope fields.
3. **The provider interface stays thin.** When 6b adds tool use, add a sibling `complete_with_tools` method rather than overloading `complete`. When streaming becomes useful, add a sibling `stream` method. Resist the urge to make `complete` a swiss-army knife.
4. **Cooldown counters live in `agent_memory`, not in process state.** A restart should not reset Secretary's "I just spoke" memory. This is a small but real test of the `agent_memory` durability promise that future agents will inherit.
5. **Persona and config files are part of the product, not infrastructure.** They are versioned in git, reviewed in PRs, and edited as carefully as code. Future agents (Manager, Intelligence, Learning, Supervisor) follow the same `prompts/<agent>.md` + `prompts/<agent>.toml` convention.
6. **All agent voices default to Chinese.** Future agents author their persona prompts in Chinese as well. The local-model migration sub-project must pick a Chinese-capable model — Qwen family is the current obvious candidate.
7. **No agent base class yet.** With one real agent there is no shared shape to extract. The base class arrives in 6b when Manager's surface area makes the commonalities visible. Premature abstraction here would lock in the wrong shape.
8. **Listener fan-out is sequential in 6a.** If measurements ever show it matters, parallelize via `asyncio.gather`. Not before.

---

## 14. What Comes After

6b — first real agent with delegation (Manager). Builds on 6a's LLM provider abstraction and adds the `complete_with_tools` method. Replaces `manager_stub` with a real LLM-backed Manager that decides between handling directly and delegating via Anthropic tool use. The Manager-directed reminder path Secretary already supports finally has a real source.

6c — first real agent with external data (Intelligence). Real Twitter/X ingestion, source cache (Layer F), de-duplication, tagging.

6d onward — Learning, Supervisor, memory-layer hardening, WebUI, tool-gateway hardening. Each gets its own brainstorm → spec → plan → implementation cycle.
