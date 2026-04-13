# Sub-project 6c — Manager agent and pulse primitive

**Date:** 2026-04-14
**Status:** Design approved, ready for implementation plan
**Depends on:** 6a (multi-agent skeleton + Secretary), 6b (Google Calendar client)

---

## 1. Context and scope

Sub-project 6c replaces the Manager stub (`manager_stub` in `src/project0/agents/manager.py`, a one-rule `"news" → intelligence` substring check) with a real LLM-backed agent that:

- uses Anthropic tool-use to call the Google Calendar client landed in 6b
- delegates to Secretary (for reminders) and Intelligence (for briefings) through the existing `AgentResult.delegate_to` contract
- can be triggered by a new **pulse** primitive: a generic scheduled wake-up mechanism that fires an envelope at a target agent on a configurable interval

6c also lands the Chinese persona files (`prompts/manager.md` + `prompts/manager.toml`) and the composition-root wiring in `main.py`.

### In scope
- Manager as a real `LLMProvider`-backed agent with an agentic tool-use loop
- A `complete_with_tools` method on `LLMProvider` plus tool schema types
- A generic, domain-agnostic pulse primitive (config loader + scheduler task + orchestrator entry point + envelope kind)
- One concrete pulse entry (`check_calendar`) wired into Manager
- Full test coverage at the unit and orchestrator level (no new live-API tests)

### Out of scope (explicitly deferred)
- Governance and approval flows (§10.3 of the master doc) — Manager's persona tells the model to propose before executing, but no tool enforces it
- Shared Blackboard writes, User Profile reads, Supervisor handoffs
- A listener role for Manager (Manager stays out of `LISTENER_REGISTRY`)
- Cron expressions, jitter, catch-up scheduling, drift correction
- Multi-process pulse deduplication (6c assumes one orchestrator process)
- A generic "any agent can own pulses" path — in 6c only Manager consumes pulses, though the primitive itself is agent-agnostic

---

## 2. Pulse primitive

### 2.1 Envelope additions

`src/project0/envelope.py`:

- `RoutingReason` literal gains `"pulse"`
- `source` literal gains `"pulse"`

A pulse envelope has shape:

```
source            = "pulse"
from_kind         = "system"
from_agent        = None
to_agent          = <target agent name>
routing_reason    = "pulse"
telegram_chat_id  = <resolved from chat_id_env, or None>
telegram_msg_id   = None          # pulse envelopes never collide on the UNIQUE index
received_by_bot   = None
body              = <pulse_name>  # human-readable label, used in logs + transcript
payload           = {"pulse_name": <name>, **entry_payload_dict}
mentions          = []
```

### 2.2 Config schema

Pulse entries live in each agent's existing `prompts/<agent>.toml` as a TOML array-of-tables. Adding a new pulse is append-only; no schema churn.

```toml
[[pulse]]
name         = "check_calendar"
every_seconds = 300
chat_id_env  = "MANAGER_PULSE_CHAT_ID"   # optional; omit = unbound pulse
payload      = { window_minutes = 60 }   # arbitrary pass-through dict
```

Rules:
- `name`: required, non-empty string, unique within the file
- `every_seconds`: required int, `>= 10` (sanity floor; raises `RuntimeError` if lower)
- `chat_id_env`: optional string naming an env var. If present and the env var is missing or non-integer, **loader raises `RuntimeError` at startup** (fail loud)
- `payload`: optional dict, passed through verbatim into the pulse envelope's `payload`
- Missing `[[pulse]]` array = no pulses (empty list, valid)

### 2.3 Loader (`src/project0/pulse.py`)

```python
@dataclass(frozen=True)
class PulseEntry:
    name: str
    every_seconds: int
    chat_id: int | None     # resolved at load time
    payload: dict[str, Any]

def load_pulse_entries(toml_path: Path) -> list[PulseEntry]: ...
```

Loader responsibilities:
- Parse the `[[pulse]]` array from the given TOML file
- Resolve `chat_id_env` → `int` via `os.environ`; missing var or non-int value → `RuntimeError` with the env var name
- Validate `every_seconds >= 10`; otherwise `RuntimeError`
- Validate `name` uniqueness; otherwise `RuntimeError`
- Return a list of immutable `PulseEntry` instances

### 2.4 Scheduler (`src/project0/pulse.py`)

```python
async def run_pulse_loop(
    *, entry: PulseEntry, target_agent: str, orchestrator: Orchestrator
) -> None:
```

Behavior:
- `while True: await asyncio.sleep(entry.every_seconds); await orchestrator.handle_pulse(build_pulse_envelope(entry, target_agent))`
- The first tick fires *after* the first sleep, not at startup — avoids boot-time thundering herd and makes restart-driven loops non-destructive
- Exceptions from `handle_pulse` are logged (`log.exception`) and swallowed; the loop continues. A single failing tick must never kill future ticks.
- `asyncio.CancelledError` propagates (so `TaskGroup` shutdown still works)
- No jitter, no catch-up, no drift correction. If a tick overruns, the next one fires late. YAGNI until we hit the problem.

### 2.5 Orchestrator entry point

`src/project0/orchestrator.py::Orchestrator.handle_pulse(pulse_env: Envelope) -> None`:

- Acquire `self.store.lock`
- Insert `pulse_env` via `self.store.messages().insert(...)`. Pulse envelopes always insert (no `telegram_msg_id`, no UNIQUE collision)
- Release lock
- Assert `persisted.to_agent == "manager"` — 6c only supports Manager as a pulse target (future agents will add their own path when they gain tools)
- Call `AGENT_REGISTRY[persisted.to_agent](persisted)` outside the lock, exactly like the group/DM path
- Reply path: reuse the existing `_emit_reply` helper. If `parent.telegram_chat_id is None`, the reply is persisted as an internal observation with no Telegram send (existing behavior).
- Delegation path: reuse the existing delegation block (visible handoff → internal forward envelope → dispatch target). The internal forward envelope inherits `telegram_chat_id` from the pulse envelope so Secretary's reminder reaches the bound chat.
- Listener fan-out: skipped because `pulse_env.source != "telegram_group"` (existing guard).
- `chat_focus` is not touched. Pulses never change focus.

### 2.6 Why "generic" (not a calendar-specific trigger)

The orchestrator and pulse module know nothing about calendars, events, or reminders. They deliver a named, interval-scheduled envelope with an opaque payload. The domain logic — "is there anything to remind the user about" — lives entirely in Manager, where the calendar tools already live. This mirrors how listener fan-out works: the orchestrator delivers a `listener_observation` envelope and does not know or care what the listener does with it.

---

## 3. LLM tool-use extension

### 3.1 Shared types (`src/project0/llm/tools.py`)

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]   # JSONSchema dict, passed straight through to Anthropic

@dataclass(frozen=True)
class ToolCall:
    id: str                         # Anthropic tool_use id — required for tool_result pairing
    name: str
    input: dict[str, Any]

@dataclass(frozen=True)
class ToolUseResult:
    kind: Literal["text", "tool_use"]
    text: str | None                 # set when kind="text", optional preamble when kind="tool_use"
    tool_calls: list[ToolCall]       # set when kind="tool_use"; may contain >1 call per turn
    stop_reason: str | None          # "end_turn" | "tool_use" | other — for debugging
```

Additional message variants for multi-turn tool-use conversations:

```python
@dataclass
class AssistantToolUseMsg:
    tool_calls: list[ToolCall]
    text: str | None                 # optional preamble text the assistant emitted alongside the tool_use

@dataclass
class ToolResultMsg:
    tool_use_id: str
    content: str                     # stringified result (plain text or JSON)
    is_error: bool = False
```

### 3.2 `LLMProvider` protocol addition

```python
async def complete_with_tools(
    self, *,
    system: str,
    messages: list[Msg | AssistantToolUseMsg | ToolResultMsg],
    tools: list[ToolSpec],
    max_tokens: int = 1024,
) -> ToolUseResult: ...
```

The existing `complete` method is unchanged. Agents that do not need tool use (Secretary today) keep using `complete`.

### 3.3 `AnthropicProvider.complete_with_tools`

- Translates each message variant into Anthropic SDK `MessageParam` blocks:
  - `Msg(role, content)` → plain `{"role": role, "content": content}`
  - `AssistantToolUseMsg` → `{"role": "assistant", "content": [*optional text block, *tool_use blocks]}`
  - `ToolResultMsg` → `{"role": "user", "content": [{"type": "tool_result", "tool_use_id": ..., "content": ..., "is_error": ...}]}`
- Translates `ToolSpec` list → Anthropic `tools` parameter
- Preserves prompt caching on the `system` block (same ephemeral cache_control as `complete`)
- Inspects the response:
  - If `stop_reason == "tool_use"`: collect every `tool_use` block into `ToolCall`s, collect any preceding text block as `text`, return `ToolUseResult(kind="tool_use", ...)`
  - Else: find the first text block, return `ToolUseResult(kind="text", text=..., tool_calls=[])`
  - No text block in a non-tool-use response → `LLMProviderError`
- Wraps SDK exceptions as `LLMProviderError` (same pattern as `complete`)

### 3.4 `FakeProvider.complete_with_tools`

A parallel `tool_responses: list[ToolUseResult]` list, popped in order. Tests can script:

```python
FakeProvider(tool_responses=[
    ToolUseResult(kind="tool_use", text=None, tool_calls=[ToolCall("id1", "calendar_list_events", {...})], stop_reason="tool_use"),
    ToolUseResult(kind="tool_use", text=None, tool_calls=[ToolCall("id2", "delegate_to_secretary", {...})], stop_reason="tool_use"),
    ToolUseResult(kind="text", text="done", tool_calls=[], stop_reason="end_turn"),
])
```

Every call is recorded on `self.tool_calls_log` (parallel to the existing `self.calls` list) so tests can assert on what messages/tools the Manager actually sent.

Exhausted response list → `LLMProviderError` (same as `complete`).

### 3.5 Error handling

- Anthropic transport/API errors: `LLMProviderError` at the provider boundary
- Tool *execution* errors: not the provider's concern. Manager catches them at the dispatch site, feeds a `ToolResultMsg(is_error=True, content=str(e))` back into the next iteration, lets the model see it and either recover or emit a final text apology

---

## 4. Manager agent

### 4.1 Files

- `src/project0/agents/manager.py` — full rewrite (replaces `manager_stub`)
- `prompts/manager.md` — Chinese persona, five sections
- `prompts/manager.toml` — `[llm]`, `[context]`, `[[pulse]]`

### 4.2 Persona sections (`prompts/manager.md`)

Five canonical Chinese headers, parsed the same way Secretary's persona is parsed (exact header match, near-miss detection with suggestion):

1. `# 经理 — 角色设定` — core identity. The manager is the planner, scheduler, and coordination authority. Never inspects other agents' private memory. Proposes plan changes but does not execute them without user confirmation. Speaks warmly but concisely.
2. `# 模式：私聊` — DM mode. User is talking to Manager directly; reply in their voice, use tools when concrete information is needed.
3. `# 模式：群聊点名` — mention / focus / default_manager mode. Group setting, addressed either by mention or because Manager is the default recipient.
4. `# 模式：定时脉冲` — pulse mode. "You were woken by a scheduled pulse named X with payload Y. Use your tools to check whether anything needs user attention in the next window_minutes. If nothing does, emit a short internal note and stop. If something does, delegate to Secretary so she can warmly remind the user."
5. `# 模式：工具使用守则` — tool-use rules. Always read before write on the calendar. Never fabricate event details. After a delegation tool call, stop calling tools and emit final text. Handoff text should be short and in-character.

### 4.3 Config (`prompts/manager.toml`)

```toml
[llm]
model               = "claude-sonnet-4-6"
max_tokens_reply    = 1024
max_tool_iterations = 8

[context]
transcript_window = 20

[[pulse]]
name         = "check_calendar"
every_seconds = 300
chat_id_env  = "MANAGER_PULSE_CHAT_ID"
payload      = { window_minutes = 60 }
```

Loader (`load_manager_config`) mirrors `load_config` in Secretary — missing keys raise `RuntimeError` with the file path and key name.

### 4.4 Class shape

```python
class Manager:
    def __init__(self, *, llm, calendar, memory, messages_store, persona, config):
        self._llm = llm
        self._calendar = calendar
        self._memory = memory
        self._messages = messages_store
        self._persona = persona
        self._config = config
        self._tool_specs = self._build_tool_specs()

    async def handle(self, env: Envelope) -> AgentResult | None:
        reason = env.routing_reason
        if reason == "direct_dm":
            return await self._run_chat_turn(env, self._persona.dm_mode)
        if reason in ("mention", "focus", "default_manager"):
            return await self._run_chat_turn(env, self._persona.group_addressed_mode)
        if reason == "pulse":
            return await self._run_pulse_turn(env)
        log.debug("manager: ignoring routing_reason=%s", reason)
        return None
```

### 4.5 Tool surface (6c)

Six tools, registered as `ToolSpec`s at construction time:

| Tool | Input (JSONSchema sketch) | Result |
|---|---|---|
| `calendar_list_events` | `time_min: iso8601, time_max: iso8601, max_results: int` | JSON array of `CalendarEvent` dicts |
| `calendar_create_event` | `summary, start, end, description?, location?` | created `event_id` |
| `calendar_update_event` | `event_id, summary?, start?, end?, description?, location?` | `"ok"` |
| `calendar_delete_event` | `event_id` | `"ok"` |
| `delegate_to_secretary` | `reminder_text, appointment?, when?, note?` | `"delegated"` (side-effect: queues pending delegation) |
| `delegate_to_intelligence` | `query` | `"delegated"` (side-effect: queues pending delegation) |

No blackboard, memory, user-profile, or supervisor tools in 6c.

### 4.6 Agentic loop

```python
async def _agentic_loop(
    self, *, env: Envelope, system: str, initial_user_text: str, max_tokens: int,
) -> AgentResult:
    turn_state = TurnState()
    messages: list = [Msg(role="user", content=initial_user_text)]

    for _ in range(self._config.max_tool_iterations):
        result = await self._llm.complete_with_tools(
            system=system, messages=messages, tools=self._tool_specs, max_tokens=max_tokens,
        )
        if result.kind == "text":
            return AgentResult(
                reply_text=result.text or "",
                delegate_to=turn_state.delegation_target,
                handoff_text=turn_state.delegation_handoff,
                delegation_payload=turn_state.delegation_payload,
            )
        messages.append(AssistantToolUseMsg(tool_calls=result.tool_calls, text=result.text))
        for call in result.tool_calls:
            content_str, is_error = await self._dispatch_tool(call, turn_state)
            messages.append(ToolResultMsg(tool_use_id=call.id, content=content_str, is_error=is_error))

    raise LLMProviderError(
        f"manager exceeded max_tool_iterations={self._config.max_tool_iterations}"
    )
```

`TurnState` is a local dataclass owned by `_agentic_loop` (not a class attribute). It holds the pending delegation target, handoff text, and payload dict. Because it lives in the local scope, concurrent turns never share state.

### 4.7 Tool dispatch

```python
async def _dispatch_tool(self, call: ToolCall, turn_state: TurnState) -> tuple[str, bool]:
    try:
        if call.name == "calendar_list_events":
            events = await self._calendar.list_events(**self._parse_list_args(call.input))
            return json.dumps([model_to_raw(e) for e in events], ensure_ascii=False), False
        if call.name == "calendar_create_event":
            ev = await self._calendar.create_event(**self._parse_create_args(call.input))
            return ev.event_id, False
        # ... update, delete similar ...
        if call.name == "delegate_to_secretary":
            turn_state.delegation_target = "secretary"
            turn_state.delegation_handoff = f"→ 已让秘书帮你记着 {call.input['reminder_text']}"
            turn_state.delegation_payload = {
                "kind": "reminder_request",
                "appointment": call.input.get("appointment", ""),
                "when":        call.input.get("when", ""),
                "note":        call.input.get("note", ""),
            }
            return "delegated", False
        if call.name == "delegate_to_intelligence":
            turn_state.delegation_target = "intelligence"
            turn_state.delegation_handoff = f"→ 去查一下「{call.input['query']}」"
            turn_state.delegation_payload = {"kind": "query", "query": call.input["query"]}
            return "delegated", False
        return f"unknown tool: {call.name}", True
    except GoogleCalendarError as e:
        return f"calendar error: {e}", True
    except (KeyError, ValueError) as e:
        return f"invalid tool input: {e}", True
```

If the model calls a delegation tool and then keeps calling more tools, Manager honors the **last** delegation. The persona instructs the model to stop after a delegation; if it misbehaves, the behavior is deterministic rather than erroring.

### 4.8 Delegation payload plumbing

Secretary's reminder path (landed in 6a) reads `env.payload["kind"] == "reminder_request"` from the internal forward envelope. The current orchestrator constructs that internal envelope with `body=persisted.body` and no payload. 6c extends:

- `AgentResult` gains an optional field: `delegation_payload: dict[str, Any] | None = None` (default preserves backwards compatibility — Secretary never sets it)
- `Envelope` already has `payload: dict[str, Any] | None`
- Orchestrator delegation block: when building the internal forward envelope, copies `result.delegation_payload` onto the new envelope's `payload`

This is a small, additive change. Secretary's stub/test paths continue to return `delegation_payload=None` and nothing breaks.

### 4.9 Chat turn entry (`_run_chat_turn`)

Builds system from `core + mode_section + tool_use_guide`. Builds `initial_user_text` from:
- recent transcript (via `self._messages.recent_for_chat`, `transcript_window` entries)
- a preface line naming the latest user message

Then calls `_agentic_loop(env, system, initial_user_text, max_tokens=max_tokens_reply)`.

### 4.10 Pulse turn entry (`_run_pulse_turn`)

Builds system from `core + pulse_mode + tool_use_guide`. Builds `initial_user_text` from:
- `pulse_name` and `payload` (JSON-encoded) from `env.payload`
- if `env.telegram_chat_id is not None`: a short transcript window for context

Then calls `_agentic_loop(env, system, initial_user_text, max_tokens=max_tokens_reply)`. Same loop, same dispatch, same delegation plumbing — the only difference is the initial user text.

If the final model text is empty and no delegation was queued, the handler returns `None` (no outbound action at all — just the persisted pulse envelope). If there is text, it is persisted as an internal observation via `_emit_reply` (no Telegram send when `telegram_chat_id is None`).

---

## 5. Composition root wiring (`src/project0/main.py`)

Additions to `_run`:

```python
# 6b Google Calendar client (shared, one per process)
calendar_creds = load_google_credentials(settings.google_creds_path)
calendar = GoogleCalendar(
    credentials=calendar_creds,
    calendar_id=settings.google_calendar_id,
    user_tz=ZoneInfo(settings.user_tz),
)

# Manager (replaces manager_stub)
manager_persona = load_manager_persona(Path("prompts/manager.md"))
manager_cfg     = load_manager_config(Path("prompts/manager.toml"))
manager = Manager(
    llm=llm,
    calendar=calendar,
    memory=store.agent_memory("manager"),
    messages_store=store.messages(),
    persona=manager_persona,
    config=manager_cfg,
)
register_manager(manager.handle)
log.info("manager registered (model=%s)", manager_cfg.model)

# Pulse scheduler tasks — started inside the existing TaskGroup
pulse_entries = load_pulse_entries(Path("prompts/manager.toml"))
log.info("manager pulse entries: %s", [e.name for e in pulse_entries])
```

Inside the existing `async with asyncio.TaskGroup() as tg:` block, alongside bot pollers:

```python
for entry in pulse_entries:
    tg.create_task(run_pulse_loop(entry=entry, target_agent="manager", orchestrator=orch))
```

`AGENT_SPECS` is unchanged. `register_manager` replaces the stub handler in `AGENT_REGISTRY` with `manager.handle`; `registry.py` gains a `register_manager` function symmetric with `register_secretary` if it does not already exist.

`Settings` fields from 6b (`google_creds_path`, `google_calendar_id`, `user_tz`) are already wired. The only new environment dependency is `MANAGER_PULSE_CHAT_ID`, which is read by the pulse loader directly from `os.environ` — not baked into `Settings`, since it is referenced by name from the TOML and may vary per deployment.

---

## 6. Testing strategy

All new tests are unit or orchestrator-level. No new live-API tests — 6a and 6b already cover live Anthropic and live Google Calendar paths.

1. **`tests/llm/test_tools.py`**
   - `FakeProvider.complete_with_tools` replays a scripted sequence
   - Exhausted script → `LLMProviderError`
   - Calls log records every invocation

2. **`tests/llm/test_anthropic_tool_translation.py`**
   - Unit-test the `AnthropicProvider` message translation layer with a mocked SDK client
   - `Msg` → text block, `AssistantToolUseMsg` → assistant with tool_use, `ToolResultMsg` → user with tool_result
   - `ToolSpec` list → Anthropic `tools` parameter
   - Prompt caching preserved on system block

3. **`tests/agents/test_manager_tool_loop.py`**
   - Plain text turn → returns `AgentResult(reply_text=..., delegate_to=None)`
   - `calendar_list_events` → text → reply includes event data
   - Calendar tool raises `GoogleCalendarError` → `is_error` tool_result fed back → model recovers → final text
   - `delegate_to_secretary` tool → `AgentResult` has `delegate_to="secretary"`, correct `delegation_payload`, correct `handoff_text`
   - `delegate_to_intelligence` tool → same for intelligence
   - Exceeding `max_tool_iterations` → `LLMProviderError`
   - Pulse path with no chat_id and empty final text → returns `None`

4. **`tests/agents/test_manager_persona_load.py`**
   - Five-section parser, near-miss header detection (copy Secretary's test pattern)

5. **`tests/orchestrator/test_pulse_dispatch.py`**
   - Construct a pulse envelope by hand, call `orch.handle_pulse`
   - Asserts: envelope persisted with `source="pulse"`, Manager handler invoked, no listener fan-out
   - Delegation to Secretary produces the correct internal envelope chain with `delegation_payload` plumbed through
   - Pulse with `telegram_chat_id=None` and Manager returning text: reply persisted, no Telegram send
   - Pulse dispatch of non-Manager target → assertion error

6. **`tests/pulse/test_pulse_loader.py`**
   - Valid TOML parses to `PulseEntry` list
   - `chat_id_env` resolution from `os.environ`; missing var → `RuntimeError` naming the var
   - Non-int env var value → `RuntimeError`
   - `every_seconds < 10` → `RuntimeError`
   - Duplicate `name` → `RuntimeError`
   - Missing `[[pulse]]` array → empty list

7. **`tests/pulse/test_pulse_scheduler.py`**
   - `run_pulse_loop` with a fake orchestrator and a very short `every_seconds` (or monkeypatched `asyncio.sleep`): asserts orchestrator receives the right envelope at each tick
   - A raising `handle_pulse` does not kill the loop; next tick still fires
   - `CancelledError` propagates cleanly

8. **`tests/llm/test_provider_contract.py`** (extend existing if present)
   - Protocol conformance: `FakeProvider` and `AnthropicProvider` both satisfy the extended `LLMProvider` protocol

---

## 7. Open risks

1. **Long-loop token cost.** A runaway Manager that keeps calling tools could burn budget. Mitigation: `max_tool_iterations=8` with a loud `LLMProviderError` on overflow, and the tool-use-guide persona section instructing the model to be decisive. Monitoring cost per Manager turn is a follow-up.

2. **Pulse × multi-process duplication.** If we ever run >1 orchestrator process, each would fire its own `check_calendar` pulse independently and Manager would double-remind. 6c assumes one process. A future change adds a `pulse_leases` table with a short TTL lock on `(pulse_name, tick_bucket)`.

3. **Delegation-payload schema drift.** Secretary expects a specific `{kind, appointment, when, note}` shape. Manager constructs that shape in `_dispatch_tool`. If either side changes, the contract breaks silently because both are dicts. Mitigation (follow-up, not 6c): a small `DelegationPayload` union type in `envelope.py` with `reminder_request` as its first variant, validated at construction.

4. **Pulse-triggered delegation with unbound chat.** If `chat_id_env` is omitted and Manager still decides to delegate, Secretary's reminder has nowhere to go. Mitigation: the tool_use_guide section tells the model not to delegate during pulses with no chat binding; if it does anyway, the outbound Telegram send is a no-op and only the persisted internal envelope exists, which is observable in the audit layer.

5. **TurnState and re-entrancy.** `_agentic_loop` uses a local `TurnState`, so concurrent turns are safe — but if any future refactor moves state onto `self`, concurrent Manager turns would cross-contaminate delegations. The tests should include a concurrent-turn assertion (two `_agentic_loop` invocations interleaved via a scripted `FakeProvider`).

6. **Tool-use-guide persona drift.** The "stop calling tools after a delegation" rule is soft (persona only, not enforced). The deterministic "last delegation wins" fallback keeps behavior predictable, but we should watch for models that ignore the rule and adjust the persona.
