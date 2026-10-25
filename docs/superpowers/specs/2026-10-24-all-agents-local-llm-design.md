# All agents on the local LLM — design

**Date:** 2026-10-24
**Status:** approved by user in chat ("make the agents all use the new Qwen3.5
model instead of the Claude API, but for news report generation we can still
rely on the API"); detailed decisions below are the builder's calls, flagged.

## Intent

Run every MAAS agent on the locally served Qwen3.5-122B-A10B (Ollama,
`qwen3.5-122b-abliterated-q4km-32k`, OpenAI-compatible endpoint on
`127.0.0.1:11434/v1`) instead of the Anthropic API, **except** the
Intelligence agent's daily news-briefing generation, which stays on Claude
(Opus + extended thinking). Motivation: cost and privacy; the Secretary has
already been running locally since 2026-04-20 and the model now has a 32k
window shared with the chat UI and SillyTavern.

## Goals

- One switch (`LLM_BACKEND=local`) moves Manager, Secretary (both modes),
  Intelligence Q&A, Learning and Supervisor onto the local model.
- Intelligence briefing generation keeps its own provider and stays on
  Anthropic by default (`INTELLIGENCE_BRIEFING_BACKEND=anthropic`).
- Tool-using agents keep working: `LocalProvider.complete_with_tools` is
  implemented, so `run_agentic_loop` is untouched.
- `LLM_BACKEND=anthropic` (the default) reproduces today's behaviour exactly;
  the existing test-suite semantics are preserved.

## Non-goals

- No change to prompts/personas, tool sets, or agent logic beyond
  robustness to a non-Claude model's output (lenient JSON parsing).
- No automatic Anthropic fallback when Ollama is down: failures surface as
  `LLMProviderError` exactly as the Secretary free mode does today.
- No Ollama upgrade or server-side tool parser dependency (this machine runs
  Ollama 0.17.7; the official qwen3.5 library models require ≥0.40).
- No re-enabling of the Secretary's long-term-memory tool on the local model
  (see Invariant below).

## Anti-goals (would make this a failure even if it "works")

- Tool calls silently degrading into plain text replies (Manager "saying" it
  created an event without calling the calendar tool).
- Unaligned-model output reaching the shared `user_facts` table.
- The briefing quietly moving off Claude because of a config default.

## Constraints

- Ollama 0.17.7: the custom-imported GGUF is registered with a hand-written
  Go template (ChatML, thinking pre-disabled) and reports only the
  `completion` capability. Ollama will not render or parse tools for it.
- Qwen3.5's own chat template (embedded in the GGUF) defines the model's
  native tool protocol: tools as JSON lines inside `<tools>…</tools>` in the
  system turn with a fixed instruction block, calls emitted as
  `<tool_call><function=NAME><parameter=K>V</parameter>…</function></tool_call>`,
  results fed back as a user turn wrapping `<tool_response>…</tool_response>`.
  Verified live on 2026-10-24: single call with typed args, follow-up answer
  after a tool response, and no spurious calls on casual chat.
- Context 32,768 tokens shared with other consumers; agents' existing
  windows (transcript windows, `max_tokens`) already fit.
- `LocalProvider` ignores `thinking_budget_tokens` and prompt-cache hints.

## Decisions (builder's calls, each overridable)

1. **Prompt-level tool protocol in `LocalProvider`.** `complete_with_tools`
   renders tools into the system text exactly as the Qwen3.5 Jinja template
   does (tools block first, then the agent's system prompt), translates
   `AssistantToolUseMsg`/`ToolResultMsg` into the model's text forms, calls
   the plain chat-completions endpoint (no `tools` field), and parses
   `<tool_call>` blocks out of the reply. Argument values are coerced using
   the `ToolSpec.input_schema` property types (object/array → `json.loads`,
   integer/number/boolean → native, otherwise string). Tool-call ids are
   synthesised (`call_1`, …) because the protocol has none. Text before the
   first `<tool_call>` is preserved as `text`; a reply with no well-formed
   call is `kind="text"`. Rationale: works on today's Ollama, fully
   unit-testable with a mocked endpoint, and is the format the model was
   trained on.
2. **Two settings, same shape as `SECRETARY_MODE`:** `LLM_BACKEND`
   (`anthropic` | `local`, default `anthropic`) selects the shared default
   provider; `INTELLIGENCE_BRIEFING_BACKEND` (same values, default
   `anthropic`) selects the summarizer provider independently. `LLM_PROVIDER=fake`
   keeps overriding everything for tests.
3. **Provider wiring in `main.py`:** `_build_llm_provider` becomes a pure
   function of `(settings, usage_store)` returning Local or Anthropic;
   Intelligence's two providers are built by a sibling factory honouring both
   settings (the QA provider follows `LLM_BACKEND`; when local it reuses the
   shared `LocalProvider` instance, which is stateless per call).
4. **Invariant kept: a `LocalProvider` Secretary never gets the
   `user_facts_writer`.** With `LLM_BACKEND=local` and `SECRETARY_MODE=work`
   the Secretary runs the normal persona on the local model with the memory
   tool disabled (same rule the 2026-04-20 design §6 established for free
   mode). The factory's parameter is renamed `default_provider` since it is no
   longer necessarily Anthropic.
5. **Lenient JSON extraction for Learning's `_summarize_content`** (find the
   first balanced JSON object in the reply) so a chatty local reply does not
   fail the tool. Supervisor already uses `raw_decode`.
6. **Observability:** per-agent startup log lines report the effective
   backend and model instead of the log-only toml model names. The local
   model name is already in the `/usage` exclusion set.
7. **`.env` for this deployment** sets `LLM_BACKEND=local`; `.env.example`
   and README document both settings.

## Left to the builder

- Exact regexes and error handling for malformed `<tool_call>` output (log at
  WARNING and fall back to text).
- Whether to merge the shared retry/error-mapping code between `complete`
  and `complete_with_tools` (recommended).
- Test fixtures: extend `tests/test_local_provider.py` (respx) and add a
  `run_agentic_loop` + `LocalProvider` integration test mirroring the
  Anthropic translation tests.

## Verification

- Full test suite green (the pre-existing `test_daily_rollup_groups_by_day_desc`
  time-bomb excepted; a separate task fixes it).
- Live probe: `LocalProvider.complete_with_tools` against the real Ollama
  endpoint with a dummy tool returns a parsed `ToolCall`; a follow-up with a
  `ToolResultMsg` returns `kind="text"`.
- One human smoke test at the end: MAAS restarted with `LLM_BACKEND=local`,
  then on Telegram ask Manager about tomorrow's schedule (calendar tool) and
  Intelligence a follow-up question about today's report (Q&A on local, the
  report itself still generated by Claude).

## Addendum 2026-10-25 — thinking mode on for every consumer

User decision: enable Qwen3.5 thinking for MAAS, the chat UI and SillyTavern.

- **Mechanism (builder's call).** The Modelfile template pre-fills `<think>\n`
  on the generation prompt (the model only reasons when pre-filled — without
  it, it emits an empty think block and answers directly, verified) and
  references `.Thinking`/`.Think`, which makes Ollama 0.17.7 infer the
  `thinking` capability and separate reasoning server-side: `thinking` on
  `/api/chat`, `reasoning` on `/v1`, and `think:false` honoured per request on
  the native API. No consumer parses `</think>` text; `LocalProvider` strips a
  leaked closing tag only defensively.
- **Budgets.** Reasoning tokens count against `max_tokens`. `LocalProvider`
  adds `LOCAL_LLM_THINKING_BUDGET` (default 4096) to every caller's budget and
  raises `LocalProviderError` ("thinking exhausted…") when reasoning consumed
  it all with no reply. The chat UI uses 6144 total.
- **Tool loop.** Assistant tool-call turns are rendered with the empty
  `<think>\n\n</think>\n\n` prefix the model's template emits after the last
  user query (reasoning is not retained across iterations).
- **Sampling** switched to Qwen's thinking-mode defaults (temperature 1.0,
  top_p 0.95, top_k 20).
- **Measured cost (single Ollama slot, ~20 tok/s):** 200–1500 reasoning
  tokens per reply — 14 s for "3+4", ~80 s for a casual Secretary message.
  Known consequence: every Secretary listener evaluation now pays this too.
  Per-purpose control (e.g. `think:false` for the listener) would require
  `LocalProvider` to call Ollama's native `/api/chat`; deferred.
