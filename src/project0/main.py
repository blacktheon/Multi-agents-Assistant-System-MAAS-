"""Entrypoint. Wires Store + Orchestrator + TelegramIO together and runs
the asyncio loop forever.

Usage:  uv run python -m project0.main
"""

from __future__ import annotations

import asyncio
import logging
import os
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from project0.agents.intelligence import Intelligence

from project0.agents.manager import Manager, load_manager_config, load_manager_persona
from project0.agents.registry import AGENT_SPECS, register_manager, register_secretary
from project0.agents.secretary import Secretary, load_config, load_persona
from project0.calendar.auth import load_or_acquire_credentials
from project0.calendar.client import GoogleCalendar
from project0.config import Settings, load_settings
from project0.intelligence_web.config import WebConfig
from project0.llm.local_provider import LocalProvider
from project0.llm.provider import AnthropicProvider, FakeProvider, LLMProvider
from project0.orchestrator import Orchestrator
from project0.pulse import load_pulse_entries, run_pulse_loop
from project0.store import (
    LLMUsageStore,
    Store,
    UserFactsReader,
    UserFactsWriter,
    UserProfile,
)
from project0.telegram_io import (
    FakeBotSender,
    build_bot_applications,
    fetch_bot_usernames,
)

log = logging.getLogger("project0")


def _install_sigterm_handler() -> None:
    """Translate SIGTERM to KeyboardInterrupt so ``asyncio.run`` exits via
    the existing clean-shutdown path. Used when MAAS runs as a child of the
    control panel, which sends SIGTERM on Stop."""
    import signal

    def _handler(signum: int, frame: object) -> None:  # noqa: ARG001
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, _handler)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    # Silence chatty third-party loggers. These emit one INFO line per HTTP
    # request, which floods the console with Telegram long-polling pings
    # and per-tweet-fetch lines during report generation. Project code at
    # `project0.*` still respects the user's configured level.
    for noisy in (
        "httpx",
        "httpcore",
        "urllib3",
        "telegram",
        "telegram.ext",
        "apscheduler",
        "uvicorn.access",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _ensure_store_dir(store_path: str) -> None:
    p = Path(store_path)
    p.parent.mkdir(parents=True, exist_ok=True)


async def _run_intelligence_daily_pulse(
    *,
    intelligence: Intelligence,
    user_tz: ZoneInfo,
    pulse_hour: int,
    stop_event: asyncio.Event,
) -> None:
    """Daily pulse: at ``pulse_hour`` each day, generate today's daily
    report if it doesn't already exist. Runs as an asyncio task inside
    ``_run``'s TaskGroup; stops when ``stop_event`` is set. Errors in a
    single generation (e.g. twitterapi.io outage) are logged and the loop
    continues — we retry on the next pulse."""
    while not stop_event.is_set():
        now = datetime.now(tz=user_tz)
        today = now.date()
        target = datetime(
            today.year, today.month, today.day, pulse_hour, tzinfo=user_tz
        )
        if now >= target:
            # Past today's pulse window — check and generate now if needed.
            try:
                generated = await intelligence.ensure_today_report()
                if generated:
                    log.info("daily pulse: generated report for %s", today)
                else:
                    log.info(
                        "daily pulse: report for %s already exists, skipping",
                        today,
                    )
            except Exception:
                log.exception("daily pulse: generation failed for %s", today)
            # Schedule next pulse for tomorrow at pulse_hour.
            target = target + timedelta(days=1)

        wait_seconds = max((target - now).total_seconds(), 1.0)
        log.info(
            "daily pulse: next check at %s (in %ds)",
            target.strftime("%Y-%m-%d %H:%M %Z"),
            int(wait_seconds),
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
            return  # stop_event was set — exit loop
        except TimeoutError:
            continue  # time to check again


async def _run_web(
    *,
    web_config: WebConfig,
    stop_event: asyncio.Event,
) -> None:
    """Run the Intelligence webapp (6e) as an asyncio task alongside the bot
    pollers. Shares ``stop_event`` with the rest of ``_run`` so ``Ctrl-C``
    stops everything together."""
    import uvicorn

    from project0.intelligence_web.app import create_app

    app = create_app(web_config)
    config = uvicorn.Config(
        app,
        host=web_config.bind_host,
        port=web_config.bind_port,
        log_level="info",
        access_log=True,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    # main.py owns signals; prevent uvicorn from installing its own handlers.
    # The attribute exists on uvicorn.Server at runtime but isn't in the stubs.
    server.install_signal_handlers = lambda: None  # type: ignore[attr-defined]

    server_task = asyncio.create_task(server.serve())
    try:
        await stop_event.wait()
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=5.0)
        except TimeoutError:
            server.force_exit = True
            await server_task


def _build_llm_provider(settings: Settings, usage_store: LLMUsageStore) -> LLMProvider:
    """Construct the LLM provider based on LLM_PROVIDER env var.

    Instantiation does not hit the network; a bad Anthropic key surfaces
    as an LLMProviderError on the first Secretary call rather than at
    startup. load_settings already rejected obviously-malformed keys
    (empty or not starting with 'sk-').
    """
    provider_name = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower() or "anthropic"
    model = os.environ.get("LLM_MODEL", "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"

    if provider_name == "anthropic":
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=model,
            usage_store=usage_store,
            cache_ttl=settings.anthropic_cache_ttl,
        )
    if provider_name == "fake":
        log.warning("LLM_PROVIDER=fake — using FakeProvider. Not for production.")
        return FakeProvider(responses=["(fake provider response)"] * 10_000)
    raise RuntimeError(f"unknown LLM_PROVIDER={provider_name!r}")


def _build_secretary_dependencies(
    *,
    settings: Settings,
    usage_store: LLMUsageStore,
    anthropic_provider: LLMProvider,
    base_facts_writer: UserFactsWriter,
) -> tuple[LLMProvider, Path, Path, UserFactsWriter | None]:
    """Return (provider, persona_path, config_path, facts_writer_or_none).

    `work` mode returns the shared AnthropicProvider, normal prompt files,
    and the wired writer. `free` mode returns a fresh LocalProvider, the
    free-mode prompt files, and None for the writer — this is the NSFW
    isolation invariant from 2026-04-20-secretary-local-llm-design.md §6.
    """
    if settings.secretary_mode == "work":
        return (
            anthropic_provider,
            Path("prompts/secretary.md"),
            Path("prompts/secretary.toml"),
            base_facts_writer,
        )
    if settings.secretary_mode == "free":
        local = LocalProvider(
            base_url=settings.local_llm_base_url,
            model=settings.local_llm_model,
            api_key=settings.local_llm_api_key,
            usage_store=usage_store,
        )
        writer: UserFactsWriter | None = None
        assert writer is None, (
            "free-mode Secretary must not wire user_facts_writer; "
            "see 2026-04-20-secretary-local-llm-design.md §6"
        )
        log.info(
            "secretary factory: mode=free model=%s base_url=%s (writer disabled)",
            settings.local_llm_model,
            settings.local_llm_base_url,
        )
        return (
            local,
            Path("prompts/secretary_free.md"),
            Path("prompts/secretary_free.toml"),
            writer,
        )
    raise RuntimeError(f"unknown secretary_mode={settings.secretary_mode!r}")


async def _run(settings: Settings) -> None:
    _ensure_store_dir(settings.store_path)
    store = Store(settings.store_path)
    store.init_schema()

    # Every process restart begins with Manager as the default route for
    # every group chat, regardless of where the previous process left focus.
    # Delegations (e.g. "please remind me of X" → Secretary) no longer
    # switch focus at all, but this clears any legacy focus rows left over
    # from pre-fix runs.
    store.chat_focus().clear_all()
    log.info("chat_focus cleared on startup")

    # Build the LLM provider once; share it across agents. Task 9 will wire
    # the usage store properly into a dedicated surface; for now it lives on
    # the same sqlite connection as Store.
    usage_store = LLMUsageStore(store.conn)
    llm = _build_llm_provider(settings, usage_store)

    # Task 9: shared user profile + per-agent fact readers + single writer.
    # UserProfile.load tolerates a missing file (logs a warning and returns
    # an empty profile), so this is safe on fresh checkouts.
    user_profile = UserProfile.load(Path("data/user_profile.yaml"))
    secretary_facts_reader = UserFactsReader("secretary", store.conn)
    manager_facts_reader = UserFactsReader("manager", store.conn)
    intelligence_facts_reader = UserFactsReader("intelligence", store.conn)
    secretary_facts_writer = UserFactsWriter("secretary", store.conn)

    # Construct Secretary and install it into both registries. This MUST
    # happen before the orchestrator handles any message — AGENT_SPECS
    # already lists secretary, so load_settings will have demanded its
    # bot token above.
    (
        secretary_llm,
        secretary_persona_path,
        secretary_config_path,
        secretary_writer,
    ) = _build_secretary_dependencies(
        settings=settings,
        usage_store=usage_store,
        anthropic_provider=llm,
        base_facts_writer=secretary_facts_writer,
    )
    # Belt-and-suspenders invariant check (factory also enforces).
    if isinstance(secretary_llm, LocalProvider):
        assert secretary_writer is None, (
            "SECRETARY_MODE=free must NOT wire user_facts_writer — "
            "see 2026-04-20-secretary-local-llm-design.md §6"
        )

    persona = load_persona(secretary_persona_path)
    secretary_cfg = load_config(secretary_config_path)
    secretary = Secretary(
        llm=secretary_llm,
        memory=store.agent_memory("secretary"),
        messages_store=store.messages(),
        persona=persona,
        config=secretary_cfg,
        user_profile=user_profile,
        user_facts_reader=secretary_facts_reader,
        user_facts_writer=secretary_writer,
    )
    register_secretary(secretary.handle)
    log.info(
        "secretary registered (mode=%s model=%s)",
        settings.secretary_mode, secretary_cfg.model,
    )

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
        settings.google_calendar_id,
        settings.user_tz.key,
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
        user_profile=user_profile,
        user_facts_reader=manager_facts_reader,
        user_facts_writer=None,
    )
    register_manager(manager.handle)
    log.info("manager registered (model=%s)", manager_cfg.model)

    # --- 6d: Intelligence agent -------------------------------------------
    from project0.agents.intelligence import (
        Intelligence,
        load_intelligence_config,
        load_intelligence_persona,
    )
    from project0.agents.registry import register_intelligence
    from project0.intelligence.twitterapi_io import TwitterApiIoSource
    from project0.intelligence.watchlist import load_watchlist

    intelligence_persona = load_intelligence_persona(Path("prompts/intelligence.md"))
    intelligence_cfg = load_intelligence_config(Path("prompts/intelligence.toml"))
    intelligence_watchlist = load_watchlist(Path("prompts/intelligence.toml"))

    # 6e: webapp config loaded from the same file, shared between the agent
    # (for building link URLs in get_report_link) and the webapp (for
    # binding and filesystem paths).
    _intel_toml_data = tomllib.loads(Path("prompts/intelligence.toml").read_text(encoding="utf-8"))
    if "web" not in _intel_toml_data:
        raise RuntimeError("prompts/intelligence.toml missing [web] section — required for 6e")
    web_config = WebConfig.from_toml_section(_intel_toml_data["web"])

    twitterapi_key = os.environ.get("TWITTERAPI_IO_API_KEY", "").strip()
    if not twitterapi_key:
        raise RuntimeError(
            "TWITTERAPI_IO_API_KEY not set in environment — required by Intelligence agent (6d)"
        )
    twitter_source = TwitterApiIoSource(api_key=twitterapi_key)

    reports_dir = Path("data/intelligence/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Intelligence uses two LLM providers: Opus for the one-shot summarizer,
    # Sonnet for the agentic Q&A tool-use loop. Both talk to Anthropic
    # directly and do not go through _build_llm_provider (which is driven
    # by env vars and returns a single provider).
    intelligence_llm_summarizer = AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model=intelligence_cfg.summarizer_model,
        usage_store=usage_store,
        cache_ttl=settings.anthropic_cache_ttl,
    )
    intelligence_llm_qa = AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model=intelligence_cfg.qa_model,
        usage_store=usage_store,
        cache_ttl=settings.anthropic_cache_ttl,
    )

    intelligence = Intelligence(
        llm_summarizer=intelligence_llm_summarizer,
        llm_qa=intelligence_llm_qa,
        twitter=twitter_source,
        messages_store=store.messages(),
        persona=intelligence_persona,
        config=intelligence_cfg,
        watchlist=intelligence_watchlist,
        reports_dir=reports_dir,
        user_tz=settings.user_tz,
        public_base_url=web_config.public_base_url,
        user_profile=user_profile,
        user_facts_reader=intelligence_facts_reader,
        user_facts_writer=None,
    )
    register_intelligence(intelligence.handle)
    log.info(
        "intelligence registered (summarizer=%s, qa=%s, watchlist=%d)",
        intelligence_cfg.summarizer_model,
        intelligence_cfg.qa_model,
        len(intelligence_watchlist),
    )

    # --- Learning agent -------------------------------------------------------
    from project0.agents.learning import (
        LearningAgent,
        load_learning_config,
        load_learning_persona,
    )
    from project0.agents.registry import register_learning
    from project0.notion.client import NotionClient

    learning_persona = load_learning_persona(Path("prompts/learning.md"))
    learning_cfg = load_learning_config(Path("prompts/learning.toml"))

    notion_client = NotionClient(
        token=settings.notion_token,
        database_id=settings.notion_database_id,
    )

    learning_facts_reader = UserFactsReader("learning", store.conn)

    learning = LearningAgent(
        llm=llm,
        notion=notion_client,
        knowledge_index=store.knowledge_index(),
        review_schedule=store.review_schedule(),
        messages_store=store.messages(),
        persona=learning_persona,
        config=learning_cfg,
        user_tz=settings.user_tz,
        user_profile=user_profile,
        user_facts_reader=learning_facts_reader,
    )
    register_learning(learning.handle)
    log.info("learning registered (model=%s)", learning_cfg.model)

    # --- Supervisor agent (叶霏) ---------------------------------------------
    from project0.agents.supervisor import (
        Supervisor,
        load_supervisor_config,
        load_supervisor_persona,
    )
    from project0.agents.registry import register_supervisor

    supervisor_persona = load_supervisor_persona(Path("prompts/supervisor.md"))
    supervisor_cfg = load_supervisor_config(Path("prompts/supervisor.toml"))

    supervisor = Supervisor(
        llm=llm,
        store=store,
        persona=supervisor_persona,
        config=supervisor_cfg,
    )
    register_supervisor(supervisor.handle)
    log.info("supervisor registered (model=%s)", supervisor_cfg.model)

    supervisor_pulse_entries = load_pulse_entries(Path("prompts/supervisor.toml"))
    log.info(
        "supervisor pulse entries: %s",
        [(e.name, e.every_seconds) for e in supervisor_pulse_entries],
    )

    # Pulse scheduler entries for Manager.
    pulse_entries = load_pulse_entries(Path("prompts/manager.toml"))
    log.info(
        "manager pulse entries: %s",
        [(e.name, e.every_seconds) for e in pulse_entries],
    )

    # Pulse scheduler entries for Learning.
    learning_pulse_entries = load_pulse_entries(Path("prompts/learning.toml"))
    log.info(
        "learning pulse entries: %s",
        [(e.name, e.every_seconds) for e in learning_pulse_entries],
    )

    # Sanity-check that every registered agent has a token.
    for agent_name in AGENT_SPECS:
        if agent_name not in settings.bot_tokens:
            raise RuntimeError(f"agent {agent_name!r} is registered but has no bot token in .env")

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
    supervisor.set_sender(real_sender)
    learning.set_sender(real_sender)
    intelligence.set_sender(real_sender)

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
    stop_event = asyncio.Event()
    async with asyncio.TaskGroup() as tg:
        for name, app in apps.items():
            assert app.updater is not None
            tg.create_task(app.updater.start_polling(drop_pending_updates=True))
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

        for entry in learning_pulse_entries:
            tg.create_task(
                run_pulse_loop(
                    entry=entry,
                    target_agent="learning",
                    orchestrator=orch,
                )
            )
            log.info("pulse task spawned: %s", entry.name)

        for entry in supervisor_pulse_entries:
            tg.create_task(
                run_pulse_loop(
                    entry=entry,
                    target_agent="supervisor",
                    orchestrator=orch,
                )
            )
            log.info("pulse task spawned: %s", entry.name)

        tg.create_task(_run_web(web_config=web_config, stop_event=stop_event))
        log.info(
            "intelligence webapp task spawned: bound to %s:%d",
            web_config.bind_host,
            web_config.bind_port,
        )

        if intelligence_cfg.daily_pulse_hour is not None:
            tg.create_task(
                _run_intelligence_daily_pulse(
                    intelligence=intelligence,
                    user_tz=settings.user_tz,
                    pulse_hour=intelligence_cfg.daily_pulse_hour,
                    stop_event=stop_event,
                )
            )
            log.info(
                "intelligence daily pulse spawned: %02d:00 %s",
                intelligence_cfg.daily_pulse_hour,
                settings.user_tz.key,
            )

        # Run forever until cancelled.
        await stop_event.wait()


def main() -> None:
    settings = load_settings()
    _setup_logging(settings.log_level)
    _install_sigterm_handler()
    try:
        asyncio.run(_run(settings))
    except KeyboardInterrupt:
        log.info("shutting down")


if __name__ == "__main__":
    main()
