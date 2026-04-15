# MAAS — Multi-Agent Assistant System

A single-process Python implementation of a personal multi-agent assistant. Three Telegram bots back three distinct agents, sharing a single orchestrator and a single SQLite envelope log. The agents speak Chinese in character.

> **Status:** sub-projects 6a (Secretary), 6b (Google Calendar), 6c (Manager + pulse), and 6d (Intelligence) all merged. All three agents are real LLM-backed Claude tool-use agents.

## What it does today

- **Manager (林夕)** — the planner. A real Claude tool-use agent with read/write access to your Google Calendar via four calendar tools (list, create, update, delete) and two delegation tools (to Secretary, to Intelligence). Composed and precise; understands time words like "明天" / "下午" / "一会" relative to your sleep rhythm, not the calendar rollover. Big public/private contrast: professional in group chats, wife-like warmth in DMs.
- **Secretary** — the warm conversational companion. Passively observes group chats with a rich cooldown gate, replies to `@mentions` and DMs in character, delivers reminders that Manager delegates to her, and gets playfully jealous in groups when she sees the user interacting with Intelligence or Manager.
- **Intelligence (顾瑾)** — the briefing specialist. A real Claude tool-use agent that ingests tweets from a static Twitter/X watchlist via [twitterapi.io](https://twitterapi.io), generates a structured **DailyReport** JSON file through a one-Opus-call deterministic pipeline, and answers questions about the latest report through a Sonnet tool-use loop. Four tools: `generate_daily_report`, `get_latest_report`, `get_report`, `list_reports`. Never delegates. Reports live at `data/intelligence/reports/YYYY-MM-DD.json` and are `cat`/`jq`-friendly.
- **Pulse primitive** — generic per-agent scheduled wake-up. Manager has one pulse entry (`check_calendar`, every 30 min by default) that scans the next 60 minutes of calendar events and proactively asks Secretary to deliver a reminder when something is coming up. The orchestrator and pulse loader are domain-agnostic; adding a new pulse to any agent is a one-line TOML edit.
- **Shared agentic tool-use loop** — `src/project0/agents/_tool_loop.py` holds the iterate-`complete_with_tools`-dispatch-append body used by both Manager and Intelligence. Each agent supplies its own `dispatch_tool` callable and applies its own finalization rules (pulse suppression, delegation-wins, etc).
- **DM-scoped transcript loader** — `MessagesStore.recent_for_dm(chat_id, agent, limit)` isolates per-agent DM transcripts. Telegram assigns the same `chat_id` (= user's Telegram user id) to every 1:1 DM the user opens with any bot, so a naive `recent_for_chat(chat_id)` query mixes Intelligence's DM transcript with Secretary's. `recent_for_dm` additionally filters on `(from_agent OR to_agent)` so each agent only sees its own DM conversation. Group-chat transcripts intentionally stay shared.
- **Routing & focus** — `@mention` switches focus, last-mentioned agent wins, sticky focus until the next mention. Delegation never switches focus (so a "remind me of X" request doesn't silently reassign the chat). `chat_focus` is wiped on every process restart.
- **Audit log** — every envelope (user message, agent reply, internal delegation, listener observation, pulse tick) is written to `data/store.db` as a JSON blob with parent-child links. Inspectable with `sqlite3` for postmortems.

## Architecture at a glance

```
Telegram                  Orchestrator                Agents
─────────                 ────────────                ──────
manager bot ─┐                                       Manager (LLM + calendar tools)
secretary bot├─→  poller ─→ allow-list ─→ envelope ─→ Secretary (LLM + cooldown)
intel bot    ┘                  ↓             ↓       Intelligence (LLM + Twitter + report tools)
                            content dedup   focus  ↘
                                ↓                   tool calls → GoogleCalendar, twitterapi.io, filesystem
                            messages table              ↓
                                ↑                   reply / delegate
                                └── PULSE_REGISTRY ──── pulse scheduler (asyncio)
```

Design docs (worth reading in order):

- `docs/superpowers/specs/2026-04-13-multi-agent-skeleton-design.md` — orchestrator, envelope shape, focus routing, listener fan-out
- `docs/superpowers/specs/2026-04-13-secretary-design.md` — Secretary's persona, cooldown gate, four entry paths
- `docs/superpowers/specs/2026-04-14-google-calendar-integration-design.md` — `GoogleCalendar` async client, OAuth flow, model translation
- `docs/superpowers/specs/2026-04-14-manager-agent-and-pulse-design.md` — Manager's tool-use loop, pulse primitive, `complete_with_tools` provider extension
- `docs/superpowers/specs/2026-04-15-intelligence-agent-design.md` — Intelligence agent, `TwitterSource` protocol, deterministic generation pipeline, daily report schema, Q&A tool surface, dual-LLM wiring (Opus summarizer + Sonnet QA)

Implementation plans (matching specs) live in `docs/superpowers/plans/`.

## One-time setup

### 1. Python environment

```bash
uv sync
```

### 2. Telegram bots

In Telegram, talk to `@BotFather`:

1. `/newbot` × 3 — create one bot each for Manager, Secretary, Intelligence. Save all three tokens.
2. For **each** bot run `/setprivacy` → pick the bot → **Disable**. Required so bots in groups see every message, not only `@mentions`.
3. Create a Telegram group, add all three bots, send a test message.
4. Find the group's `chat_id` (negative, starts with `-100…` for supergroups). The simplest way is to start the project with `LOG_LEVEL=DEBUG` once and watch the orchestrator log lines.
5. Find your own Telegram numeric user id by talking to `@userinfobot`.

### 3. Google Cloud (for Manager's calendar tools)

One-time, ~5 minutes via https://console.cloud.google.com:

1. Create a project (or reuse one). Enable the **Google Calendar API** in the API Library.
2. Configure the OAuth consent screen as **External**, app name `MAAS`, user support email = your email, scope `https://www.googleapis.com/auth/calendar.events`, add your own Google account as a Test user. Leave the app in **Testing** mode forever — that's correct for a personal tool.
3. Create OAuth 2.0 credentials, type **Desktop app**, name it freely, download the JSON.
4. Save the JSON as `data/google_client_secrets.json`. (`data/` is gitignored.)

You'll be prompted to authorize once on the first run; the resulting refresh token is cached at `data/google_token.json` automatically.

To revoke later: https://myaccount.google.com/permissions → MAAS → Remove access. Then `rm data/google_token.json`.

### 4. twitterapi.io (for Intelligence's Twitter/X ingestion)

Sign up at https://twitterapi.io and grab an API key. Pay-as-you-go at ~$0.15 per 1,000 tweets — at Intelligence's daily-report volume (20-handle watchlist × ~10 tweets/day) the total cost is a few cents per day. No monthly minimum.

### 5. `.env`

```bash
cp .env.example .env
```

Fill it in:

```env
# Telegram bot tokens (one per agent)
TELEGRAM_BOT_TOKEN_MANAGER=...
TELEGRAM_BOT_TOKEN_INTELLIGENCE=...
TELEGRAM_BOT_TOKEN_SECRETARY=...

# Allow-lists. Required — without these the orchestrator refuses to start.
TELEGRAM_ALLOWED_CHAT_IDS=-100123456789
TELEGRAM_ALLOWED_USER_IDS=12345678

# LLM
ANTHROPIC_API_KEY=sk-ant-...
LLM_PROVIDER=anthropic               # default; "fake" uses FakeProvider for tests
LLM_MODEL=claude-sonnet-4-6          # default for Manager and Secretary

# Storage + logging
STORE_PATH=data/store.db
LOG_LEVEL=INFO

# Google Calendar (sub-project 6b)
USER_TIMEZONE=Asia/Shanghai          # any IANA zone name
GOOGLE_CALENDAR_ID=primary           # or a non-primary calendar id
# GOOGLE_TOKEN_PATH=data/google_token.json            # default
# GOOGLE_CLIENT_SECRETS_PATH=data/google_client_secrets.json   # default

# Manager pulse target (sub-project 6c)
# Telegram chat id where check_calendar reminders are delivered. Must
# match one of TELEGRAM_ALLOWED_CHAT_IDS. Comment this out to disable
# the pulse entry entirely (the loader will then fail loudly at startup,
# which is what you want).
MANAGER_PULSE_CHAT_ID=-100123456789

# Intelligence agent (sub-project 6d)
# twitterapi.io auth key. Required at startup — main.py fails loudly
# if it's missing.
TWITTERAPI_IO_API_KEY=...
```

Intelligence uses two separate Anthropic models, both driven by the same `ANTHROPIC_API_KEY` but constructed as independent `AnthropicProvider` instances in `main.py`:

- Summarizer (`claude-opus-4-6`, 16k max tokens) — runs the one-shot daily report generation call. Configurable via `[llm.summarizer]` in `prompts/intelligence.toml`.
- Q&A (`claude-sonnet-4-6`, 2k max tokens) — drives the agentic tool-use chat loop. Configurable via `[llm.qa]` in the same file.

The seed watchlist of 20 tech/AI accounts lives under `[[watch]]` in `prompts/intelligence.toml`. Edit it by hand to add or remove sources — 6d is deliberately static; dynamic follow-management via chat is deferred to the feedback-loop sub-project.

## Running

```bash
uv run python -m project0.main
```

Healthy startup logs roughly look like:

```
INFO project0 :: secretary registered (model=claude-sonnet-4-6)
INFO project0 :: chat_focus cleared on startup
INFO project0 :: google calendar ready (calendar_id=primary tz=Asia/Shanghai)
INFO project0 :: manager registered (model=claude-sonnet-4-6)
INFO project0 :: intelligence registered (summarizer=claude-opus-4-6, qa=claude-sonnet-4-6, watchlist=20)
INFO project0 :: manager pulse entries: [('check_calendar', 1800)]
INFO project0 :: bot manager polling
INFO project0 :: bot secretary polling
INFO project0 :: bot intelligence polling
INFO project0 :: pulse task spawned: check_calendar
```

Leave the process running. Talk to the bots from your Telegram client.

## What to try

In the allow-listed Telegram group:

- **Manager queries.** `@manager 我明天有什么事？` — Manager calls `calendar_list_events` and reports your real events.
- **Edits.** `@manager 把下个活动改名成「和教授吃早餐」` — direct edit, no confirmation prompt; Manager reports `搞定` after the patch lands.
- **Deletes.** `@manager 把下个活动删了` — same, no confirmation.
- **Creates.** `@manager 添加：明天下午三点和导师开会一小时` — created without prompting because all required fields are present.
- **Manual reminder.** `@manager 提醒我下一个活动` — Manager delegates to Secretary, who posts a warm reminder in her own voice.
- **Proactive pulse reminder.** Create a calendar event for ~30 minutes from now via Google Calendar. Wait for the next pulse tick (default 30 min — lower `every_seconds` in `prompts/manager.toml` to `60` for fast testing). Secretary posts a proactive reminder in the group with no prompting.
- **Secretary directly.** `@secretary 你好` for a Chinese reply, or DM the Secretary bot directly for a more personal tone.
- **Intelligence report.** DM the Intelligence bot `生成今天的报告`. Takes 30-90 seconds (one Opus call + ~20 Twitter fetches), then writes `data/intelligence/reports/YYYY-MM-DD.json` and replies with an ack (item count, tweets fetched, handles that failed).
- **Intelligence Q&A.** After a report exists, DM Intelligence with `今天最要紧的是什么？` — she answers from the pre-loaded report and cites source tweet URLs. Ask `昨天有什么` and she calls `get_report(date=yesterday)` to look back.
- **Manager → Intelligence delegation.** `@manager 帮我看看最近有什么 AI 模型发布` — Manager calls `delegate_to_intelligence`, Intelligence answers in her own voice via the handoff chain.

## Inspecting the audit log

Every envelope is in `data/store.db`. Useful queries:

```bash
# Last 20 envelopes, terse view
sqlite3 data/store.db "SELECT id, parent_id, source, from_kind, from_agent, to_agent,
  json_extract(envelope_json, '\$.routing_reason') AS reason
  FROM messages ORDER BY id DESC LIMIT 20;"

# Just pulse envelopes
sqlite3 data/store.db "SELECT id, ts, json_extract(envelope_json, '\$.payload') AS payload
  FROM messages WHERE source='pulse' ORDER BY id DESC LIMIT 20;"
```

Intelligence's daily reports live as plain JSON files separate from the envelope log:

```bash
ls data/intelligence/reports/
cat data/intelligence/reports/$(date +%F).json | jq '.news_items[] | {id, headline, importance}'
```

## Intelligence webapp (6e)

The Intelligence agent ships with a FastAPI webapp that renders daily
reports in your browser. Run `uv run python -m project0.main` and the webapp
starts alongside the Telegram bots on the port configured in
`prompts/intelligence.toml` (default `8080`).

**Access from your phone via Tailscale.** The webapp binds to `0.0.0.0:8080`
by default. Install Tailscale on both the server machine and your phone, log
in on both with the same account, and open
`http://<machine>.<tailnet>.ts.net:8080/` in your phone's browser. It works
on cellular, coffee-shop WiFi, anywhere — Tailscale handles the tunneling
and provides the DNS name. No port forwarding, no TLS cert management, no
auth wiring.

**URLs:**
- `/` — latest report (auto-picks newest on disk)
- `/reports/YYYY-MM-DD` — specific report by date
- `/history` — browsable list of all reports grouped by month
- `/api/feedback/thumbs` — POST thumbs events
- `/healthz` — liveness probe

**顾瑾 can send you links.** In Telegram, ask "把今天的日报发给我" or
"send me today's report" — Intelligence calls the `get_report_link` tool
and returns a URL you can tap.

**Feedback.** Each news item has thumbs up/down buttons. Clicks are recorded
to `data/intelligence/feedback/YYYY-MM.jsonl` as an append-only event log.
6e captures the signal only; reading it back to influence generation or
memory is a later sub-project.

**Configuration** (`[web]` section in `prompts/intelligence.toml`):
- `public_base_url` — URL 顾瑾 uses when building report links. Must start
  with `http://` or `https://`. **Update this to your Tailscale hostname.**
- `bind_host` — default `0.0.0.0` (all interfaces)
- `bind_port` — default `8080`
- `reports_dir`, `feedback_dir` — filesystem paths
- `user_tz` — timezone for feedback event timestamps and the report meta line

`[llm.summarizer]` also gained `thinking_budget_tokens` (default `16384`)
which enables Claude Opus extended thinking on the daily-report generation
call. This bumps report quality at a small cost increment (~$0.24/report).

**Dev workflow** — run just the webapp with live reload on port 8081 for
template/CSS iteration (doesn't start the Telegram bots):

```bash
./scripts/dev_web.sh
```

**Smoke test** — spins up a temporary server on port 18080, exercises all
routes against a seeded fake report, cleans up:

```bash
./scripts/smoke_web.sh
```

**Security note.** There is no auth, TLS, rate limiting, or CSRF protection.
The security model is "Tailscale is the gate". **Do not expose port 8080 to
the public internet.** Verify your firewall (`sudo ufw status` or equivalent)
does not forward port 8080 from the outside.

## Tests

```bash
uv run pytest -q              # 256 unit + orchestrator tests, all hermetic; 1 skipped (live twitterapi.io smoke)
uv run mypy src/project0
uv run ruff check src tests
```

There are no live-API tests in the default unit suite. One optional live test (`tests/intelligence/test_twitterapi_io_live.py`) is gated on `TWITTERAPI_IO_API_KEY` and fetches a small number of real tweets; it's skipped without the env var.

The scripts in `scripts/` exercise real APIs manually:

- `scripts/calendar_smoke.py` — seven-step end-to-end calendar test (read, create, update, get, delete, error path). Creates a real test event on your real calendar; cleans up via `atexit` even if interrupted.
- `scripts/inject_reminder.py` — synthesizes a Manager-delegated reminder envelope and prints Secretary's response without going through Telegram.
- `scripts/smoke_generate_report.py` — drives Intelligence's `generate_daily_report` against real twitterapi.io + real Anthropic Opus. Does NOT touch Telegram or `load_settings()`. Reads `ANTHROPIC_API_KEY` and `TWITTERAPI_IO_API_KEY` from `.env`. Writes a real report to `data/intelligence/reports/`.
- `scripts/diagnose_chat_leakage.py` — dumps the last N envelopes grouped by `telegram_chat_id`, useful for investigating cross-agent DM leakage or transcript boundary issues.

## Reset

```bash
rm data/store.db                          # wipes the envelope log
rm -rf data/intelligence/reports/         # wipes daily reports (gitignored)
```

The schema is recreated on next startup. Idempotent additive migrations handle column adds across sub-projects, so you don't normally need to wipe.

To also drop Google auth: `rm data/google_token.json` (next startup will re-prompt the browser flow).

## Project layout

```
src/project0/
├── main.py                # composition root: Settings → Store → agents → bots → pulse loops
├── orchestrator.py        # routing, focus, dedup, delegation, pulse dispatch
├── envelope.py            # the Envelope dataclass + AgentResult
├── store.py               # SQLite messages table, agent_memory, chat_focus, recent_for_dm
├── pulse.py               # PulseEntry, load_pulse_entries, run_pulse_loop, build_pulse_envelope
├── config.py              # Settings dataclass + load_settings (env + .env)
├── telegram_io.py         # python-telegram-bot wrappers, FakeBotSender
├── mentions.py            # @mention parser
├── llm/
│   ├── provider.py        # LLMProvider Protocol, FakeProvider, AnthropicProvider
│   └── tools.py           # ToolSpec / ToolCall / ToolUseResult / msg variants
├── agents/
│   ├── _tool_loop.py      # shared run_agentic_loop + TurnState + LoopResult (Manager + Intelligence)
│   ├── registry.py        # AGENT_REGISTRY, LISTENER_REGISTRY, PULSE_REGISTRY, register_*
│   ├── manager.py         # Manager class (林夕): calendar tools + agentic loop + pulse
│   ├── secretary.py       # Secretary class: cooldown gate + four entry paths + DM-scoped loader
│   └── intelligence.py    # Intelligence class (顾瑾): four report tools + agentic loop
├── intelligence/          # Intelligence infrastructure (sub-project 6d)
│   ├── source.py          # TwitterSource protocol + Tweet dataclass + TwitterSourceError
│   ├── fake_source.py     # FakeTwitterSource for tests
│   ├── twitterapi_io.py   # TwitterApiIoSource concrete HTTP client
│   ├── watchlist.py       # WatchEntry + load_watchlist
│   ├── report.py          # DailyReport schema + validator + atomic_write_json + readers
│   ├── summarizer_prompt.py  # SUMMARIZER_SYSTEM_PROMPT + three user-prompt builders
│   └── generate.py        # deterministic generate_daily_report pipeline
└── calendar/
    ├── auth.py            # OAuth installed-app flow
    ├── client.py          # async wrapper around google-api-python-client
    ├── model.py           # CalendarEvent + raw ↔ model translation
    └── errors.py          # GoogleCalendarError

prompts/
├── manager.md / .toml       # 林夕 persona + LLM/pulse config
├── secretary.md / .toml     # Secretary persona + cooldown thresholds
└── intelligence.md / .toml  # 顾瑾 persona + dual-LLM config + seed watchlist

data/
├── store.db                      # envelope log (gitignored)
├── google_client_secrets.json    # OAuth app (gitignored)
├── google_token.json             # OAuth refresh token (gitignored)
└── intelligence/reports/         # daily report JSON files (gitignored)

docs/superpowers/
├── specs/                 # per-sub-project design specs (read in date order)
└── plans/                 # per-sub-project TDD implementation plans
```

## Roadmap

- **6e** — ✅ Intelligence delivery surface. FastAPI webapp rendering reports with history browsing, thumbs feedback capture, `get_report_link` agent tool, extended thinking on the Opus summarizer.
- **6f** — Two-source generation: dedicated intel Twitter account + dynamic follows fetching + automatic discovery via Twitter search queries.
- **6g** — Pulse integrations for Intelligence: scheduled daily reports + user-defined ad-hoc watch pulses ("check Iran news every 10 min", "new model releases every 3 days").
- **6h** — Feedback loop + preference learning. Per-entry thumbs-up, dynamic follow/unfollow via chat, trained ranking.
- **Supervisor agent** — audit, scoring, incident investigation (long-term).
- **Multi-process safety** — pulses currently assume one orchestrator process; a `pulse_leases` table is the planned fix.
- **WebUI control panel** — design exists in `Project 0: Multi-agent assistant system.md`; implementation TBD.

The full Project 0 architecture document — agent responsibilities, memory layers (A–F), governance model — lives at `Project 0: Multi-agent assistant system.md` in the repo root.
