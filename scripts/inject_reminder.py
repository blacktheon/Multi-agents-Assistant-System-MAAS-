"""Manual smoke-test helper for Secretary's Manager-directed reminder path.

Usage:
    uv run python scripts/inject_reminder.py "<appointment>" "<when>" [<note>]

Synthesizes a manager_delegation envelope with
payload={"kind": "reminder_request", ...}, dispatches it through Secretary,
and prints the reply. Does NOT go through Telegram — Secretary's reply is
printed to stdout. This is intentionally minimal: the real Manager in 6b
will construct and dispatch these envelopes via the live orchestrator.

Requires all of the same env vars main.py needs (bot tokens, allow-list,
ANTHROPIC_API_KEY) because Settings validation is shared.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from project0.agents.secretary import Secretary, load_config, load_persona
from project0.config import load_settings
from project0.envelope import Envelope
from project0.llm.provider import AnthropicProvider
from project0.store import Store


async def main() -> None:
    if len(sys.argv) < 3:
        print(
            "usage: uv run python scripts/inject_reminder.py "
            '"<appointment>" "<when>" [<note>]',
            file=sys.stderr,
        )
        sys.exit(2)

    appointment = sys.argv[1]
    when = sys.argv[2]
    note = sys.argv[3] if len(sys.argv) > 3 else ""

    settings = load_settings()
    store = Store(settings.store_path)
    store.init_schema()

    provider = AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model="claude-sonnet-4-6",
    )
    persona = load_persona(Path("prompts/secretary.md"))
    cfg = load_config(Path("prompts/secretary.toml"))
    sec = Secretary(
        llm=provider,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=cfg,
    )

    env = Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="internal",
        telegram_chat_id=None,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="secretary",
        body="",
        routing_reason="manager_delegation",
        payload={
            "kind": "reminder_request",
            "appointment": appointment,
            "when": when,
            "note": note,
        },
    )
    result = await sec.handle(env)
    if result is None or result.reply_text is None:
        print("(secretary returned no reply)")
    else:
        print(result.reply_text)


if __name__ == "__main__":
    asyncio.run(main())
