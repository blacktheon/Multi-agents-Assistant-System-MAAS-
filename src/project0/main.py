"""Entrypoint. Wires Store + Orchestrator + TelegramIO together and runs
the asyncio loop forever.

Usage:  uv run python -m project0.main
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from anthropic import AsyncAnthropic  # imported to fail fast on bad key

from project0.agents.registry import AGENT_SPECS
from project0.config import Settings, load_settings
from project0.orchestrator import Orchestrator
from project0.store import Store
from project0.telegram_io import FakeBotSender, build_bot_applications

log = logging.getLogger("project0")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


def _ensure_store_dir(store_path: str) -> None:
    p = Path(store_path)
    p.parent.mkdir(parents=True, exist_ok=True)


def _validate_anthropic_key(settings: Settings) -> None:
    """Instantiate the Anthropic client to validate the key shape, but do
    not make any API call. If the skeleton ever starts calling Claude,
    this is the seam where a smoke-test call would go."""
    _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    del _client


async def _run(settings: Settings) -> None:
    _ensure_store_dir(settings.store_path)
    store = Store(settings.store_path)
    store.init_schema()

    # Sanity-check that every registered agent has a token.
    for agent_name in AGENT_SPECS:
        if agent_name not in settings.bot_tokens:
            raise RuntimeError(
                f"agent {agent_name!r} is registered but has no bot token in .env"
            )

    # Build orchestrator first; sender is attached after bot apps exist.
    # We construct a placeholder and swap in RealBotSender once built.
    placeholder_sender = FakeBotSender()  # swapped below
    orch = Orchestrator(
        store=store,
        sender=placeholder_sender,
        allowed_chat_ids=settings.allowed_chat_ids,
        allowed_user_ids=settings.allowed_user_ids,
    )

    apps, real_sender = await build_bot_applications(
        bot_tokens=settings.bot_tokens,
        handler=orch.handle,
    )
    orch.sender = real_sender

    log.info("starting bot pollers for: %s", ", ".join(apps))

    async with asyncio.TaskGroup() as tg:
        for name, app in apps.items():
            # python-telegram-bot's Application.run_polling blocks and manages
            # its own event loop. We want it as a coroutine task in our loop,
            # so use initialize/start/updater.start_polling/idle pattern.
            await app.initialize()
            await app.start()
            assert app.updater is not None
            tg.create_task(app.updater.start_polling())
            log.info("bot %s started", name)

        # Run forever until cancelled.
        stop_event = asyncio.Event()
        await stop_event.wait()


def main() -> None:
    settings = load_settings()
    _setup_logging(settings.log_level)
    _validate_anthropic_key(settings)
    try:
        asyncio.run(_run(settings))
    except KeyboardInterrupt:
        log.info("shutting down")


if __name__ == "__main__":
    main()
