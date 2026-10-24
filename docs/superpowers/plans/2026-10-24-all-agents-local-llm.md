# All Agents on the Local LLM — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every MAAS agent runnable on the local Qwen3.5 model via `LLM_BACKEND=local`, with tool use working through `LocalProvider`, while the Intelligence briefing stays on Anthropic.

**Architecture:** A new pure module `project0.llm.qwen_tools` renders/parses Qwen3.5's native text tool protocol; `LocalProvider.complete_with_tools` uses it over the plain chat-completions endpoint so `run_agentic_loop` is untouched. Two new settings choose the shared default provider and the Intelligence summarizer provider independently; `main.py` factories become pure functions of `Settings` and get unit tests.

**Tech Stack:** Python 3.12, `openai` AsyncOpenAI client → Ollama `/v1`, pytest + pytest-asyncio + respx, `uv run`.

**Spec:** `docs/superpowers/specs/2026-10-24-all-agents-local-llm-design.md`

## Global Constraints

- Default behaviour with no new env vars set must be identical to today (`LLM_BACKEND=anthropic`, `INTELLIGENCE_BRIEFING_BACKEND=anthropic`).
- No `tools` field is ever sent to the local endpoint (Ollama 0.17.7 rejects it for this model).
- The tool-protocol text must match the GGUF's Jinja template byte-for-byte for the instruction block (copied verbatim in Task 1).
- A `LocalProvider` Secretary never receives a `user_facts_writer`.
- Valid backend values are exactly `anthropic` and `local`; anything else raises `RuntimeError` naming the env var.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. Model replies with text *and* a tool call ("好的，我来查一下 <tool_call>…") — the text must be kept as `ToolUseResult.text`, the call parsed. (Task 1 test `test_parse_text_before_call`.)
2. Model emits a malformed call (unclosed `</function>`) — must degrade to `kind="text"` with the raw reply, never raise. (Task 1 `test_parse_malformed_falls_back_to_text`.)
3. Tool argument typed `integer` arrives as `"3"`, or `object` arrives as a JSON string — must be coerced per schema; non-coercible values stay strings. (Task 1 `test_parse_coerces_by_schema`.)
4. `LLM_BACKEND=local` with `SECRETARY_MODE=work` — Secretary gets the normal persona on the local provider but **no** writer. (Task 5 `test_work_mode_with_local_default_provider_disables_writer`.)
5. Learning's summarizer reply wrapped in prose ("这是结果：{…} 希望有帮助") — must still parse. (Task 6 `test_extract_json_object_from_prose`.)

---

### Task 1: Qwen3.5 native tool protocol (pure functions)

**Files:**
- Create: `src/project0/llm/qwen_tools.py`
- Test: `tests/llm/test_qwen_tools.py`

**Interfaces:**
- Consumes: `ToolSpec`, `ToolCall`, `ToolResultMsg` from `project0.llm.tools`.
- Produces:
  - `TOOLS_INSTRUCTIONS: str` — verbatim from the GGUF Jinja: the text from `If you choose to call a function ONLY reply in the following format with NO suffix:` through `</IMPORTANT>` (exact copy below).
  - `render_tools_system(tools: list[ToolSpec], system_text: str) -> str`
  - `render_assistant_turn(text: str | None, tool_calls: list[ToolCall]) -> str`
  - `render_tool_results(results: list[ToolResultMsg]) -> str`
  - `parse_tool_calls(reply: str, tools: list[ToolSpec]) -> tuple[str | None, list[ToolCall]]`

Verbatim instruction block (the Jinja template's `tools` branch, preceded by the tools list):

```
# Tools

You have access to the following functions:

<tools>
{tool JSON, one per line}
</tools>

If you choose to call a function ONLY reply in the following format with NO suffix:

<tool_call>
<function=example_function_name>
<parameter=example_parameter_1>
value_1
</parameter>
<parameter=example_parameter_2>
This is the value for the second parameter
that can span
multiple lines
</parameter>
</function>
</tool_call>

<IMPORTANT>
Reminder:
- Function calls MUST follow the specified format: an inner <function=...></function> block must be nested within <tool_call></tool_call> XML tags
- Required parameters MUST be specified
- You may provide optional reasoning for your function call in natural language BEFORE the function call, but NOT after
- If there is no function call available, answer the question like normal with your current knowledge and do not tell the user about function calls
</IMPORTANT>
```

Each tool JSON line is `json.dumps({"type": "function", "function": {"name": spec.name, "description": spec.description, "parameters": spec.input_schema}}, ensure_ascii=False)`. `system_text` follows after `\n\n` when non-blank.

Rendering rules (from the Jinja): assistant turn = `text` then, for the first call, `\n\n<tool_call>` if text else `<tool_call>`, later calls joined by `\n`; each call is `<tool_call>\n<function=NAME>\n` + per arg `<parameter=K>\nV\n</parameter>\n` + `</function>\n</tool_call>`, where V is `json.dumps(v, ensure_ascii=False)` for dict/list else `str(v)`. Tool results = `"\n".join(f"<tool_response>\n{content}\n</tool_response>")`, with `[tool error] ` prefixed to `content` when `is_error`.

Parsing rules: calls matched by `<tool_call>\s*<function=([^>\s]+)>(.*?)</function>\s*</tool_call>` (DOTALL); params by `<parameter=([^>\s]+)>\n?(.*?)\n?</parameter>` (DOTALL); ids `call_1`, `call_2`, …; `text` = reply before the first `<tool_call>`, stripped, `None` if empty. Coercion by the matching tool's `input_schema["properties"][k]["type"]` (a list type uses its first non-`"null"` entry): `integer`→`int`, `number`→`float`, `boolean`→`true/false/yes/no/1/0` case-insensitive, `object`/`array`→`json.loads`; any failure or unknown type keeps the raw string. If the reply contains `<function=` but no complete match, log a WARNING and return `(reply.strip(), [])`.

- [ ] **Step 1: Write the failing tests** in `tests/llm/test_qwen_tools.py`:

```python
WEATHER = ToolSpec(name="get_weather", description="Forecast.", input_schema={
    "type": "object", "properties": {"city": {"type": "string"}, "days": {"type": "integer"},
    "opts": {"type": "object"}, "flag": {"type": ["boolean", "null"]}}, "required": ["city"]})

def test_render_tools_system_layout():
    out = render_tools_system([WEATHER], "You are 林夕.")
    assert out.startswith("# Tools\n\nYou have access to the following functions:\n\n<tools>\n")
    assert '"name": "get_weather"' in out
    assert "</tools>\n\nIf you choose to call a function ONLY reply" in out
    assert out.endswith("</IMPORTANT>\n\nYou are 林夕.")

def test_render_assistant_turn_with_and_without_text():
    call = ToolCall(id="call_1", name="get_weather", input={"city": "上海", "days": 3, "opts": {"a": 1}})
    assert render_assistant_turn(None, [call]) == (
        "<tool_call>\n<function=get_weather>\n<parameter=city>\n上海\n</parameter>\n"
        "<parameter=days>\n3\n</parameter>\n<parameter=opts>\n{\"a\": 1}\n</parameter>\n</function>\n</tool_call>")
    assert render_assistant_turn("查一下", [call]).startswith("查一下\n\n<tool_call>")

def test_render_tool_results_merges_and_marks_errors():
    out = render_tool_results([ToolResultMsg(tool_use_id="call_1", content="ok", is_error=False),
                               ToolResultMsg(tool_use_id="call_2", content="boom", is_error=True)])
    assert out == "<tool_response>\nok\n</tool_response>\n<tool_response>\n[tool error] boom\n</tool_response>"

def test_parse_single_call_with_ids():
    text, calls = parse_tool_calls("<tool_call>\n<function=get_weather>\n<parameter=city>\n上海\n</parameter>\n</function>\n</tool_call>", [WEATHER])
    assert text is None and calls == [ToolCall(id="call_1", name="get_weather", input={"city": "上海"})]

def test_parse_coerces_by_schema():
    reply = ("<tool_call>\n<function=get_weather>\n<parameter=city>\n上海\n</parameter>\n<parameter=days>\n3\n</parameter>\n"
             "<parameter=opts>\n{\"a\": 1}\n</parameter>\n<parameter=flag>\ntrue\n</parameter>\n</function>\n</tool_call>")
    _, calls = parse_tool_calls(reply, [WEATHER])
    assert calls[0].input == {"city": "上海", "days": 3, "opts": {"a": 1}, "flag": True}

def test_parse_uncoercible_stays_string():
    _, calls = parse_tool_calls("<tool_call>\n<function=get_weather>\n<parameter=days>\nthree\n</parameter>\n</function>\n</tool_call>", [WEATHER])
    assert calls[0].input == {"days": "three"}

def test_parse_text_before_call():
    text, calls = parse_tool_calls("好的，我来查一下。\n\n<tool_call>\n<function=get_weather>\n<parameter=city>\n上海\n</parameter>\n</function>\n</tool_call>", [WEATHER])
    assert text == "好的，我来查一下。" and len(calls) == 1

def test_parse_multiple_calls_numbered():
    reply = "<tool_call>\n<function=get_weather>\n<parameter=city>\nA\n</parameter>\n</function>\n</tool_call>\n<tool_call>\n<function=get_weather>\n<parameter=city>\nB\n</parameter>\n</function>\n</tool_call>"
    _, calls = parse_tool_calls(reply, [WEATHER])
    assert [c.id for c in calls] == ["call_1", "call_2"] and calls[1].input == {"city": "B"}

def test_parse_plain_text_has_no_calls():
    assert parse_tool_calls("今天天气不错。", [WEATHER]) == ("今天天气不错。", [])

def test_parse_malformed_falls_back_to_text():
    broken = "<tool_call>\n<function=get_weather>\n<parameter=city>\n上海\n</parameter>\n"
    assert parse_tool_calls(broken, [WEATHER]) == (broken.strip(), [])
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/llm/test_qwen_tools.py -q` → ImportError on `project0.llm.qwen_tools`.
- [ ] **Step 3: Implement `src/project0/llm/qwen_tools.py`** with the four functions and the constant, module docstring citing the spec and that the text is copied from the GGUF's `tokenizer.chat_template`.
- [ ] **Step 4: Run to verify pass** — same command → 10 passed. Also `uv run ruff check src/project0/llm/qwen_tools.py tests/llm/test_qwen_tools.py` → clean.
- [ ] **Step 5: Commit** — `feat(llm): Qwen3.5 native tool-protocol render/parse helpers`.

---

### Task 2: `LocalProvider.complete_with_tools`

**Files:**
- Modify: `src/project0/llm/local_provider.py` (docstring :1-7, class docstring :51, `complete` :75-159, `complete_with_tools` :161-175)
- Test: `tests/test_local_provider.py` (replace `test_local_provider_complete_with_tools_raises_not_implemented` at :279)

**Interfaces:**
- Consumes: Task 1 functions.
- Produces:
  - `async def _create(self, payload_messages: list[ChatCompletionMessageParam], max_tokens: int) -> ChatCompletion` — the existing retry/error-mapping loop extracted from `complete`.
  - `def _record_usage(self, resp: ChatCompletion, *, agent: str, purpose: str, envelope_id: int | None) -> None` — the existing usage-record + log extracted from `complete`.
  - `complete_with_tools(...) -> ToolUseResult` per the `LLMProvider` protocol.

Translation in `complete_with_tools`: system = `render_tools_system(tools, _flatten_system(system))`; `Msg` → `{role, content}`; `AssistantToolUseMsg` → `{"role": "assistant", "content": render_assistant_turn(m.text, m.tool_calls)}`; each maximal run of consecutive `ToolResultMsg` → one `{"role": "user", "content": render_tool_results(run)}`. After `_create` + `_record_usage`: `text, calls = parse_tool_calls(content, tools)`; return `ToolUseResult(kind="tool_use", text=text, tool_calls=calls, stop_reason=finish_reason)` when calls, else `ToolUseResult(kind="text", text=content or "", tool_calls=[], stop_reason=finish_reason)`.

- [ ] **Step 1: Write failing tests** appended to `tests/test_local_provider.py` (same respx style; capture the request with `route.calls.last.request`):

```python
TOOL = ToolSpec(name="calendar_list_events", description="List events.",
                input_schema={"type": "object", "properties": {"day": {"type": "string"}, "limit": {"type": "integer"}}})

def _completion(content: str) -> dict: ...  # same shape as the happy-path test, finish_reason "stop"

@pytest.mark.asyncio
@respx.mock
async def test_complete_with_tools_parses_call_and_sends_no_tools_field(tmp_path):
    route = respx.post(f"{BASE_URL}/chat/completions").mock(return_value=httpx.Response(200, json=_completion(
        "<tool_call>\n<function=calendar_list_events>\n<parameter=day>\n明天\n</parameter>\n<parameter=limit>\n5\n</parameter>\n</function>\n</tool_call>")))
    result = await provider.complete_with_tools(system=SystemBlocks(stable="You are 林夕.", facts="喜欢茶"),
        messages=[Msg(role="user", content="明天有什么安排？")], tools=[TOOL], max_tokens=512, agent="manager", purpose="tool_loop", envelope_id=7)
    body = json.loads(route.calls.last.request.content)
    assert "tools" not in body
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"].startswith("# Tools")
    assert body["messages"][0]["content"].endswith("You are 林夕.\n\n喜欢茶")
    assert result.kind == "tool_use"
    assert result.tool_calls == [ToolCall(id="call_1", name="calendar_list_events", input={"day": "明天", "limit": 5})]
    assert result.stop_reason == "stop"
    row = usage.recent(limit=1)[0]; assert row["agent"] == "manager" and row["purpose"] == "tool_loop"

@pytest.mark.asyncio
@respx.mock
async def test_complete_with_tools_translates_history(tmp_path):
    # messages: user, AssistantToolUseMsg(text="我查一下", one call), two ToolResultMsg (one error), then expects final text
    ... mock returns _completion("明天下午三点有会议。")
    body = json.loads(route.calls.last.request.content)
    assert body["messages"][2]["role"] == "assistant" and body["messages"][2]["content"].startswith("我查一下\n\n<tool_call>")
    assert body["messages"][3]["role"] == "user"
    assert body["messages"][3]["content"] == "<tool_response>\n[{\"title\": \"会议\"}]\n</tool_response>\n<tool_response>\n[tool error] not found\n</tool_response>"
    assert len(body["messages"]) == 4
    assert result.kind == "text" and result.text == "明天下午三点有会议。" and result.tool_calls == []

@pytest.mark.asyncio
@respx.mock
async def test_complete_with_tools_connection_error_is_provider_error(tmp_path):
    respx.post(...).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LLMProviderError): await provider.complete_with_tools(...)
```

Delete `test_local_provider_complete_with_tools_raises_not_implemented`.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_local_provider.py -q` → the three new tests fail with `NotImplementedError`.
- [ ] **Step 3: Implement** — extract `_create` and `_record_usage` from `complete` (behaviour unchanged), write `complete_with_tools` per the translation rules; update the module and class docstrings (no longer "Secretary only", "no tool-use").
- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_local_provider.py tests/test_secretary_local_integration.py -q` → all pass (integration test still asserts no `tools` field).
- [ ] **Step 5: Commit** — `feat(llm): LocalProvider tool use via Qwen3.5 native protocol`.

---

### Task 3: Agentic loop end-to-end on `LocalProvider`

**Files:**
- Test: `tests/test_local_tool_loop_integration.py` (new)

**Interfaces:**
- Consumes: `run_agentic_loop` (`src/project0/agents/_tool_loop.py:65`), Task 2.

- [ ] **Step 1: Write the test** — respx `side_effect` returning first a tool-call completion (`echo` with `text="ping"`), then `"ping done"`; `dispatch_tool` as in `tests/agents/test_tool_loop_shared.py:19`. Assert `result.final_text == "ping done"`, `result.errored is False`, two requests made, and the second request's last message is `{"role": "user", "content": "<tool_response>\nping\n</tool_response>"}`. Add a second test: server returns 503 twice → `result.errored is True` (loop catches `LLMProviderError`).
- [ ] **Step 2: Run** — `uv run pytest tests/test_local_tool_loop_integration.py -q` → 2 passed (no new code expected; if a translation bug surfaces, fix it in `local_provider.py`).
- [ ] **Step 3: Commit** — `test(llm): run_agentic_loop end-to-end over LocalProvider`.

---

### Task 4: Settings `LLM_BACKEND` and `INTELLIGENCE_BRIEFING_BACKEND`

**Files:**
- Modify: `src/project0/config.py` (`Settings` :36-39, parsing :96-107, return :150-153)
- Test: `tests/test_config_llm_backend.py` (new; copy the `required_env` fixture from `tests/test_config_secretary_mode.py:17-29`)

**Interfaces:**
- Produces: `Settings.llm_backend: Literal["anthropic", "local"] = "anthropic"`, `Settings.intelligence_briefing_backend: Literal["anthropic", "local"] = "anthropic"`; env vars `LLM_BACKEND`, `INTELLIGENCE_BRIEFING_BACKEND`, parsed with the `SECRETARY_MODE` pattern (`.strip().lower() or default`, membership check, `RuntimeError` message containing the env var name).

- [ ] **Step 1: Write failing tests**: defaults are both `"anthropic"` with vars unset; `LLM_BACKEND=local` → `"local"` while briefing stays `"anthropic"`; `INTELLIGENCE_BRIEFING_BACKEND=local` honoured independently; `LLM_BACKEND=openai` raises `RuntimeError` matching `"LLM_BACKEND"`; `INTELLIGENCE_BRIEFING_BACKEND=x` raises matching `"INTELLIGENCE_BRIEFING_BACKEND"`; values are case-insensitive (`Local` → `"local"`).
- [ ] **Step 2: Run** — `uv run pytest tests/test_config_llm_backend.py -q` → fail (`Settings` has no field).
- [ ] **Step 3: Implement** fields + parsing + constructor args.
- [ ] **Step 4: Run** — `uv run pytest tests/test_config_llm_backend.py tests/test_config_secretary_mode.py tests/test_secretary_mode_factory.py -q` → pass (the factory test constructs `Settings` positionally by keyword; new fields default).
- [ ] **Step 5: Commit** — `feat(config): LLM_BACKEND and INTELLIGENCE_BRIEFING_BACKEND settings`.

---

### Task 5: Provider factories in `main.py`

**Files:**
- Modify: `src/project0/main.py` (`_build_llm_provider` :171-192, `_build_secretary_dependencies` :195-239 and call site :274-290, Intelligence providers :379-394, log lines :412-417, :451, :471)
- Modify: `tests/test_secretary_mode_factory.py` (param rename; add invariant test)
- Test: `tests/test_llm_backend_factory.py` (new)

**Interfaces:**
- Consumes: Task 4 settings.
- Produces:
  - `_build_local_provider(settings: Settings, usage_store: LLMUsageStore) -> LocalProvider` — from `settings.local_llm_*`.
  - `_build_llm_provider(settings, usage_store) -> LLMProvider` — `LLM_PROVIDER=fake` still wins; else `settings.llm_backend == "local"` → `_build_local_provider`; else Anthropic exactly as today.
  - `_build_intelligence_providers(*, settings, usage_store, default_provider: LLMProvider, summarizer_model: str, qa_model: str) -> tuple[LLMProvider, LLMProvider]` returning `(summarizer, qa)`: summarizer = Anthropic(`summarizer_model`) unless `intelligence_briefing_backend == "local"` → `_build_local_provider`; qa = `default_provider` when `llm_backend == "local"`, else Anthropic(`qa_model`).
  - `_build_secretary_dependencies(*, settings, usage_store, default_provider, base_facts_writer)` — parameter renamed from `anthropic_provider`; work mode returns `base_facts_writer` only when `not isinstance(default_provider, LocalProvider)`, else `None` with a `log.info` naming the invariant.

- [ ] **Step 1: Write failing tests** in `tests/test_llm_backend_factory.py` (reuse `_settings()` helper shape from `tests/test_secretary_mode_factory.py:22`, extended with `llm_backend=`/`intelligence_briefing_backend=` kwargs; `monkeypatch.delenv("LLM_PROVIDER", raising=False)`):
  - `test_default_backend_builds_anthropic` → `isinstance(p, AnthropicProvider)`.
  - `test_local_backend_builds_local_provider_from_settings` → `isinstance(p, LocalProvider)` and `p._model == settings.local_llm_model`.
  - `test_fake_provider_env_overrides_backend` → `LLM_PROVIDER=fake` + `llm_backend="local"` → `FakeProvider`.
  - `test_intelligence_briefing_stays_anthropic_when_backend_local` → summarizer `AnthropicProvider` with `_model == "claude-opus-4-6"` (check the attribute name in `provider.py:231`), qa `is default_provider`.
  - `test_intelligence_briefing_local_when_asked` → `intelligence_briefing_backend="local"` → summarizer `LocalProvider`.
  - `test_intelligence_all_anthropic_by_default` → both Anthropic, qa model `"claude-sonnet-4-6"`.
  In `tests/test_secretary_mode_factory.py`: rename `anthropic_provider=` → `default_provider=` in all calls and the signature assertion; add `test_work_mode_with_local_default_provider_disables_writer` (work mode + a `LocalProvider` default → provider is that instance, prompts are `secretary.{md,toml}`, writer is `None`).
- [ ] **Step 2: Run** — `uv run pytest tests/test_llm_backend_factory.py tests/test_secretary_mode_factory.py -q` → fail on missing names / old kwarg.
- [ ] **Step 3: Implement** the factories; at the Intelligence construction site replace :383-394 with the factory call; change the startup log lines at :412-417, :451, :471 (and Manager's at :345, Secretary's at :307) to log `backend=%s` from `settings.llm_backend` (briefing line logs `intelligence_briefing_backend`) alongside the configured model names; keep the belt-and-suspenders assertion at :286-290 (message no longer mentions free mode specifically).
- [ ] **Step 4: Run** — `uv run pytest -q` → all pass except the known `test_daily_rollup_groups_by_day_desc`. `uv run ruff check src/project0/main.py` adds no new errors versus `git stash` baseline.
- [ ] **Step 5: Commit** — `feat(main): LLM_BACKEND-driven provider factories; briefing stays on Anthropic`.

---

### Task 6: Lenient JSON extraction in Learning

**Files:**
- Modify: `src/project0/agents/learning.py` (`_summarize_content` :541-548)
- Test: `tests/agents/test_learning_agent.py` (append)

**Interfaces:**
- Produces: module-level `def _extract_json_object(text: str) -> dict[str, Any]` in `learning.py`: strip code fences as today, then `json.loads` the whole text; on failure, scan for the first `{` and use `json.JSONDecoder().raw_decode` from there; raise `ValueError` if nothing parses (the existing `_dispatch_tool` catches `ValueError`).

- [ ] **Step 1: Write failing tests**: `test_extract_json_object_plain`, `test_extract_json_object_fenced`, `test_extract_json_object_from_prose` (`'这是结果：{"title": "T", "summary": "S", "tags": ["a"]} 希望有帮助'` → the dict), `test_extract_json_object_garbage_raises` (`"抱歉，我做不到"` → `ValueError`).
- [ ] **Step 2: Run** — `uv run pytest tests/agents/test_learning_agent.py -q -k extract_json` → ImportError.
- [ ] **Step 3: Implement** and make `_summarize_content` return `_extract_json_object(raw)`.
- [ ] **Step 4: Run** — `uv run pytest tests/agents/test_learning_agent.py -q` → pass.
- [ ] **Step 5: Commit** — `fix(learning): tolerate prose around the summarizer's JSON reply`.

---

### Task 7: Deploy on this machine and document

**Files:**
- Modify: `.env` (add `LLM_BACKEND=local`; leave `INTELLIGENCE_BRIEFING_BACKEND` unset = anthropic), `.env.example` (document both vars next to the local-LLM block :56-75), `README.md` (:27 bullet: all agents can run local; briefing stays on Claude).

- [ ] **Step 1: Live provider probe** (no Telegram): `uv run python -` script constructing `LocalProvider` from `load_settings()` values with a dummy `echo` tool against the real Ollama; assert the first `complete_with_tools` returns `kind="tool_use"` with `name="echo"`, and a follow-up with the `ToolResultMsg` returns `kind="text"`. Expected: both assertions print `OK`.
- [ ] **Step 2: Docs + env edits**, then `uv run pytest -q` (same green state).
- [ ] **Step 3: Restart MAAS** — `curl -s -X POST http://127.0.0.1:8090/maas/restart`; then confirm in the MAAS log/terminal the startup lines show `backend=local` for manager/learning/supervisor/secretary and the Intelligence line shows `briefing_backend=anthropic`.
- [ ] **Step 4: Commit** — `feat: run all agents on the local LLM; Intelligence briefing stays on Anthropic` (`.env` is untracked and not committed).
- [ ] **Step 5: Hand the user the single human smoke test**: on Telegram ask 林夕 "明天下午有什么安排？" (calendar tool round-trip on Qwen) and 顾瑾 a follow-up about today's briefing (Q&A on Qwen; report generation unchanged).
