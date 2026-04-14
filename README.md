# MAAS — Multi-Agent Assistant System

A single-process Python implementation of a personal multi-agent assistant. Three Telegram bots back three distinct agents, sharing a single orchestrator and a single SQLite envelope log. The agents speak Chinese in character.

> **Status:** sub-projects 6a (Secretary), 6b (Google Calendar), and 6c (Manager + pulse) all merged. Manager and Secretary are real LLM-backed agents; Intelligence is still a stub awaiting sub-project 6d.

## What it does today

- **Manager (林夕)** — the planner. A real Claude tool-use agent with read/write access to your Google Calendar via four calendar tools (list, create, update, delete) and two delegation tools (to Secretary, to Intelligence). Composed and precise; understands time words like "明天" / "下午" / "一会" relative to your sleep rhythm, not the calendar rollover.
- **Secretary** — the warm conversational companion. Passively observes group chats with a rich cooldown gate, replies to `@mentions` and DMs in character, and delivers reminders that Manager delegates to her.
- **Intelligence** — currently a stub. Real implementation is sub-project 6d.
- **Pulse primitive** — generic per-agent scheduled wake-up. Manager has one pulse entry (`check_calendar`, every 30 min by default) that scans the next 60 minutes of calendar events and proactively asks Secretary to deliver a reminder when something is coming up. The orchestrator and pulse loader are domain-agnostic; adding a new pulse to any agent is a one-line TOML edit.
- **Routing & focus** — `@mention` switches focus, last-mentioned agent wins, sticky focus until the next mention. Delegation never switches focus (so a "remind me of X" request doesn't silently reassign the chat). `chat_focus` is wiped on every process restart.
- **Audit log** — every envelope (user message, agent reply, internal delegation, listener observation, pulse tick) is written to `data/store.db` as a JSON blob with parent-child links. Inspectable with `sqlite3` for postmortems.

## Architecture at a glance

```
Telegram                  Orchestrator                Agents
─────────                 ────────────                ──────
manager bot ─┐                                       Manager (LLM + tool use)
secretary bot├─→  poller ─→ allow-list ─→ envelope ─→ Secretary (LLM)
intel bot    ┘                  ↓             ↓       Intelligence (stub)
                            content dedup   focus  ↘
                                ↓                   tool calls → GoogleCalendar
                            messages table              ↓
                                ↑                   reply / delegate
                                └── PULSE_REGISTRY ──── pulse scheduler (asyncio)
```

Design docs (worth reading in order):

- `docs/superpowers/specs/2026-04-13-multi-agent-skeleton-design.md` — orchestrator, envelope shape, focus routing, listener fan-out
- `docs/superpowers/specs/2026-04-13-secretary-design.md` — Secretary's persona, cooldown gate, four entry paths
- `docs/superpowers/specs/2026-04-14-google-calendar-integration-design.md` — `GoogleCalendar` async client, OAuth flow, model translation
- `docs/superpowers/specs/2026-04-14-manager-agent-and-pulse-design.md` — Manager's tool-use loop, pulse primitive, `complete_with_tools` provider extension

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

### 4. `.env`

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
LLM_MODEL=claude-sonnet-4-6          # default

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
```

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
- **Manual reminder.** `@manager 提醒我下一个活动` — Manager delegates to Secretary, who posts a warm reminder in her own voice. Manager itself stays visibly silent during the handoff.
- **Proactive pulse reminder.** Create a calendar event for ~30 minutes from now via Google Calendar. Wait for the next pulse tick (default 30 min — lower `every_seconds` in `prompts/manager.toml` to `60` for fast testing). Secretary should post a proactive reminder in the group with no prompting.
- **Secretary directly.** `@secretary 你好` for a Chinese reply, or DM the Secretary bot directly for a more personal tone. Without a mention, group messages route to whoever currently holds focus (Manager by default after every restart).

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

## Tests

```bash
uv run pytest -q              # ~170 unit + orchestrator tests, all hermetic
uv run mypy src/project0
uv run ruff check src tests
```

There are no live-API tests in the unit suite. The two scripts in `scripts/` exercise the real Anthropic and Google Calendar APIs respectively and are run manually:

- `scripts/calendar_smoke.py` — seven-step end-to-end calendar test (read, create, update, get, delete, error path). Creates a real test event on your real calendar; cleans up via `atexit` even if interrupted.
- `scripts/inject_reminder.py` — synthesizes a Manager-delegated reminder envelope and prints Secretary's response without going through Telegram. Useful for tuning Secretary's reminder voice.

## Reset

```bash
rm data/store.db
```

The schema is recreated on next startup. Idempotent additive migrations handle column adds across sub-projects, so you don't normally need to wipe.

To also drop Google auth: `rm data/google_token.json` (next startup will re-prompt the browser flow).

## Project layout

```
src/project0/
├── main.py                # composition root: Settings → Store → agents → bots → pulse loops
├── orchestrator.py        # routing, focus, dedup, delegation, pulse dispatch
├── envelope.py            # the Envelope dataclass + AgentResult
├── store.py               # SQLite messages table, agent_memory, chat_focus
├── pulse.py               # PulseEntry, load_pulse_entries, run_pulse_loop, build_pulse_envelope
├── config.py              # Settings dataclass + load_settings (env + .env)
├── telegram_io.py         # python-telegram-bot wrappers, FakeBotSender
├── mentions.py            # @mention parser
├── llm/
│   ├── provider.py        # LLMProvider Protocol, FakeProvider, AnthropicProvider
│   └── tools.py           # ToolSpec / ToolCall / ToolUseResult / msg variants
├── agents/
│   ├── registry.py        # AGENT_REGISTRY, LISTENER_REGISTRY, PULSE_REGISTRY, register_*
│   ├── manager.py         # Manager class (林夕): tool dispatch + agentic loop
│   ├── secretary.py       # Secretary class: cooldown gate + four entry paths
│   └── intelligence.py    # stub (sub-project 6d)
└── calendar/
    ├── auth.py            # OAuth installed-app flow
    ├── client.py          # async wrapper around google-api-python-client
    ├── model.py           # CalendarEvent + raw ↔ model translation
    └── errors.py          # GoogleCalendarError

prompts/
├── manager.md / .toml     # 林夕 persona + LLM/pulse config
└── secretary.md / .toml   # Secretary persona + cooldown thresholds

docs/superpowers/
├── specs/                 # per-sub-project design specs (read in date order)
└── plans/                 # per-sub-project TDD implementation plans
```

## Roadmap

- **6d** — Intelligence agent (currently a stub). Twitter/X ingestion, daily briefings, candidate learning material.
- **6e** — Learning agent. Notes, knowledge base, review scheduling.
- **6f** — Supervisor agent. Audit, scoring, incident investigation.
- **Multi-process safety** — pulses currently assume one orchestrator process; a `pulse_leases` table is the planned fix.
- **WebUI control panel** — design exists in `Project 0: Multi-agent assistant system.md`; implementation TBD.

The full Project 0 architecture document — agent responsibilities, memory layers (A–F), governance model — lives at `Project 0: Multi-agent assistant system.md` in the repo root.
