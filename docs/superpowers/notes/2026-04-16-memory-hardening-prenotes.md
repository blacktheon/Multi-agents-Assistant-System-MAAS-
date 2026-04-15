# Pre-brainstorm notes — Memory layer hardening + token cost reduction

**Date:** 2026-04-16
**Purpose:** Hold the analysis from the 6e wrap-up conversation so the next session can pick up without re-deriving it.

---

## What triggered this sub-project

After merging 6e, we looked at the master spec's remaining sub-projects and the user picked **memory layer hardening** as next. During that discussion, the question of token cost came up — would adding a user profile layer make per-turn calls more or less expensive? The analysis produced a list of decoupled token-reduction wins we want to bundle into the same sub-project so memory hardening lands "net cheaper or equal" rather than "adds overhead."

Parent master spec for reference: `docs/superpowers/specs/2026-04-13-multi-agent-skeleton-design.md` §9 "What Comes After" — lists memory layer hardening as sub-project 2 in the master sequence. The Layer A (user profile) item is explicitly deferred there and never built.

---

## Token accounting as of post-6e

Measured character counts × rough CJK/English token ratios:

| Component | Tokens (approx) |
|---|---|
| Secretary persona (`prompts/secretary.md`, 5.0 KB) | ~1500 |
| Manager persona (`prompts/manager.md`, 10.2 KB) | ~3000 |
| Intelligence persona (`prompts/intelligence.md`, 9.5 KB) | ~2800 |
| Transcript window (20 msgs × ~100 tok) | ~2000 |
| Manager tool specs (~6 tools) | ~2000 |
| Intelligence tool specs (5 tools) | ~1500 |
| Injected latest report (Intelligence, 12 items) | ~2500 |

Typical per-turn input:

- Secretary DM (no tools): **~3500 tokens**
- Manager DM (tools): **~7000 tokens**
- Intelligence DM (tools + injected report): **~8500 tokens**

Prompt caching is already enabled on the `system` block via `cache_control: ephemeral` in `AnthropicProvider.complete`. First call pays full price; subsequent calls within ~5 min pay ~10% on the cached prefix. Extended-TTL cache (1 hr) is available for a higher first-call cost.

**Current cost order of magnitude:** ~$2–5/month uncached / ~$0.50–1.50/month cached for Q&A agent turns at casual use. The dominant monthly line item is still Opus daily-report generation (~$30/month) and that's per-day, uncacheable across days.

---

## Suggestions to bundle into the memory-hardening sub-project

These are the seven token-cost wins we discussed. They're decoupled from each other — the sub-project can pick any subset. Numbered in the order they were presented so the next session can refer to them by index.

### 1. Move durable state into the cached `system` block; keep transcript small

Rule of thumb to respect for the new user profile layer: anything that changes <1×/day goes into the system prompt and benefits from prompt cache. Anything that changes per-turn stays in `messages`. Memory hardening must not accidentally put volatile state in the cached block (breaks the cache) nor durable state in the volatile path (pays tokens every turn).

### 2. Prune the persona files

10 KB personas are the worst offender, especially Manager's at ~3000 tokens × every turn × every day. Likely 30–50% can be cut without quality loss by removing example dialogues, redundant rules, and edge-case reminders.

**Targets, in priority order:**
- `prompts/manager.md` — biggest file, highest call frequency
- `prompts/intelligence.md` — big, medium call frequency
- `prompts/secretary.md` — smallest, but also called often

**Approach:** diff the persona against actual observed behavior from the messages table — sections the model never seems to act on are deletion candidates.

### 3. Shrink `transcript_window`

Current values:
- `prompts/secretary.toml` → `transcript_window = 20`
- `prompts/manager.toml` → `transcript_window = 20`
- `prompts/intelligence.toml` → `transcript_window = 10`

Hypothesis: 20 is overkill; recent turns dominate signal. Test 10 for a few days; if agents still feel coherent, keep it. Saves ~1000 tokens per turn on Secretary and Manager.

### 4. Lazy memory access via tools, not eager inlining

For user profile / blackboard / formal KB reads: expose them as tools (`get_user_profile()`, `recall_about(topic)`, `kb_query(q)`) that the agent calls on demand, rather than dumping the full profile into every system prompt.

**Trade-off:** adds one extra round-trip on turns that need the context; saves tokens on the majority of turns that don't. Good fit for pieces of state that are only relevant to a minority of turns (KB facts, long-tail preferences). Bad fit for things needed every turn (the user's name, timezone, current active projects) — those belong inlined.

### 5. Summarize old transcript turns rather than dropping them

When a conversation exceeds `transcript_window`, instead of dropping the oldest turns, roll them into a compact summary paragraph kept in the transcript. Preserves long-range context at a fraction of the tokens. Best paired with the blackboard so one agent can read another's summary without re-deriving it.

**Shape:** when the transcript would overflow, produce a 1–2 sentence summary of the N oldest turns, store it as a `from_kind=system` envelope, and include it as the first `messages` entry in subsequent turns. The summarization pass itself costs tokens — worth it only if the summary lasts long enough to amortize.

### 6. Prune the injected Intelligence report

Intelligence currently injects the **full** latest DailyReport JSON (~2500 tokens) on every Q&A turn. Alternative: inject only `{date, [headline + item_id]}` (~500 tokens) and let the model call `get_report_item(item_id)` when it wants to discuss a specific item in depth.

**Saves:** ~2000 tokens per Intelligence Q&A turn that doesn't deep-dive into multiple items. Cost: one extra tool round-trip on turns that do.

### 7. Tool-spec pruning (lower priority)

Manager and Intelligence expose all their tools on every call. Some tools are rarely relevant (e.g., `get_report_link` only matters when the user asks for a link). A lightweight intent classifier could gate which tools get exposed, cutting ~30–50% of tool-spec tokens on most turns.

**Not urgent** — the tool surface isn't that bloated yet. Revisit when Manager's tool list passes ~10 entries.

---

## Things I'd also consider bundling

These came up in the same discussion but weren't explicitly in the user's 1–7 list. Mentioned here so the next session doesn't miss them.

### Extended prompt cache (1-hour TTL)

Anthropic offers a longer-TTL cache tier at a higher first-call cost. Usage pattern "a few turns, gap, a few more turns" — the second burst still hits the cache. Trivial config change in `AnthropicProvider.complete`.

### Model tiering for cheap work

Secretary's listener mode (background reads to decide whether to speak) probably doesn't need Sonnet. Dropping it to Haiku 4.5 is ~5× cheaper per call. Not memory-related, but bundles well with a cost-cut pass.

---

## Master-spec scope anchors

From `2026-04-13-multi-agent-skeleton-design.md`:

- **Layer A (user profile)** — explicitly deferred in the skeleton; master spec says "added when Manager has real planning logic." That's now.
- **Richer blackboard semantics** — current blackboard is append-only with open-string `kind`. Master implies this gets structured as sub-projects mature.
- **Possible Postgres migration** — flagged as "when concurrent writers (WebUI + agents) become a real concern." WebUI isn't built yet; defer the Postgres call.
- **`store.py` is a trust boundary** — any new memory APIs must enforce per-agent isolation through the Python API surface, not at the SQL layer.

---

## Open questions for the brainstorm

1. **What is "user profile" actually?** Name, timezone, roles, current projects, values, communication preferences, relationships, working hours — all of the above? What's minimum viable?
2. **How does it get written?** Automatically by agents from conversations (Learning agent's job, but Learning isn't built), manually via a config file, or both?
3. **Who reads it?** All agents? Only Manager? Read-only vs read-write?
4. **How does it interact with the existing `agent_memory` table?** That's per-agent private memory. User profile is user-level, shared. Probably a new table (`user_profile` or `user_facts`).
5. **How do we measure the cost cut?** Need a baseline: instrument `AnthropicProvider` to log `input_tokens` / `output_tokens` / `cache_read_input_tokens` per call for a week before and after changes.
6. **What happens to existing conversations?** If we shrink `transcript_window`, old conversations that depended on the larger window may feel different. Probably fine but worth a thought.

---

## The next sub-project is NOT

- Two-source Intelligence generation (the "new 6f" we discussed during 6e brainstorming) — that's an Intelligence-specific enhancement, not part of the master plan. It can happen later.
- WebUI control panel — master spec puts this after memory hardening.
- Learning / Supervisor agents — after WebUI.

---

## Files that will probably be touched

Based on the scope:

- `src/project0/store.py` — new table + API for user profile (trust boundary — extra care)
- `src/project0/agents/{manager,secretary,intelligence}.py` — consume the profile
- `prompts/{manager,secretary,intelligence}.md` — pruned personas
- `prompts/{manager,secretary,intelligence}.toml` — `transcript_window` changes
- `src/project0/llm/provider.py` — usage logging / extended cache TTL
- Possibly: `src/project0/envelope.py` — summary envelope variant for suggestion #5
- New: a config or seed file for the initial user profile

Tests: probably +30–50 new tests across store, agents, and provider logging.

---

## Rough sizing

Comparable to 6d or 6e in code volume. Lower conceptual complexity than those (no new infrastructure packages), higher discipline requirements (trust boundary changes, cache-correctness, cost measurement). Probably 12–18 discrete plan tasks.
