# Project 0 — Multi-Agent Assistant

Single-process Python implementation of Project 0. Design specs live under
`docs/superpowers/specs/`:
- `2026-04-13-multi-agent-skeleton-design.md` — foundational skeleton
- `2026-04-13-secretary-design.md` — sub-project 6a (Secretary, first real LLM agent)

## What runs today

**Skeleton:** Manager + Intelligence routing, memory isolation, @mention
focus tracking, Manager-only delegation, SQLite envelope log.

**Sub-project 6a (Secretary):** a real LLM-backed conversational companion.
Speaks Chinese, passively observes group chats with a rich cooldown gate,
replies to @mentions / DMs, and delivers Manager-directed reminders.
Calls Claude via a thin provider interface with prompt caching.

## One-time setup

### 1. Create the bots

In Telegram, talk to `@BotFather`:

1. `/newbot` → name it "Project0 Manager", username ending in `_bot`.
2. `/newbot` → name it "Project0 Intelligence", username ending in `_bot`.
3. `/newbot` → name it "Project0 Secretary", username ending in `_bot`.
4. For **each** bot, run `/setprivacy` → pick the bot → **Disable**. This is
   required so bots in groups see every message, not only @mentions.
5. Save all three tokens.

### 2. Create a Telegram group

1. Create a new Telegram group.
2. Add all three bots as members.
3. Send any message. Look at the raw update (e.g., by enabling Telegram
   Desktop's "Copy Link" feature on a message, or by temporarily running
   the bot with `LOG_LEVEL=DEBUG`) to find the group's `chat_id`. For
   supergroups, the `chat_id` is negative and starts with `-100...`.
4. Find your own Telegram user id. Talk to `@userinfobot` — it will reply
   with your numeric id.

### 3. Fill in `.env`

```bash
cp .env.example .env
```

Edit `.env`:

```
TELEGRAM_BOT_TOKEN_MANAGER=<manager bot token>
TELEGRAM_BOT_TOKEN_INTELLIGENCE=<intelligence bot token>
TELEGRAM_BOT_TOKEN_SECRETARY=<secretary bot token>
TELEGRAM_ALLOWED_CHAT_IDS=<your group chat_id, e.g. -100123456789>
TELEGRAM_ALLOWED_USER_IDS=<your telegram user id>
ANTHROPIC_API_KEY=sk-ant-...    # Secretary makes real calls via this
LLM_PROVIDER=anthropic          # default; "fake" for FakeProvider
LLM_MODEL=claude-sonnet-4-6     # override to change models
STORE_PATH=data/store.db
LOG_LEVEL=INFO
```

### 4. Install dependencies

```bash
uv sync
```

## Running

```bash
uv run python -m project0.main
```

Both bots start polling. Leave the process running in one terminal.

## Manual smoke test (acceptance criterion D)

In the Telegram group that contains your user + both bots, perform these
checks in order. The `messages` table can be inspected any time with
`sqlite3 data/store.db`.

- **D.1.** Send `hello`. Expect: one reply from the Manager bot:
  `[manager-stub] acknowledged: hello`
- **D.2.** Send `any news today?`. Expect two messages, in order:
  1. From the Manager bot: `→ forwarding to @intelligence`
  2. From the Intelligence bot: `[intelligence-stub] acknowledged: any news today?`
- **D.3.** Send `what else?` (no @mention). Expect: one reply from the
  **Intelligence** bot, proving sticky focus carried over from D.2.
- **D.4.** Send `@manager what's up`. Expect: one reply from the Manager
  bot. Then send `and now?` — it should also route to Manager, proving
  the @mention switched the focus.
- **D.5.** Open a direct chat (DM) with the Intelligence bot. Send
  `hi there`. Expect: `[intelligence-stub] acknowledged: hi there` from
  Intelligence in the DM. Group focus should be unchanged.

### Inspecting the message tree

After D.2, the envelope tree should be visible:

```bash
sqlite3 data/store.db "SELECT id, parent_id, source, from_kind, from_agent, to_agent, substr(envelope_json, 1, 60) FROM messages ORDER BY id;"
```

You should see four rows for the D.2 flow:

```
id | parent_id | source          | from_kind | from_agent    | to_agent
---+-----------+-----------------+-----------+---------------+--------------
 N | NULL      | telegram_group  | user      | NULL          | manager
N+1| N         | internal        | agent     | manager       | user         (the visible handoff)
N+2| N         | internal        | agent     | manager       | intelligence (the internal forward)
N+3| N+2       | internal        | agent     | intelligence  | user         (the reply)
```

## Automated checks

```bash
uv run pytest -v
uv run mypy src/project0
uv run ruff check src tests
```

All three must be green for acceptance.

## Sub-project 6a — Secretary smoke test (acceptance criterion G)

Requires a real `ANTHROPIC_API_KEY` and all three bots in the allow-listed
group. Secretary will speak Chinese in character.

- **G.1.** Send a few short messages (`hi`, `ok`, `sure`). Secretary stays
  silent — the cooldown has not opened yet (needs ≥90s elapsed, ≥4 msgs,
  ≥200 weighted chars since the last Secretary reply).
- **G.2.** Once thresholds are crossed, Secretary either chimes in (one
  Chinese line, in character) or stays silent (LLM returned `[skip]`).
  Both outcomes are normal — repeat over a few minutes to see both.
- **G.3.** Send `@secretary 你好`. Expect an immediate Chinese reply.
- **G.4.** DM Secretary's bot directly with `你今天怎么样`. Expect a reply
  with a more personal tone.
- **G.5.** Run `uv run python scripts/inject_reminder.py "项目评审" "明天下午3点"`.
  Expect a warm Chinese reminder printed to stdout. (Bypasses Telegram.)
- **G.6.** Inspect the audit tree:
  ```bash
  sqlite3 data/store.db "SELECT id, parent_id, from_agent, to_agent, \
    json_extract(envelope_json, '\$.routing_reason') AS rr \
    FROM messages ORDER BY id DESC LIMIT 20;"
  ```
  For each group message there should be a `listener_observation` envelope
  whose `parent_id` points at the original user envelope; any Secretary
  reply links to the listener_observation, not the original.

### Upgrading from skeleton to 6a

The new `payload_json` column on `messages` is added via an idempotent
`ALTER TABLE ADD COLUMN` on startup — no manual migration needed. Your
existing `data/store.db` will be upgraded in place.

## Reset

If you need to start over:

```bash
rm data/store.db
```

The schema is recreated on next startup. There is no migration system
in the skeleton.

## Google Cloud setup (sub-project 6b)

The Google Calendar integration needs a personal OAuth client. This is
a one-time setup done through the Google Cloud Console and takes about
five minutes.

1. Go to https://console.cloud.google.com, create a new project called
   `project-0` (or reuse an existing personal project).
2. In the project, open the API Library and enable the **Google Calendar
   API**.
3. Open the OAuth consent screen page:
   - User type: **External**
   - App name: `Project 0`
   - User support email: your own email
   - Scopes: add `https://www.googleapis.com/auth/calendar.events`
   - Test users: add your own Google account
   The app stays in **Testing** mode permanently. This is correct for a
   personal tool — no Google verification review is required and your
   token stays valid.
4. Open the Credentials page and create OAuth 2.0 credentials:
   - Type: **Desktop app**
   - Name: `Project 0 local`
   Download the resulting JSON file.
5. Save the downloaded file as `data/google_client_secrets.json` in this
   repo. `data/` is already gitignored so this file cannot land in the
   repo by accident.

You only do these steps once per personal Google account. After that,
the OAuth token file at `data/google_token.json` is created automatically
the first time you run `scripts/calendar_smoke.py`.

## 6b smoke test

After completing Google Cloud setup above, run:

    uv run python scripts/calendar_smoke.py

On first run a browser window opens asking you to authorize Project 0
to access your calendar. Approve, then return to the terminal. The
script walks through seven steps:

1. Authorize (browser flow on first run; silent load on later runs).
2. Read upcoming 7 days — prints a table of your real events.
3. Create a test event 1 hour from now. Pauses for you to confirm
   visually in Google Calendar on any device, then press Enter.
4. Update the test event's title. Pauses again.
5. Round-trip the edit through `get_event` and assert it matches.
6. Delete the test event. Pauses for visual confirmation.
7. Verify that a bogus event ID raises `GoogleCalendarError`.

If the script crashes between steps 3 and 6, an `atexit` handler makes
a best-effort attempt to delete the test event. If the network is down
at exit time, delete it manually in Google Calendar.

To refresh the golden test fixtures after an SDK upgrade or a Google
API change, delete `data/google_token.json` and run:

    uv run python scripts/calendar_smoke.py --dump-fixtures tests/fixtures/google_calendar/

Then commit the updated fixtures.

To revoke Project 0's access entirely, visit
https://myaccount.google.com/permissions, find "Project 0" in the list,
and remove its access. Delete `data/google_token.json` locally afterwards.
