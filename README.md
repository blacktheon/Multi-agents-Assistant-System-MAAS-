# Project 0 — Multi-Agent Skeleton

Single-process Python skeleton for Project 0. See the design spec at
`docs/superpowers/specs/2026-04-13-multi-agent-skeleton-design.md` for the full
architecture and scope rationale.

## What this skeleton does

- Runs two Telegram bots (Manager + Intelligence) in one Python process.
- Routes group-chat messages through an orchestrator that enforces
  memory isolation, @mention focus tracking, and Manager-only delegation.
- Persists every message envelope into a SQLite database.
- **Does not** call any LLM. Agents are hardcoded stubs that echo back.

## One-time setup

### 1. Create the bots

In Telegram, talk to `@BotFather`:

1. `/newbot` → name it something like "Project0 Manager", username ending in `_bot`.
2. `/newbot` → name it "Project0 Intelligence", username ending in `_bot`.
3. For **each** bot, run `/setprivacy` → pick the bot → **Disable**. This is
   required so bots in groups see every message, not only @mentions.
4. Save both tokens.

### 2. Create a Telegram group

1. Create a new Telegram group.
2. Add both bots as members.
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
TELEGRAM_ALLOWED_CHAT_IDS=<your group chat_id, e.g. -100123456789>
TELEGRAM_ALLOWED_USER_IDS=<your telegram user id>
ANTHROPIC_API_KEY=sk-ant-...   # validated at startup, never called
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

## Scope reminder

The skeleton is **deliberately unintelligent**. Agents do not understand
your messages — the Manager's routing decision is a single substring
check for `"news"`. If you find yourself thinking "the skeleton is almost
smart," something is wrong; see section 1 of the design spec for why.

Real LLM-backed agents start in sub-project 6a (Secretary). Do not add
LLM calls or natural-language routing to this skeleton — that belongs
in the next sub-project's spec.

## Reset

If you need to start over:

```bash
rm data/store.db
```

The schema is recreated on next startup. There is no migration system
in the skeleton.
