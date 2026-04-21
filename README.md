# MAAS — Multi-Agent Assistant System

**A personal assistant that isn't one agent — it's a small team.** Five specialists live inside Telegram as five separate bots, each with their own character, memory, and skills. They coordinate with each other, handle your calendar, brief you on tech news every morning, manage your knowledge base, review each other's performance, and reply in the same chat you already use to talk to friends.

Built with Python 3.12, FastAPI, SQLite (WAL mode), and Claude (Anthropic API). Ships with a WebUI control panel for managing settings, editing agent personas, monitoring token usage, and reading agent-performance reviews from your phone. Single user. Speaks Chinese.

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
- **Optional local-LLM backend** — flip `SECRETARY_MODE=free` in `.env` to point Secretary at an OpenAI-compatible server (vLLM / TensorRT-LLM) running an abliterated Qwen 2.5 72B on your own hardware. Uses a separate persona file (`secretary_free.md`) and disables the long-term memory tool to keep anything she says from leaking into the other agents' context. Telegram's native "typing…" indicator fills the wait on slow local inference. Flip back to `work` to return to Claude with full memory

### 顾瑾 · Intelligence
Your personal tech-news briefer.

- Ingests tweets from a curated watchlist of AI / tech accounts via twitterapi.io
- Generates a **daily tech briefing** every morning at 10am using Claude Opus with extended thinking — clustered news items, importance ratings, source-tweet links, suggested new accounts to follow
- Answers follow-up questions about the report in chat ("今天最要紧的是什么？", "昨天的 OpenAI 新闻是什么？") and cites her sources
- Ships with a **web page** for reading the briefings on your phone over Tailscale — browsable history, thumbs up/down feedback, tap-to-open source tweets
- When you ask "把今天的日报发给我", she replies with a tappable URL to the rendered page

### 温书瑶 · Learning
Your knowledge curator and review coach.

- Manages your knowledge base through **Notion** — Notion is the source of truth, MAAS keeps a lightweight local index
- Send her a link or paste text → she fetches, summarizes, extracts key points, and saves a structured entry to your Notion database
- Runs a **spaced repetition** review system with fixed intervals (1, 3, 7, 14, 30 days) — reminds you when items are due
- Syncs with Notion every 30 seconds so you can browse, edit, and reorganize entries on your phone and she stays in sync
- Warm older-sister persona — calls you 少爷, gently nudges you to review, celebrates when you complete reviews

### 叶霏 · Supervisor
Your agent-performance reviewer.

- Runs a scheduled **review every 3 hours** across Manager, Intelligence, and Learning (never Secretary — her chats are private), waiting for 5 minutes of chat silence before starting so she doesn't interrupt mid-conversation
- Scores each agent on four dimensions — helpfulness / correctness / tone-consistency / efficiency — with a weighted overall 0-100, a short Chinese critique, and up to three concrete recommendations per review
- Reviews once only: a per-agent cursor tracks the last reviewed envelope id so windows never overlap
- Ask her anything in chat: `@MAAS_supervisor_bot 最近林夕表现怎么样？` → she pulls the stored review rows and narrates them in her voice
- Ask her to run a review now: `帮我跑一次manager的评价` or `全部评一遍` — she calls her review tools on the spot (bypassing the idle gate since you asked), auto-skips agents with no new conversation, never touches Secretary
- Young-undergraduate voice (calls you 欧尼酱, playful and flirty in private chat); switches to a cold, impartial auditor voice when producing the rubric JSON in pulse mode
- History and trends surface in the control panel's new `/reviews` page

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

1. `/newbot` five times — create one bot each for **Manager**, **Secretary**, **Intelligence**, **Learning**, and **Supervisor**. Save all five tokens somewhere.
2. For each bot run `/setprivacy` → pick the bot → **Disable**. This is required so bots in groups can see every message, not only direct mentions.
3. Create a Telegram group, add all five bots, send a test message so the group exists.
4. Find the group's numeric `chat_id` (it's negative and starts with `-100`). Easiest way: run MAAS once with `LOG_LEVEL=DEBUG` and watch the logs.
5. Find your own Telegram user id by talking to `@userinfobot`.

### Step 3 — Set up Google Calendar (for Manager)

One-time, about five minutes at https://console.cloud.google.com:

1. Create a new project (or reuse an existing one). Enable the **Google Calendar API** from the API Library.
2. Configure the OAuth consent screen as **External**, set the app name to `MAAS`, add your own email as a test user. Leave the app in **Testing** mode permanently — that's correct for a personal tool.
3. Create OAuth 2.0 credentials of type **Desktop app**, download the JSON.
4. Save the downloaded file as `data/google_client_secrets.json`.

The first time you run MAAS it will pop open a browser window to authorize. The refresh token is cached automatically after that.

### Step 4 — Set up Notion (for Learning)

1. Go to https://www.notion.so/my-integrations and create a new **internal integration** called "MAAS Knowledge". Copy the "Internal Integration Secret" (starts with `ntn_`).
2. Create a new Notion database (full page) with these columns:

   | Column name | Type | Options |
   |-------------|------|---------|
   | Title | Title | (default first column, rename from "Name") |
   | Source | URL | |
   | Source Type | Select | `link`, `text` |
   | Tags | Multi-select | (leave empty, agent creates them) |
   | User Notes | Text | |
   | Status | Select | `active`, `archived` |

3. On the database page, click `...` → **Connections** → find "MAAS Knowledge" → **Connect**.
4. Copy the database ID from the URL: `https://www.notion.so/workspace/DATABASE_ID_HERE?v=...`

### Step 5 — Configure `.env`

```bash
cp .env.example .env
```

Fill in your real values:

```env
# --- Telegram (Step 2) ---
TELEGRAM_BOT_TOKEN_MANAGER=...
TELEGRAM_BOT_TOKEN_SECRETARY=...
TELEGRAM_BOT_TOKEN_INTELLIGENCE=...
TELEGRAM_BOT_TOKEN_LEARNING=...
TELEGRAM_BOT_TOKEN_SUPERVISOR=...

TELEGRAM_ALLOWED_CHAT_IDS=-100123456789   # your group chat id
TELEGRAM_ALLOWED_USER_IDS=12345678        # your Telegram user id

# --- AI ---
ANTHROPIC_API_KEY=sk-ant-...

# --- Basics ---
USER_TIMEZONE=Asia/Shanghai               # any IANA zone
GOOGLE_CALENDAR_ID=primary                # or a non-primary calendar id

# --- Manager pulse (where calendar reminders get delivered) ---
MANAGER_PULSE_CHAT_ID=-100123456789       # same as your allowed group id

# --- Supervisor pulse target (叶霏 never sends from pulse path,
# but the pulse plumbing requires a valid chat id) ---
SUPERVISOR_PULSE_CHAT_ID=-100123456789

# --- Intelligence (Step 6) ---
TWITTERAPI_IO_API_KEY=...

# --- Learning / Notion (Step 4) ---
NOTION_INTERNAL_INTEGRATION_SECRET=ntn_...
NOTION_DATABASE_ID=...
LEARNING_PULSE_CHAT_ID=-100123456789     # same as your allowed group id
```

The full list of environment variables is at the bottom of this README under **Configuration reference**.

### Step 6 — Set the webapp's Tailscale address

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

### Step 7 — Install Tailscale on your phone

Get the Tailscale app from the App Store or Google Play, log in with the same account you used on the server, and flip the toggle on. That's it — it stays on and uses essentially zero battery. Now your phone can reach the server from anywhere (cellular, coffee-shop WiFi, hotel, another country) as if they were on the same network.

### Step 8 — Start it up

```bash
uv run python -m project0.control_panel
```

This launches the **control panel** on `http://0.0.0.0:8090`. Open it in your browser (or from your phone via your Tailscale IP, e.g. `http://100.x.x.x:8090`), then click the green **▶ Start** button to launch MAAS.

You'll see MAAS startup logs in the same terminal:

```
secretary registered (model=claude-sonnet-4-6)
google calendar ready (calendar_id=primary tz=Asia/Shanghai)
manager registered (model=claude-sonnet-4-6)
intelligence registered (summarizer=claude-opus-4-6, qa=claude-sonnet-4-6, watchlist=20)
learning registered (model=claude-sonnet-4-6)
supervisor registered (model=claude-sonnet-4-6)
supervisor pulse entries: [('review_cycle', 10800), ('review_retry', 60)]
bot manager polling
bot secretary polling
bot intelligence polling
bot learning polling
bot supervisor polling
pulse task spawned: notion_sync
pulse task spawned: review_reminder
intelligence webapp task spawned: bound to 0.0.0.0:8080
intelligence daily pulse spawned: 10:00 Asia/Shanghai
```

From the control panel you can stop, restart, edit agent configs, manage user facts, and monitor token usage — all without touching the terminal again. Ctrl+C stops both the panel and MAAS.

> **Alternative:** You can still run MAAS directly with `uv run python -m project0.main` if you don't need the panel.

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

### Knowledge base (Learning)

- `@learning 帮我学习一下这篇文章 https://example.com/article` — she fetches, summarizes, extracts key points, saves to Notion
- Paste text directly: `帮我学习一下：[your text here]` — she structures it into a knowledge entry
- Add your thoughts: `帮我学习 https://... 我觉得这个对 XXX 项目有用` — your notes get attached to the entry
- `复习计划` — lists what's due for review in the next 7 days
- `已复习 1` — marks an item as reviewed, advances to the next interval
- Every 30 minutes she checks for due reviews and sends a gentle reminder

> **Note:** WeChat article links (`mp.weixin.qq.com`) often require login and can't be scraped. Paste the article text directly instead.

### Agent reviews (Supervisor)

- Every 3 hours, 叶霏 automatically reviews the last block of conversation for Manager, Intelligence, and Learning (never Secretary) — as long as the chat has been quiet for 5 minutes
- `@MAAS_supervisor_bot 最近林夕表现怎么样` — she looks up stored reviews and narrates the scores
- `跑一次manager的评价` or `跑一次林夕` — runs an on-demand review right now, bypassing the idle gate
- `全部评一遍` — reviews all three agents in one turn, auto-skipping anyone with no new conversation since their last review
- Open `http://<your-panel>:8090/reviews` on your phone — chart + per-agent cards + full critique history

### Reading reports on your phone

Tap any URL Intelligence sends you. You'll see:

- Today's news items, sorted by importance, color-coded
- Thumbs up/down buttons on each item (your feedback is recorded, though the agent doesn't learn from it yet)
- Source tweet links that open on X
- Suggested accounts to follow, with reasons
- A dropdown at the top to jump between dates
- A `History` link to see every past report grouped by month

---

## Control panel (WebUI)

A separate web surface for editing every human-tweakable setting and
starting/stopping MAAS without touching a terminal. Runs as its own
process and supervises MAAS as a child.

### Starting the panel

```bash
uv run python -m project0.control_panel
```

Panel binds to `0.0.0.0:8090`. Same Tailscale deployment story as the
Intelligence webapp — reach it from your phone via your Tailnet IP.

### What it does

- **Start / Stop / Restart MAAS** — the panel spawns MAAS as a child
  process; MAAS's lifecycle is in the panel's hands while the panel is
  running.
- **Edit `data/user_profile.yaml`**, **`prompts/*.toml`**,
  **`prompts/*.md`**, and **`.env`** — plain textarea, Save, then click
  Restart on the header to apply. The `/personas` and `/toml` pages list
  every `.md` and `.toml` file currently in `prompts/` (drop a new file
  in and it's immediately editable — no code change needed).
- **Full CRUD on `user_facts`** — add, edit, deactivate/reactivate, and
  hard delete individual facts. Changes are live (shared SQLite in WAL
  mode); no restart required.
- **Token usage page** — daily SVG bar chart + rollup tables for the
  last 30 days, last 7 days by agent × purpose, and the last 50 calls.
  Local-LLM calls (`qwen2.5-72b-awq-8k`) are kept in the audit trail
  but excluded from the dashboard, since they aren't a dollar cost
  and would add noise to a chart that's really about Anthropic spend.
- **Review page** — `/reviews`: multi-line SVG time-series of overall scores, three per-agent cards (latest score + four rubric dims + sparkline + top recommendation), per-agent collapsible history with full critique text. Read-only surface into `supervisor_reviews`.

### Caveat: panel crash with MAAS still running

If the panel process itself dies while MAAS is still running, the panel
on next start sees `stopped` because state is in-memory. If you then
click Start, a second MAAS spawns and fights the first one for Telegram
long-polling. **If the panel says `stopped` but the bots are still
responding in Telegram, SSH in and `pkill -f project0.main` before
clicking Start again.** A PID-file reattach mechanism is a deferred
future improvement.

### No authentication

The panel has no login, no token, no CSRF. Tailscale is the gate. Do
not expose port 8090 outside your Tailnet.

---

## Configuration reference

All the env vars:

```env
# Telegram bot tokens — one per agent
TELEGRAM_BOT_TOKEN_MANAGER=...
TELEGRAM_BOT_TOKEN_SECRETARY=...
TELEGRAM_BOT_TOKEN_INTELLIGENCE=...
TELEGRAM_BOT_TOKEN_LEARNING=...
TELEGRAM_BOT_TOKEN_SUPERVISOR=...

# Allow-lists — required. MAAS refuses to start without these.
TELEGRAM_ALLOWED_CHAT_IDS=-100123456789      # comma-separated for multiple
TELEGRAM_ALLOWED_USER_IDS=12345678

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...
LLM_PROVIDER=anthropic                        # default; "fake" uses FakeProvider (for tests)
LLM_MODEL=claude-sonnet-4-6                   # default for Manager and Secretary

# Secretary mode. `work` (default) = Claude + normal persona + memory tool.
# `free` = local OpenAI-compatible server + secretary_free.{md,toml} + NO
# memory tool (required: prevents anything Secretary produces in free mode
# from leaking into other agents' prompts via the shared user_facts table).
SECRETARY_MODE=work

# Local LLM connection (only used when SECRETARY_MODE=free).
LOCAL_LLM_BASE_URL=http://127.0.0.1:8000/v1
LOCAL_LLM_MODEL=qwen2.5-72b-awq-8k
LOCAL_LLM_API_KEY=unused

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

# Supervisor pulse target (叶霏 never sends from pulse path,
# but the pulse plumbing requires a valid chat id)
SUPERVISOR_PULSE_CHAT_ID=-100123456789

# Intelligence
TWITTERAPI_IO_API_KEY=...

# Notion (Learning agent)
NOTION_INTERNAL_INTEGRATION_SECRET=ntn_...
NOTION_DATABASE_ID=...
LEARNING_PULSE_CHAT_ID=-100123456789
```

Agent-specific configuration lives in `prompts/`:

- `prompts/manager.md` and `manager.toml` — Manager's persona and pulse config
- `prompts/secretary.md` and `secretary.toml` — Secretary's persona and cooldown thresholds (used when `SECRETARY_MODE=work`)
- `prompts/secretary_free.md` and `secretary_free.toml` — alternate persona and tighter token caps for local-LLM mode (used when `SECRETARY_MODE=free`)
- `prompts/intelligence.md` and `intelligence.toml` — Intelligence's persona, watchlist, webapp binding, daily pulse hour
- `prompts/learning.md` and `learning.toml` — Learning's persona, Notion sync interval, review intervals, processing limits
- `prompts/supervisor.md` and `supervisor.toml` — Supervisor's persona and review rubric config (quiet threshold, max wait, per-tick limit, two pulse entries)

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
intel bot    ├                  ↓             ↓       Intelligence (LLM + Twitter + report tools)
learning bot ┘            content dedup   focus  ↘   Learning (LLM + Notion tools + review schedule)
                                ↓                   tool calls → GoogleCalendar, twitterapi.io, Notion API, filesystem
                            messages table              ↓
                                ↑                   reply / delegate → webapp (FastAPI + Jinja2)
                                └── PULSE_REGISTRY ──── pulse scheduler (asyncio)
```

- Single Python process, single `asyncio` event loop, single SQLite database (WAL mode for multi-process access with the control panel)
- Each Telegram bot is its own polling task inside the event loop
- The orchestrator is ~200 lines of plain async Python — no framework, no LangGraph
- Agents are classes that expose a `handle(envelope) -> AgentResult` method; a shared tool-use loop (`agents/_tool_loop.py`) drives Manager's and Intelligence's agentic calls
- The webapp runs as one more `asyncio.Task` inside the same process (FastAPI + uvicorn + Jinja2) reading the same report files the Intelligence agent writes
- Intelligence uses two LLM providers: **Claude Opus** (with extended thinking at 16k budget tokens) for the one-shot daily report generation call, **Claude Sonnet** for the agentic Q&A chat loop

## Storage

Single SQLite file at `data/store.db`. Seven tables:

- **`messages`** — first-class envelope log, append-only, with `parent_id` links. The audit / inspection surface.
- **`agent_memory`** — per-agent private key-value storage. Isolation is enforced in the Python API, not in SQL.
- **`blackboard`** — shared append-only collaboration surface between agents (underused today).
- **`chat_focus`** — per-chat routing state; wiped on every process restart.
- **`knowledge_index`** — lightweight mirror of Notion database page metadata (titles, tags, timestamps). Learning agent's local index.
- **`review_schedule`** — spaced repetition state per knowledge entry (interval step, next review date, times reviewed). MAAS-internal, not stored in Notion.
- **`supervisor_reviews`** — append-only per-agent rubric review rows written by the Supervisor agent. One row per (agent, review window). Idempotent on `(agent, envelope_id_to)` so process restart mid-tick cannot duplicate.

Access is gated through `src/project0/store.py`, which is a trust boundary — agent code never touches SQL directly.

Intelligence's data lives outside SQLite:

- **`data/intelligence/reports/YYYY-MM-DD.json`** — one file per generated daily report, validated against the `DailyReport` schema
- **`data/intelligence/feedback/YYYY-MM.jsonl`** — append-only thumbs feedback events, monthly rollover

## Running the tests

```bash
uv run pytest -q                 # 577 tests, all hermetic
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
│   ├── _tool_loop.py          # shared agentic loop used by Manager, Intelligence, and Learning
│   ├── registry.py            # agent + listener + pulse registries
│   ├── manager.py             # Manager (林夕): calendar tools + agentic loop + pulse
│   ├── secretary.py           # Secretary: cooldown gate + four entry paths
│   ├── intelligence.py        # Intelligence (顾瑾): five report tools + agentic loop + daily pulse
│   ├── learning.py            # Learning (温书瑶): Notion tools + review schedule + dual pulse
│   └── supervisor.py         # Supervisor (叶霏): three chat tools + pulse review + idle gate + review engine
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
├── control_panel/             # WebUI control panel (sub-project 3)
│   ├── __main__.py            # entry point: uv run python -m project0.control_panel
│   ├── app.py                 # FastAPI factory with lifespan (stops MAAS on exit)
│   ├── supervisor.py          # MAASSupervisor state machine (spawn/stop/restart)
│   ├── routes.py              # all HTTP routes (home, profile, facts, toml, personas, env, usage, reviews)
│   ├── paths.py               # allowlisted TOML/persona file resolution
│   ├── writes.py              # atomic_write_text helper
│   ├── rendering.py           # Jinja2 setup + SVG bar chart renderer
│   ├── templates/             # base.html + page templates (incl. reviews.html)
│   └── static/                # style.css (mobile-responsive)
├── notion/                    # Notion service (Learning agent)
│   ├── client.py              # async wrapper around notion-client SDK
│   └── model.py               # KnowledgeEntry + NotionClientError
└── calendar/
    ├── auth.py                # OAuth installed-app flow
    ├── client.py              # async wrapper around google-api-python-client
    ├── model.py               # CalendarEvent + translation
    └── errors.py              # GoogleCalendarError

prompts/
├── manager.md / .toml         # 林夕 persona + LLM / pulse config
├── secretary.md / .toml       # Secretary persona + cooldown thresholds
├── intelligence.md / .toml    # 顾瑾 persona + dual-LLM config + webapp bind + watchlist
├── learning.md / .toml        # 温书瑶 persona + Notion sync + review intervals + processing config
└── supervisor.md / .toml     # 叶霏 persona + review rubric config + dual pulse (cycle 10800s + retry 60s)

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
- `specs/2026-04-16-control-panel-design.md` — WebUI control panel: supervisor state machine, file editors, facts CRUD, token usage
- `specs/2026-04-16-learning-agent-design.md` — Learning agent: Notion-backed knowledge base curator + spaced repetition review coaching
- `specs/2026-04-17-supervisor-agent-design.md` — Supervisor agent (叶霏): pulse-scheduled reviewer with idle gate, rubric scoring, DM/group chat tool loop, `/reviews` page

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
- **WebUI control panel** — standalone FastAPI app supervising MAAS as a child process. Textarea editing for user profile, agent TOML configs, persona markdown, and `.env`. Full CRUD on `user_facts` (live via SQLite WAL). Token usage page with SVG bar chart and rollup tables. Mobile-responsive. Tailscale-gated, no auth.

- **Learning agent** — Notion-backed knowledge base curator (温书瑶). Processes links and text into structured knowledge entries, runs spaced repetition review coaching (1/3/7/14/30-day intervals), syncs with Notion every 30 seconds for bidirectional access.

- **Supervisor agent (叶霏)** — fifth agent, pulse-scheduled reviewer of Manager / Intelligence / Learning (never Secretary). Idle gate waits for 5 minutes of chat quiet before reviewing; exact-match idempotency on the per-agent cursor prevents overlap. Four-dimension rubric (helpfulness / correctness / tone / efficiency) with weighted overall score, short Chinese critique, and up to three recommendations per review. On-demand reviews via chat (`run_review_now`, `run_review_all`, `list_past_reviews`) bypass the idle gate; scheduled reviews run every 3 hours. Cross-cutting: Secretary-history isolation (`MessagesStore.recent_for_chat` requires a `visible_to` kwarg; non-Secretary callers never see Secretary envelopes). New `/reviews` page in the control panel: SVG time-series + per-agent cards + full critique history.

- **Secretary local-LLM option** — coupled persona + provider switch via `SECRETARY_MODE=work|free`. `free` mode points Secretary at an OpenAI-compatible vLLM / TensorRT-LLM server serving Qwen 2.5 72B abliterated on DGX Spark (5.5 tok/s single, ~43 tok/s batch 8), swaps in `prompts/secretary_free.{md,toml}`, and hard-disables the `remember_about_user` tool at the wiring level to prevent NSFW content from leaking into other agents' prompts via the shared `user_facts` table (invariant enforced by factory shape + belt-and-suspenders assert in `main.py`). Telegram `sendChatAction("typing")` refreshed every 4s fills the wait. Local usage rows still land in `llm_usage` (audit trail) but are filtered out of the `/usage` dashboard. The control panel's `/personas` and `/toml` pages now discover files by scanning `prompts/` — drop any `.md` or `.toml` in and it's editable immediately.

Further out (in rough master-spec order):

- **Tool gateway** — shared external-data infrastructure for Twitter, WeChat, web search
- **Cross-agent envelope trace viewer** — visual inspection of the audit log through the control panel
