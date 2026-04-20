# Sub-project — Secretary Local-LLM Option (persona-coupled provider switch)

**Date:** 2026-04-20
**Status:** Design approved, ready for implementation plan
**Depends on:** `LLMProvider` Protocol (`llm/provider.py`), Secretary agent (`agents/secretary.py`), existing user-facts isolation (`store.py`), external vLLM/TRT-LLM server on DGX Spark

---

## 1. Context and scope

Today Secretary (秘书) calls the Anthropic API through `AnthropicProvider`, same as every other agent. The user wants a second mode that points Secretary — and only Secretary — at a locally-hosted abliterated model, so she can chat more freely (including NSFW content) without hitting Claude's safety filter and without sending that content to a third party.

The local model is already deployed as a separate sub-project in `~/llm-workspace/TensorRT-LLM`. It runs `huihui-ai/Qwen2.5-72B-Instruct-abliterated` on NVIDIA DGX Spark (GB10), served with either TensorRT-LLM NVFP4 (default) or vLLM AWQ (rollback). Both expose an **OpenAI-compatible API** at `http://127.0.0.1:8000/v1`. Measured throughput: 5.5 tok/s single-stream, ~43 tok/s at batch 8. The server's lifecycle is owned by that project's Gradio UI (`app.py`) and is out of scope here.

**Primary goal:** make it possible to flip Secretary between her normal Claude-backed persona and a local-LLM-backed persona by editing one env var and restarting — so we can **test whether local speed and quality are good enough for daily companion chat**. If they are, we keep the switch as a user-facing choice. If not, we roll back by flipping the env var and restarting.

**NSFW isolation requirement.** The user also required a rigorous audit that nothing Secretary produces can leak into another agent's context. That audit was done before this spec and is summarised in §6. The design takes one hard invariant from it: **`free` mode disables the user-facts writer unconditionally.**

### In scope (v1.0)

- New env var `SECRETARY_MODE` with values `work` (default) and `free`
- New `LocalProvider` class in `llm/local_provider.py` implementing the existing `LLMProvider` Protocol over OpenAI-compatible HTTP
- New Persona B config file `prompts/secretary_free.toml` — for v1.0 this is a verbatim copy of `prompts/secretary.toml` with tighter token caps; the user will edit the persona prose manually later
- Factory in `main.py` that reads `SECRETARY_MODE` and builds one coupled bundle: `(provider, persona file, facts writer)`
- Hard invariant in the factory: `local provider ⇒ user_facts_writer is None` (asserted, not merely convention)
- `Settings` dataclass extended with the new env vars, documented in `.env.example`
- Typing-indicator refresh during local inference so the user sees "Secretary is typing…" on Telegram instead of a silent wait
- Full unit test coverage of provider, factory, and Secretary wiring
- One prepared human smoke test at the end

### Out of scope (explicitly deferred)

- **Streaming tokens to Telegram.** Evaluated and rejected: Telegram has no native streaming, the edit-a-message workaround runs into flood control, "edited" labels, and markdown-mid-stream glitches. The typing indicator gives enough live feedback for v1.0.
- **Runtime switching via Telegram command.** v1.0 switches via `.env` + restart. A slash command (`/mode free`) is a v1.1 concern once the local path is proven.
- **Server lifecycle management.** MAAS assumes the local server is already running when it starts. No boot-time health check, no auto-start, no "wait for ready" loop — the Gradio UI in the TRT-LLM project owns the server. If the server is down when a `free`-mode request arrives, Secretary sends a graceful fallback message and the daemon keeps running.
- **Tool-use on local model.** `free` mode disables the only tool (`remember_about_user`) at the wiring level. Secretary's existing code already treats `user_facts_writer=None` as a valid state and falls through to plain `complete()`. `LocalProvider.complete_with_tools` raises `NotImplementedError` — it must never be called in `free` mode.
- **Hybrid persona/provider combinations.** Persona and provider are coupled 1:1 by design: `work ⇔ A + Claude`, `free ⇔ B + Local`. No matrix, no cross-combos, no per-message switching.
- **NSFW detection or filtering on inbound user messages.** Out of scope — this sub-project changes where Secretary thinks, not what she's allowed to hear.
- **Cross-device / remote local LLM.** v1.0 assumes `127.0.0.1:8000`. A remote GPU box is a later concern.

---

## 2. Architecture overview

The mode toggle picks one coupled bundle:

| Mode            | Persona | Provider         | Endpoint                  | Model                   | Memory tool |
|-----------------|---------|------------------|---------------------------|-------------------------|-------------|
| `work` (default)| A (秘书, current) | Anthropic        | api.anthropic.com         | `claude-sonnet-4-6`     | enabled     |
| `free`          | B (user-defined)  | OpenAI-compat    | `http://127.0.0.1:8000/v1`| `qwen2.5-72b-awq-8k`    | **disabled**|

Only Secretary's wiring changes. Manager, Supervisor, Intelligence, and Learning continue to share the global `AnthropicProvider` built the same way they always have.

```
┌─────────────────────────────────────────────────────────────────┐
│ MAAS process                                                    │
│                                                                 │
│   main.py                                                       │
│   ├── _build_llm_provider()   ── Anthropic (for 4 other agents) │
│   └── _build_secretary_deps(settings)                           │
│         │                                                       │
│         ├── SECRETARY_MODE=work                                 │
│         │     → AnthropicProvider (shared with others)          │
│         │     → prompts/secretary.toml                          │
│         │     → user_facts_writer (normal)                      │
│         │                                                       │
│         └── SECRETARY_MODE=free                                 │
│               → LocalProvider(base_url=127.0.0.1:8000/v1)       │
│               → prompts/secretary_free.toml                     │
│               → user_facts_writer=None  (INVARIANT, asserted)   │
│                                                                 │
│   Secretary(llm=provider, user_facts_writer=writer_or_none,     │
│             prompts=path)                                       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                     external server (not managed by MAAS)
                     http://127.0.0.1:8000/v1 — Gradio UI owns it
```

`LocalProvider` sits parallel to `AnthropicProvider` at the same abstraction layer. No other agent ever sees it, and the four non-Secretary agents cannot be misconfigured to use it — the factory does not expose a way to inject `LocalProvider` into them.

---

## 3. Components

### 3.1 New files

**`src/project0/llm/local_provider.py` (~120 LOC)**
- Class `LocalProvider` implementing the `LLMProvider` Protocol.
- Thin wrapper around `openai.AsyncOpenAI(base_url=..., api_key=...)`.
- Constructor args: `base_url`, `model`, `api_key` (any non-empty string), `usage_store: LLMUsageStore`, `request_timeout_seconds: float = 180.0`.
- `complete(system, messages, max_tokens, agent, purpose, envelope_id) -> str`:
  - Translates `SystemBlocks` or plain string into a single `{"role": "system", ...}` message (local model ignores Anthropic cache markers; join the two segments with `"\n\n"`).
  - Passes `messages` through unchanged (they are already in OpenAI-compatible shape in the codebase's internal representation).
  - Calls `client.chat.completions.create(model=..., messages=..., max_tokens=..., stream=False)`.
  - Extracts `response.choices[0].message.content` (empty string if null).
  - Records a usage row with `model` from config, `input_tokens = usage.prompt_tokens`, `output_tokens = usage.completion_tokens`, `cache_creation_input_tokens = 0`, `cache_read_input_tokens = 0`, `purpose`, `envelope_id`.
  - Wraps HTTP errors in the new exception hierarchy below.
- `complete_with_tools(...) -> ToolUseResult`: raises `NotImplementedError("LocalProvider does not support tool-use; free mode must run without user_facts_writer")`. This is a safety net — if reached, something is wrong upstream.
- Retry policy: one retry after 2s on HTTP 5xx; no retry on timeout or connection refused.

**New exceptions (in the same file):**
- `LocalProviderError(Exception)` — base.
- `LocalProviderUnavailableError(LocalProviderError)` — connection refused, timeout, or persistent 5xx.
- `LocalProviderContextError(LocalProviderError)` — HTTP 400 with context-length signal.

**`prompts/secretary_free.toml` (initial content)**
- Verbatim copy of `prompts/secretary.toml` with two caps lowered:
  - `max_tokens_reply = 500` (was 800)
  - `max_tokens_listener = 200` (was 400)
- Model field changed to `model = "qwen2.5-72b-awq-8k"` (informational; actual model passed by the provider).
- The persona prose (role section) is identical to Persona A at creation time. The user will edit this file manually later.

**`tests/test_local_provider.py` (~150 LOC)** — unit tests using `respx` to mock the OpenAI endpoint. See §7.

### 3.2 Modified files

**`src/project0/config.py`**
- Extend `Settings` with:
  - `secretary_mode: Literal["work", "free"] = "work"`
  - `local_llm_base_url: str = "http://127.0.0.1:8000/v1"`
  - `local_llm_model: str = "qwen2.5-72b-awq-8k"`
  - `local_llm_api_key: str = "unused"`
- Parse env vars: `SECRETARY_MODE`, `LOCAL_LLM_BASE_URL`, `LOCAL_LLM_MODEL`, `LOCAL_LLM_API_KEY`.
- Validate `secretary_mode ∈ {"work", "free"}`; raise `ConfigError` listing valid values on invalid input.

**`src/project0/main.py`**
- Add factory `_build_secretary_dependencies(settings, usage_store, base_facts_writer) -> tuple[LLMProvider, Path, UserFactsWriter | None]`:
  - If `settings.secretary_mode == "work"`: return `(global_anthropic_provider, Path("prompts/secretary.toml"), base_facts_writer)`.
  - If `settings.secretary_mode == "free"`:
    - Build a fresh `LocalProvider(base_url=settings.local_llm_base_url, model=settings.local_llm_model, api_key=settings.local_llm_api_key, usage_store=usage_store)`.
    - Return `(local_provider, Path("prompts/secretary_free.toml"), None)`.
  - Log at INFO which mode is active and which persona file was chosen.
- Immediately after the factory returns, assert the invariant:
  ```python
  if isinstance(secretary_provider, LocalProvider):
      assert secretary_facts_writer is None, \
          "SECRETARY_MODE=free must NOT wire user_facts_writer — " \
          "see 2026-04-20-secretary-local-llm-design.md §6"
  ```
- Wire the three returned values into `Secretary(...)`.

**`.env.example`**
- Document the four new vars with brief comments and the `work`/`free` meaning table.

### 3.3 Unchanged files (explicit)

- `agents/secretary.py` — zero code changes. It already accepts `LLMProvider` and already handles `user_facts_writer=None` (falls through to plain `complete()` at `secretary.py:423`). Verified in the pre-spec audit.
- All other agent files, orchestrator, store, telegram_io, supervisor, manager, intelligence, learning.

---

## 4. Data flow — one message under `free` mode

1. User DMs Secretary via her own Telegram bot. Webhook → orchestrator → Secretary.
2. Secretary immediately calls `sender.send_chat_action(chat_id, "typing")` on her bot's `Application`. One-line addition to the existing reply path, guarded by `try/except` and logging only.
3. Secretary spawns a background `asyncio.Task` that re-sends the typing action every 4 seconds (Telegram's indicator auto-expires at 5s). The task is cancelled in a `finally` block after the LLM call resolves or raises.
4. Secretary builds `SystemBlocks` + transcript + user message, calls `llm.complete(...)`. Exactly the same code path as today.
5. `LocalProvider.complete()`:
   - Joins the two segments of `SystemBlocks` (if present) into one system message.
   - Calls `AsyncOpenAI.chat.completions.create(..., stream=False, timeout=180s)`.
   - On success: writes to `LLMUsageStore`, returns `response.choices[0].message.content`.
   - On error: maps to `LocalProviderUnavailableError` / `LocalProviderContextError` per §5.
6. Secretary receives the string, returns `AgentResult(reply_text=...)`.
7. Orchestrator sends the reply once via `sender.send(...)`. Typing-refresh task is cancelled in the `finally`.
8. Nothing is written to `user_facts` — not because the model decided not to, but because there is no writer wired. Verified in tests (§7).

**Tool-use path is structurally unreachable.** `Secretary._run_with_tool_loop` branches on `self._user_facts_writer is not None` to decide whether to register the `remember_about_user` tool. With writer = None, the branch taken calls `llm.complete(...)` (plain chat) and `LocalProvider.complete_with_tools` is never invoked.

**Context budget** (Qwen's 8192 hard cap, prompt + completion combined):

| Segment              | Budget     | Notes                                         |
|----------------------|------------|-----------------------------------------------|
| System prompt        | ~1500 tok  | Persona + profile; persona file is scoped.   |
| Transcript history   | ~5500 tok  | Existing trim logic in `_load_transcript`.   |
| Reply cap            | 500 tok    | New cap in `secretary_free.toml`.             |
| Safety headroom      | ~700 tok   | Tokenizer variance, rare tool-free retries.   |

If history overflows the budget, existing trim logic handles it. No new trim code. If the 400-context-length error appears in practice, that is a signal to lower the transcript budget in `secretary_free.toml`, not to add new code here.

---

## 5. Error handling & failure modes

| Condition                                        | Caught where              | User-visible result                                             | Log |
|--------------------------------------------------|---------------------------|------------------------------------------------------------------|-----|
| Server down (`ConnectionError`, `ConnectError`)  | `LocalProvider.complete`  | `LocalProviderUnavailableError` → Secretary fallback message    | ERROR |
| HTTP read timeout (180s)                         | `LocalProvider.complete`  | Same as above                                                   | ERROR |
| Context length exceeded (HTTP 400 with signal)   | `LocalProvider.complete`  | `LocalProviderContextError` → Secretary fallback message        | WARN, include prompt size |
| Empty / null `content`                           | `LocalProvider.complete`  | Returns `""`; Secretary treats as "nothing to say" (existing)   | DEBUG |
| HTTP 5xx (once)                                  | `LocalProvider.complete`  | Retry after 2s; on second failure → `LocalProviderUnavailableError` | WARN on first, ERROR on second |
| Typing-action send failure                       | `Secretary` typing-refresh task | Silently skipped; does not block reply                     | DEBUG |
| Mode misconfigured (`SECRETARY_MODE=typo`)       | `config.load_settings`    | Process fails to start with explicit `ConfigError`              | CRITICAL |
| `free` mode but server unreachable at boot       | **not checked at boot**   | Daemon starts fine; first user message triggers fallback path  | — |

**Fallback message (Chinese, one sentence).** On any `LocalProviderError`, Secretary sends a persona-appropriate graceful failure line (e.g., "我现在有点走神，稍后再聊。"). The exact text lives in `secretary.py`'s existing error path and does not change with mode — the error-handling layer is provider-agnostic.

**No auto-restart of the server.** MAAS deliberately does not try to heal the local LLM. If Secretary fails repeatedly in `free` mode, the user sees fallback messages, checks the Gradio UI, restarts the server there, and keeps chatting. MAAS stays up the whole time.

**No boot-time health check.** Rationale: the local server takes ~4 minutes to load. Blocking MAAS startup on its readiness would couple two daemons that should stay independent. MAAS starts fast; first `free` request proves the server is up.

---

## 6. Isolation guarantees (for NSFW content)

A separate audit confirmed that other agents cannot see Secretary's outputs in the current codebase. Summary:

- **Group transcripts** — `MessagesStore.recent_for_chat(visible_to=...)` filters `from_agent='secretary'` and `to_agent='secretary'` out for every non-Secretary caller (`store.py:381`).
- **DM transcripts** — each agent's DM transcript is scoped by its own agent name (`store.py:421`). No cross-leakage.
- **Supervisor (叶霏)** — three independent blocks prevent her from ever reviewing Secretary: explicit `ValueError` raise in `envelopes_for_review` (`store.py:511`), tool schema enum excludes Secretary (`supervisor.py:431`), dispatch guard rejects Secretary (`supervisor.py:567`).
- **Manager→Secretary delegation** — one-way hand-off. Manager never sees Secretary's reply (`orchestrator.py:188`).
- **`LLMUsageStore`** — records token counts and envelope IDs only; no message bodies.
- **Telegram sender** — transmits only final `reply_text`; never transcripts or internal state.

**The one real risk and how this spec closes it.** `UserFactsReader.active()` (`store.py:731`) ignores the `agent_name` argument and returns all active facts to every caller. Manager, Intelligence, and Learning all load facts into their system prompts via `UserFactsReader.as_prompt_block()`. Today this is safe because only Claude-backed Secretary writes facts. If a local-LLM Secretary could write facts, NSFW content could leak verbatim into every other agent's prompt.

**Closure**: `free` mode wires `user_facts_writer=None` unconditionally. Secretary's code already treats this as "don't offer the tool". This spec adds an `assert` immediately after the factory call to make the invariant explicit and enforce it against future refactors. The invariant is documented inline in the assert message pointing back to this section.

**Residual risk: none known.** If the user later adds a second writer-authorized agent or changes Secretary to offer a different tool in `free` mode, this invariant must be re-evaluated.

---

## 7. Testing

### 7.1 Unit tests (automated, no server required)

**`tests/test_local_provider.py`** — mocks the OpenAI endpoint with `respx`:

- Happy path: server returns well-formed response → `complete()` returns text; `LLMUsageStore` has one row with model, input/output tokens.
- Connection refused → `LocalProviderUnavailableError`.
- 180s read timeout → `LocalProviderUnavailableError`.
- HTTP 400 with context-length marker → `LocalProviderContextError`.
- HTTP 500 once, then 200 → returns text (retry worked).
- HTTP 500 twice → `LocalProviderUnavailableError`.
- Empty / null content in response → returns `""`.
- `complete_with_tools(...)` → raises `NotImplementedError` with the expected message.

**`tests/test_secretary_mode_factory.py`** — tests the `main.py` factory in isolation:

- `SECRETARY_MODE=work` → returns `(AnthropicProvider, secretary.toml path, writer)`.
- `SECRETARY_MODE=free` → returns `(LocalProvider, secretary_free.toml path, None)`.
- Invariant: constructing a Secretary with `LocalProvider` **and** a non-None writer triggers `AssertionError` at the factory boundary.
- Unknown mode → `ConfigError` from `load_settings` with a message listing valid values.

**`tests/test_secretary_local_integration.py`** — wires `Secretary` with `LocalProvider` backed by `respx`:

- Plain DM → reply returned end-to-end; typing action was called at least once.
- With `user_facts_writer=None`, Secretary's tool-loop takes the plain `complete()` branch and never includes the `remember_about_user` tool in the request payload.
- When `LocalProvider` raises `LocalProviderUnavailableError`, Secretary's existing error path emits the fallback message and the process does not crash.

**Existing suites** (`mypy`, `ruff`) cover the new files with no config changes.

### 7.2 Human smoke test (one, at the end)

Per project testing discipline: automate everything testable; one prepared human check at the very end.

Preconditions:
- Local LLM server running at `http://127.0.0.1:8000/v1` with model `qwen2.5-72b-awq-8k`. `curl http://127.0.0.1:8000/v1/models` returns JSON.
- `.env` contains `SECRETARY_MODE=free`.
- MAAS restarted.
- Tailscale or local terminal access to check DB state.

Steps:
1. Send **one** DM to the Secretary bot on Telegram.
2. Verify:
   - Telegram shows the native "Secretary is typing…" indicator within a second or two.
   - A single reply message arrives within ~90 seconds.
   - The reply voice is Persona B (identical to Persona A in v1.0, since the user hasn't edited yet).
   - No new row appears in the `user_facts` table (`SELECT COUNT(*) FROM user_facts` before vs after is unchanged).
   - `llm_usage` has one new row with `model='qwen2.5-72b-awq-8k'` and non-zero output tokens.
3. Flip `SECRETARY_MODE=work` in `.env`, restart, send one DM, verify normal Claude reply.

If any step fails, fix and repeat once. No iterative "keep sending messages and tweaking" loop.

---

## 8. Rollout and rollback

**Rollout.** This sub-project is additive:
- New code paths are only reachable under `SECRETARY_MODE=free`.
- Default remains `work`. An unchanged `.env` behaves exactly as it does today.
- No schema migrations, no data backfill, no breaking API changes.

**Rollback.** Set `SECRETARY_MODE=work` (or remove the line — `work` is the default), restart the daemon. No cleanup needed. The new files can stay in the tree indefinitely without being exercised.

**Deletion path (if the experiment fails).** If local quality or speed proves unacceptable: keep `SECRETARY_MODE=work` as the only used value and leave the code in place for a future revisit, OR delete `local_provider.py`, `secretary_free.toml`, the factory branch, and the new env vars in one clean commit.

---

## 9. Open questions (deferred to implementation plan, not blocking)

1. Exact text for the Chinese fallback message when `LocalProviderError` fires — either reuse Secretary's existing error-path line or a slightly warmer variant. Resolve during implementation.
2. Whether the 2s retry delay on 5xx should be configurable. Default constant is fine for v1.0.
3. Whether to expose `temperature` as a config knob in `secretary_free.toml` now or defer. Lean towards defer; hardcode the OpenAI SDK default.
