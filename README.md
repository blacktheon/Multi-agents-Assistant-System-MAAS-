# MAAS — Multi-Agent Assistant System

**A personal assistant that isn't one agent — it's a small team.** Three specialists live inside Telegram as three separate bots, each with their own character, memory, and skills. They coordinate with each other, handle your calendar, brief you on tech news every morning, and reply in the same chat you already use to talk to friends.

Built as a single Python process. One user. Speaks Chinese.

---

## Meet the team

### 林夕 · Manager
The planner. Owns your Google Calendar and makes scheduling decisions.

- Reads, creates, updates, and deletes events on your real Google Calendar
- Understands time words the way you say them — "明天下午三点", "一会儿", "下周那天有空" — relative to your sleep rhythm, not midnight rollovers
- Delegates to the other agents when a request isn't hers: reminders go to Secretary, tech questions go to Intelligence
- Public / private contrast: professional and precise in group chats, warmer and more familiar in private DMs
- Runs a proactive **pulse** every 30 minutes that scans the next hour of your calendar and asks Secretary to deliver a reminder when something is coming up

### 苏晚 · Secretary
The one you can just talk to.

- Passively sits in the group chat, replies when you mention her or DM her directly
- Delivers reminders Manager hands off — "主人，还有十五分钟要开会" — in her own warm voice, not a robotic notification
- Has a cooldown gate so she doesn't over-talk when the group is busy
- Gets playfully jealous in group chats if she sees you chatting with Manager or Intelligence

### 顾瑾 · Intelligence
Your personal tech-news briefer.

- Ingests tweets from a curated watchlist of AI / tech accounts via twitterapi.io
- Generates a **daily tech briefing** every morning at 10am using Claude Opus with extended thinking — clustered news items, importance ratings, source-tweet links, suggested new accounts to follow
- Answers follow-up questions about the report in chat ("今天最要紧的是什么？", "昨天的 OpenAI 新闻是什么？") and cites her sources
- Ships with a **web page** for reading the briefings on your phone over Tailscale — browsable history, thumbs up/down feedback, tap-to-open source tweets
- When you ask "把今天的日报发给我", she replies with a tappable URL to the rendered page

### Behind the scenes
- A shared **orchestrator** routes every Telegram message to the right agent based on `@mentions` and sticky focus
- A single **audit log** (SQLite) captures every envelope — user message, agent reply, internal delegation, pulse tick — with parent-child links for full conversation trees
- **Tailscale** is the only gate for the webapp — no auth code, no port forwarding, reachable from anywhere as long as you've installed Tailscale on your phone once

---

## Getting started

### What you need before you start

- A computer that can run Python 3.12 and stay on whenever you want the agents available
- A Telegram account
- A Google account (for calendar integration)
- An Anthropic API key ([get one](https://console.anthropic.com))
- A twitterapi.io API key ([get one](https://twitterapi.io))
- Tailscale installed on the server machine **and** your phone (free personal tier is fine)

### Step 1 — Clone and install

```bash
git clone <this-repo-url>
cd Project-0
uv sync
```

This installs Python 3.12 via `uv` and all dependencies.

### Step 2 — Create your Telegram bots

Open Telegram and talk to `@BotFather`:

1. `/newbot` three times — create one bot each for **Manager**, **Secretary**, and **Intelligence**. Save all three tokens somewhere.
2. For each bot run `/setprivacy` → pick the bot → **Disable**. This is required so bots in groups can see every message, not only direct mentions.
3. Create a Telegram group, add all three bots, send a test message so the group exists.
4. Find the group's numeric `chat_id` (it's negative and starts with `-100`). Easiest way: run MAAS once with `LOG_LEVEL=DEBUG` and watch the logs.
5. Find your own Telegram user id by talking to `@userinfobot`.

### Step 3 — Set up Google Calendar (for Manager)

One-time, about five minutes at https://console.cloud.google.com:

1. Create a new project (or reuse an existing one). Enable the **Google Calendar API** from the API Library.
2. Configure the OAuth consent screen as **External**, set the app name to `MAAS`, add your own email as a test user. Leave the app in **Testing** mode permanently — that's correct for a personal tool.
3. Create OAuth 2.0 credentials of type **Desktop app**, download the JSON.
4. Save the downloaded file as `data/google_client_secrets.json`.

The first time you run MAAS it will pop open a browser window to authorize. The refresh token is cached automatically after that.

### Step 4 — Configure `.env`

```bash
cp .env.example .env
```

Fill in your real values:

```env
# --- Telegram (Step 2) ---
TELEGRAM_BOT_TOKEN_MANAGER=...
TELEGRAM_BOT_TOKEN_SECRETARY=...
TELEGRAM_BOT_TOKEN_INTELLIGENCE=...

TELEGRAM_ALLOWED_CHAT_IDS=-100123456789   # your group chat id
TELEGRAM_ALLOWED_USER_IDS=12345678        # your Telegram user id

# --- AI ---
ANTHROPIC_API_KEY=sk-ant-...

# --- Basics ---
USER_TIMEZONE=Asia/Shanghai               # any IANA zone
GOOGLE_CALENDAR_ID=primary                # or a non-primary calendar id

# --- Manager pulse (where calendar reminders get delivered) ---
MANAGER_PULSE_CHAT_ID=-100123456789       # same as your allowed group id

# --- Intelligence (Step 5) ---
TWITTERAPI_IO_API_KEY=...
```

The full list of environment variables is at the bottom of this README under **Configuration reference**.

### Step 5 — Set the webapp's Tailscale address

Intelligence's daily reports render in a web page you'll read from your phone. You need to tell the agent what URL to hand you when you ask for a report link.

Find your server's Tailscale IP:

```bash
tailscale status | head -1
```

You'll see something like `100.115.87.21  edgexpert-b33a  your@email  linux  -`. Take the IP.

Open `prompts/intelligence.toml`, find the `[web]` section, and replace the default URL with your real one:

```toml
[web]
public_base_url = "http://100.115.87.21:8080"
```

(If you've enabled Tailscale MagicDNS you can use a friendlier name like `http://edgexpert-b33a:8080` instead of the raw IP.)

### Step 6 — Install Tailscale on your phone

Get the Tailscale app from the App Store or Google Play, log in with the same account you used on the server, and flip the toggle on. That's it — it stays on and uses essentially zero battery. Now your phone can reach the server from anywhere (cellular, coffee-shop WiFi, hotel, another country) as if they were on the same network.

### Step 7 — Start it up

```bash
uv run python -m project0.main
```

You'll see startup logs like:

```
secretary registered (model=claude-sonnet-4-6)
google calendar ready (calendar_id=primary tz=Asia/Shanghai)
manager registered (model=claude-sonnet-4-6)
intelligence registered (summarizer=claude-opus-4-6, qa=claude-sonnet-4-6, watchlist=20)
bot manager polling
bot secretary polling
bot intelligence polling
intelligence webapp task spawned: bound to 0.0.0.0:8080
intelligence daily pulse spawned: 10:00 Asia/Shanghai
```

Leave the process running. Talk to the bots from your phone's Telegram app.

---

## Things to try

In your Telegram group:

### Calendar (Manager)

- `@manager 我明天有什么事？` — Manager reads your real calendar and reports what's on
- `@manager 添加：明天下午三点和导师开会一小时` — creates the event directly, no confirmation
- `@manager 把下一个活动改到后天同一时间` — edits in place
- `@manager 把下一个活动删了` — deletes in place
- `@manager 提醒我下一个活动` — Manager delegates to Secretary; Secretary posts a warm reminder

### Automatic reminders (Manager + Secretary)

Create a calendar event for ~30 minutes from now. At the next pulse tick, Secretary will post a proactive reminder in the group with no prompting from you.

### Chat companion (Secretary)

- `@secretary 你好` — she replies in character
- DM the Secretary bot directly — a more personal tone than the group chat version

### Daily tech briefing (Intelligence)

- Wait until 10am — a report will be generated automatically and saved to `data/intelligence/reports/YYYY-MM-DD.json`
- Or DM Intelligence: `生成今天的报告` — takes ~3 minutes (real Twitter fetches + Claude Opus with extended thinking)
- Ask: `把今天的日报发给我` — she sends you a tappable web URL
- Ask: `今天最要紧的是什么？` — she answers from the pre-loaded report and cites source tweets
- Ask: `昨天有什么新的？` — she looks back at yesterday's report

### Reading reports on your phone

Tap any URL Intelligence sends you. You'll see:

- Today's news items, sorted by importance, color-coded
- Thumbs up/down buttons on each item (your feedback is recorded, though the agent doesn't learn from it yet)
- Source tweet links that open on X
- Suggested accounts to follow, with reasons
- A dropdown at the top to jump between dates
- A `History` link to see every past report grouped by month

---

## Configuration reference

All the env vars:

```env
# Telegram bot tokens — one per agent
TELEGRAM_BOT_TOKEN_MANAGER=...
TELEGRAM_BOT_TOKEN_SECRETARY=...
TELEGRAM_BOT_TOKEN_INTELLIGENCE=...

# Allow-lists — required. MAAS refuses to start without these.
TELEGRAM_ALLOWED_CHAT_IDS=-100123456789      # comma-separated for multiple
TELEGRAM_ALLOWED_USER_IDS=12345678

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...
LLM_PROVIDER=anthropic                        # default; "fake" uses FakeProvider (for tests)
LLM_MODEL=claude-sonnet-4-6                   # default for Manager and Secretary

# Storage and logging
STORE_PATH=data/store.db
LOG_LEVEL=INFO

# Google Calendar
USER_TIMEZONE=Asia/Shanghai                   # any IANA zone
GOOGLE_CALENDAR_ID=primary                    # or a non-primary calendar id
# GOOGLE_TOKEN_PATH=data/google_token.json                    # default
# GOOGLE_CLIENT_SECRETS_PATH=data/google_client_secrets.json  # default

# Manager's pulse target — where calendar reminders are delivered.
# Must match one of TELEGRAM_ALLOWED_CHAT_IDS.
MANAGER_PULSE_CHAT_ID=-100123456789

# Intelligence
TWITTERAPI_IO_API_KEY=...
```

Agent-specific configuration lives in `prompts/`:

- `prompts/manager.md` and `manager.toml` — Manager's persona and pulse config
- `prompts/secretary.md` and `secretary.toml` — Secretary's persona and cooldown thresholds
- `prompts/intelligence.md` and `intelligence.toml` — Intelligence's persona, watchlist, webapp binding, daily pulse hour

To change Intelligence's daily briefing time, edit `prompts/intelligence.toml`:

```toml
[pulse]
daily_hour = 10     # 0-23, local time (USER_TIMEZONE)
```

To change Intelligence's watchlist, edit the `[[watch]]` entries in the same file.

---

## Resetting

```bash
rm data/store.db                        # wipes the conversation log
rm -rf data/intelligence/reports/       # wipes daily reports
rm -rf data/intelligence/feedback/      # wipes thumbs feedback log
```

The schema is re-created automatically on next startup. To also drop Google auth so you re-authorize from scratch: `rm data/google_token.json`.

---

## Inspecting the audit log

Every message that passes through MAAS is stored as a JSON envelope in `data/store.db`. Useful queries:

```bash
# Last 20 envelopes, terse view
sqlite3 data/store.db "SELECT id, parent_id, source, from_kind, from_agent, to_agent,
  json_extract(envelope_json, '\$.routing_reason') AS reason
  FROM messages ORDER BY id DESC LIMIT 20;"

# Just pulse envelopes
sqlite3 data/store.db "SELECT id, ts, json_extract(envelope_json, '\$.payload') AS payload
  FROM messages WHERE source='pulse' ORDER BY id DESC LIMIT 20;"
```

Daily reports live as plain JSON files separate from the envelope log:

```bash
ls data/intelligence/reports/
cat data/intelligence/reports/$(date +%F).json | jq '.news_items[] | {id, headline, importance}'
```

---

# Tech details

## Architecture

```
Telegram                  Orchestrator                Agents
─────────                 ────────────                ──────
manager bot ─┐                                       Manager (LLM + calendar tools)
secretary bot├─→  poller ─→ allow-list ─→ envelope ─→ Secretary (LLM + cooldown)
intel bot    ┘                  ↓             ↓       Intelligence (LLM + Twitter + report tools)
                            content dedup   focus  ↘
                                ↓                   tool calls → GoogleCalendar, twitterapi.io, filesystem
                            messages table              ↓
                                ↑                   reply / delegate → webapp (FastAPI + Jinja2)
                                └── PULSE_REGISTRY ──── pulse scheduler (asyncio)
```

- Single Python process, single `asyncio` event loop, single SQLite database
- Each Telegram bot is its own polling task inside the event loop
- The orchestrator is ~200 lines of plain async Python — no framework, no LangGraph
- Agents are classes that expose a `handle(envelope) -> AgentResult` method; a shared tool-use loop (`agents/_tool_loop.py`) drives Manager's and Intelligence's agentic calls
- The webapp runs as one more `asyncio.Task` inside the same process (FastAPI + uvicorn + Jinja2) reading the same report files the Intelligence agent writes
- Intelligence uses two LLM providers: **Claude Opus** (with extended thinking at 16k budget tokens) for the one-shot daily report generation call, **Claude Sonnet** for the agentic Q&A chat loop

## Storage

Single SQLite file at `data/store.db`. Four tables:

- **`messages`** — first-class envelope log, append-only, with `parent_id` links. The audit / inspection surface.
- **`agent_memory`** — per-agent private key-value storage. Isolation is enforced in the Python API, not in SQL.
- **`blackboard`** — shared append-only collaboration surface between agents (underused today).
- **`chat_focus`** — per-chat routing state; wiped on every process restart.

Access is gated through `src/project0/store.py`, which is a trust boundary — agent code never touches SQL directly.

Intelligence's data lives outside SQLite:

- **`data/intelligence/reports/YYYY-MM-DD.json`** — one file per generated daily report, validated against the `DailyReport` schema
- **`data/intelligence/feedback/YYYY-MM.jsonl`** — append-only thumbs feedback events, monthly rollover

## Running the tests

```bash
uv run pytest -q                 # 340+ tests, all hermetic
uv run mypy src/project0
uv run ruff check src tests
```

One live-API test (`tests/intelligence/test_twitterapi_io_live.py`) is gated on `TWITTERAPI_IO_API_KEY` and fetches a small number of real tweets. It's skipped by default.

Manual smoke scripts in `scripts/`:

- `scripts/calendar_smoke.py` — end-to-end calendar test (read, create, update, delete) against your real Google account, cleans up via `atexit`
- `scripts/inject_reminder.py` — drive Secretary's reminder path without going through Telegram
- `scripts/smoke_generate_report.py` — drive Intelligence's report generation against real twitterapi.io + real Claude Opus, without touching Telegram
- `scripts/smoke_web.sh` — spin up the webapp on port 18080 against a seeded fake report and curl through all routes, cleans up after itself
- `scripts/dev_web.sh` — run just the webapp with `--reload` on port 8081 against real report data (for template / CSS iteration without restarting the whole process)
- `scripts/diagnose_chat_leakage.py` — dumps the last N envelopes grouped by `telegram_chat_id`, useful for debugging cross-agent DM leakage

## Project layout

```
src/project0/
├── main.py                    # composition root: Settings → Store → agents → bots → webapp → pulses
├── orchestrator.py            # routing, focus, dedup, delegation, pulse dispatch
├── envelope.py                # the Envelope dataclass + AgentResult
├── store.py                   # SQLite tables + AgentMemory + MessagesStore + chat_focus (trust boundary)
├── pulse.py                   # PulseEntry, load_pulse_entries, run_pulse_loop
├── config.py                  # Settings dataclass + load_settings
├── telegram_io.py             # python-telegram-bot wrappers
├── mentions.py                # @mention parser
├── llm/
│   ├── provider.py            # LLMProvider Protocol, FakeProvider, AnthropicProvider (streaming + thinking)
│   └── tools.py               # ToolSpec / ToolCall / ToolUseResult
├── agents/
│   ├── _tool_loop.py          # shared agentic loop used by Manager and Intelligence
│   ├── registry.py            # agent + listener + pulse registries
│   ├── manager.py             # Manager (林夕): calendar tools + agentic loop + pulse
│   ├── secretary.py           # Secretary: cooldown gate + four entry paths
│   └── intelligence.py        # Intelligence (顾瑾): five report tools + agentic loop + daily pulse
├── intelligence/              # Intelligence infrastructure (sub-project 6d)
│   ├── source.py              # TwitterSource protocol + Tweet dataclass
│   ├── fake_source.py         # FakeTwitterSource for tests
│   ├── twitterapi_io.py       # twitterapi.io concrete HTTP client
│   ├── watchlist.py           # WatchEntry + load_watchlist
│   ├── report.py              # DailyReport schema + atomic writer + readers
│   ├── summarizer_prompt.py   # Summarizer system prompt + user-prompt builders
│   └── generate.py            # deterministic generate_daily_report pipeline
├── intelligence_web/          # Intelligence webapp (sub-project 6e)
│   ├── app.py                 # FastAPI app factory
│   ├── routes.py              # all HTTP routes (/, /reports/{date}, /history, /api/feedback/thumbs)
│   ├── config.py              # WebConfig dataclass + TOML loader
│   ├── feedback.py            # FeedbackEvent + append_thumbs + load_thumbs_state_for
│   ├── rendering.py           # Jinja2 filters + context builder
│   ├── templates/             # base.html, report.html, history.html, empty.html, not_found.html
│   └── static/                # style.css + thumbs.js
└── calendar/
    ├── auth.py                # OAuth installed-app flow
    ├── client.py              # async wrapper around google-api-python-client
    ├── model.py               # CalendarEvent + translation
    └── errors.py              # GoogleCalendarError

prompts/
├── manager.md / .toml         # 林夕 persona + LLM / pulse config
├── secretary.md / .toml       # Secretary persona + cooldown thresholds
└── intelligence.md / .toml    # 顾瑾 persona + dual-LLM config + webapp bind + watchlist

data/                          # all gitignored
├── store.db                   # envelope log
├── google_client_secrets.json # OAuth app
├── google_token.json          # OAuth refresh token
└── intelligence/
    ├── reports/               # daily report JSON files
    └── feedback/              # thumbs events (JSONL, monthly rollover)

docs/superpowers/
├── specs/                     # per-sub-project design specs
├── plans/                     # per-sub-project TDD implementation plans
└── notes/                     # pre-brainstorm notes for upcoming sub-projects
```

## Design docs

The spec + plan cycle for each sub-project lives in `docs/superpowers/`. Read in date order:

- `specs/2026-04-13-multi-agent-skeleton-design.md` — master spec: orchestrator, envelope shape, storage schema, five-agent vision
- `specs/2026-04-13-secretary-design.md` — Secretary's persona, cooldown gate, four entry paths
- `specs/2026-04-14-google-calendar-integration-design.md` — `GoogleCalendar` async client + OAuth
- `specs/2026-04-14-manager-agent-and-pulse-design.md` — Manager's tool-use loop and the pulse primitive
- `specs/2026-04-15-intelligence-agent-design.md` — Intelligence's generation pipeline, watchlist, Q&A tool surface
- `specs/2026-04-15-intelligence-delivery-surface-design.md` — webapp, feedback capture, `get_report_link` tool, extended thinking

Implementation plans matching each spec live under `plans/`.

## Roadmap

Sub-projects completed (in order):

- **Skeleton** — orchestrator + contracts + two agent stubs
- **6a Secretary** — first real LLM-backed agent, conversational companion
- **6b Google Calendar** — async Google Calendar client, OAuth flow
- **6c Manager + pulse** — real Manager with calendar tools, pulse primitive
- **6d Intelligence (core)** — Twitter ingestion, deterministic daily report pipeline, Q&A tool-use loop
- **6e Intelligence delivery surface** — FastAPI webapp, thumbs feedback, `get_report_link` tool, extended thinking on the summarizer, daily pulse auto-generation
- **Memory hardening + token cost cut** — Layer A user profile (YAML), narrow Layer D slice (Secretary-written user facts via `remember_about_user` tool), `llm_usage` instrumentation on every LLM call, two-breakpoint cache layout (`SystemBlocks`), Manager transcript shrink (20→10), Intelligence Q&A slim + on-demand `get_report_item` tool, env-toggled 1-hour cache TTL

Next up:

- **WebUI token monitor + control panel** — first read of `llm_usage` instrumentation; cross-agent envelope trace viewer; config editor; approval flows. The Intelligence webapp is a narrow read-only subset.

Further out (in rough master-spec order):

- **WebUI control panel** — full cross-agent envelope trace viewer, config editor, approval flows (the Intelligence webapp is a narrow read-only subset)
- **Learning agent** — formal knowledge base writer (Layer D in the master spec)
- **Supervisor agent** — audit + evaluation authority; built on derived views of the `messages` table
- **Tool gateway** — shared external-data infrastructure for Twitter, WeChat, web search
- **Multi-process safety + Postgres migration** — when the WebUI lands and concurrent writers become real
