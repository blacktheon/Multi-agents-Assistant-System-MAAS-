"""Entrypoint. Wires Store + Orchestrator + TelegramIO together and runs
the asyncio loop forever.

Usage:  uv run python -m project0.main
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from project0.agents.manager import Manager, load_manager_config, load_manager_persona
from project0.agents.registry import AGENT_SPECS, register_manager, register_secretary
from project0.agents.secretary import Secretary, load_config, load_persona
from project0.calendar.auth import load_or_acquire_credentials
from project0.calendar.client import GoogleCalendar
from project0.config import Settings, load_settings
from project0.llm.provider import AnthropicProvider, FakeProvider, LLMProvider
from project0.orchestrator import Orchestrator
from project0.pulse import load_pulse_entries, run_pulse_loop
from project0.store import Store
from project0.telegram_io import (
    FakeBotSender,
    build_bot_applications,
    fetch_bot_usernames,
)

log = logging.getLogger("project0")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


def _ensure_store_dir(store_path: str) -> None:
    p = Path(store_path)
    p.parent.mkdir(parents=True, exist_ok=True)


def _build_llm_provider(settings: Settings) -> LLMProvider:
    """Construct the LLM provider based on LLM_PROVIDER env var.

    Instantiation does not hit the network; a bad Anthropic key surfaces
    as an LLMProviderError on the first Secretary call rather than at
    startup. load_settings already rejected obviously-malformed keys
    (empty or not starting with 'sk-').
    """
    provider_name = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower() or "anthropic"
    model = os.environ.get("LLM_MODEL", "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"

    if provider_name == "anthropic":
        return AnthropicProvider(api_key=settings.anthropic_api_key, model=model)
    if provider_name == "fake":
        log.warning("LLM_PROVIDER=fake — using FakeProvider. Not for production.")
        return FakeProvider(responses=["(fake provider response)"] * 10_000)
    raise RuntimeError(f"unknown LLM_PROVIDER={provider_name!r}")


async def _run(settings: Settings) -> None:
    _ensure_store_dir(settings.store_path)
    store = Store(settings.store_path)
    store.init_schema()

    # Build the LLM provider once; share it across agents.
    llm = _build_llm_provider(settings)

    # Construct Secretary and install it into both registries. This MUST
    # happen before the orchestrator handles any message — AGENT_SPECS
    # already lists secretary, so load_settings will have demanded its
    # bot token above.
    persona = load_persona(Path("prompts/secretary.md"))
    secretary_cfg = load_config(Path("prompts/secretary.toml"))
    secretary = Secretary(
        llm=llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=secretary_cfg,
    )
    register_secretary(secretary.handle)
    log.info("secretary registered (model=%s)", secretary_cfg.model)

    # Google Calendar client (shared; used by Manager and any future
    # calendar-using agent). Loads credentials via the installed-app flow
    # on first run, cached in settings.google_token_path thereafter.
    calendar_creds = load_or_acquire_credentials(
        token_path=settings.google_token_path,
        client_secrets_path=settings.google_client_secrets_path,
    )
    calendar = GoogleCalendar(
        credentials=calendar_creds,
        calendar_id=settings.google_calendar_id,
        user_tz=settings.user_tz,
    )
    log.info(
        "google calendar ready (calendar_id=%s tz=%s)",
        settings.google_calendar_id, settings.user_tz.key,
    )

    # Manager (replaces the legacy stub that used to live in
    # AGENT_REGISTRY at import time).
    manager_persona = load_manager_persona(Path("prompts/manager.md"))
    manager_cfg = load_manager_config(Path("prompts/manager.toml"))
    manager = Manager(
        llm=llm,
        calendar=calendar,
        memory=store.agent_memory("manager"),
        messages_store=store.messages(),
        persona=manager_persona,
        config=manager_cfg,
        user_tz=settings.user_tz,
    )
    register_manager(manager.handle)
    log.info("manager registered (model=%s)", manager_cfg.model)

    # Pulse scheduler entries for Manager.
    pulse_entries = load_pulse_entries(Path("prompts/manager.toml"))
    log.info(
        "manager pulse entries: %s",
        [(e.name, e.every_seconds) for e in pulse_entries],
    )

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

    # Phase 1: initialize and start every bot BEFORE any polling begins. This
    # avoids network contention on startup and also populates app.bot.username
    # so we can build the mention-routing mapping below.
    for name, app in apps.items():
        await app.initialize()
        await app.start()
        log.info("bot %s initialized", name)

    # Now that each bot's real Telegram @username is known, wire it into the
    # orchestrator so that @MAAS_manager_bot (or whatever BotFather chose)
    # routes correctly. Falls back to the short-form @manager / @intelligence
    # check inside parse_mentions, which is what agent handoff messages use.
    orch.username_to_agent = fetch_bot_usernames(apps)
    log.info("mention routing: %s", orch.username_to_agent)

    # Phase 2: start all pollers concurrently.
    #
    # drop_pending_updates=True tells Telegram to acknowledge any queued
    # updates from prior bot runs without processing them. Without this,
    # every restart replays stale updates from previous runs (including
    # ones that previous crashed processes never confirmed), which
    # silently double-feeds the orchestrator.
    async with asyncio.TaskGroup() as tg:
        for name, app in apps.items():
            assert app.updater is not None
            tg.create_task(
                app.updater.start_polling(drop_pending_updates=True)
            )
            log.info("bot %s polling", name)

        for entry in pulse_entries:
            tg.create_task(
                run_pulse_loop(
                    entry=entry,
                    target_agent="manager",
                    orchestrator=orch,
                )
            )
            log.info("pulse task spawned: %s", entry.name)

        # Run forever until cancelled.
        stop_event = asyncio.Event()
        await stop_event.wait()


def main() -> None:
    settings = load_settings()
    _setup_logging(settings.log_level)
    try:
        asyncio.run(_run(settings))
    except KeyboardInterrupt:
        log.info("shutting down")


if __name__ == "__main__":
    main()
