"""Intelligence agent — LLM-backed briefing specialist.

6d scope: Twitter/X ingestion, one-Opus-call daily report generation,
shallow Q&A over the latest report via a Sonnet tool-use loop.

Persona has five canonical Chinese sections (mirrors Manager). The
Intelligence class takes TWO LLM providers: one Opus (summarizer) and
one Sonnet (Q&A). The class itself is completed in Tasks 10–12; this
file currently holds loaders and dataclasses only."""
from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from project0.agents._tool_loop import TurnState
from project0.envelope import AgentResult, Envelope
from project0.intelligence.generate import generate_daily_report
from project0.intelligence.report import list_report_dates, read_report
from project0.intelligence.source import TwitterSource, TwitterSourceError
from project0.intelligence.watchlist import WatchEntry
from project0.llm.tools import ToolCall, ToolSpec

if TYPE_CHECKING:
    from project0.llm.provider import LLMProvider
    from project0.store import MessagesStore

log = logging.getLogger(__name__)


# --- persona -----------------------------------------------------------------

@dataclass(frozen=True)
class IntelligencePersona:
    core: str
    dm_mode: str
    group_addressed_mode: str
    delegated_mode: str
    tool_use_guide: str


# Canonical headers use the full-width colon '：' (U+FF1A). The near-miss
# detector normalizes half-width ':' (U+003A) to full-width before comparing,
# so a typo like "# 模式:私聊" is caught with a helpful suggestion instead of
# silently producing a "missing section" error.
_PERSONA_SECTIONS = {
    "core":                 "# 情报 — 角色设定",
    "dm_mode":               "# 模式：私聊",
    "group_addressed_mode":  "# 模式：群聊点名",
    "delegated_mode":        "# 模式：被经理委派",
    "tool_use_guide":        "# 模式：工具使用守则",
}


def _normalize_header(h: str) -> str:
    """Collapse whitespace and normalise colon variants so near-miss
    headers (half-width ':' instead of full-width '：') are detected."""
    return "".join(h.split()).replace(":", "：")


_CANONICAL_HEADERS_NORMALIZED = {
    _normalize_header(h): h for h in _PERSONA_SECTIONS.values()
}


def load_intelligence_persona(path: Path) -> IntelligencePersona:
    """Parse prompts/intelligence.md into its five sections. Each section
    starts with one of the canonical Chinese headers; the header line must
    match exactly (after stripping trailing whitespace). Lines starting
    with '#' that look close to a canonical header but don't match exactly
    raise ValueError — this catches missing-space and colon-mismatch typos
    before they turn into confusing 'missing section' errors."""
    text = path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    lines = text.splitlines()
    current_key: str | None = None
    current_buf: list[str] = []
    header_to_key = {v: k for k, v in _PERSONA_SECTIONS.items()}
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped in header_to_key:
            if current_key is not None:
                sections[current_key] = "\n".join(current_buf).strip()
            current_key = header_to_key[stripped]
            current_buf = [stripped]
            continue
        if stripped.startswith("#"):
            normalized = _normalize_header(stripped)
            if normalized in _CANONICAL_HEADERS_NORMALIZED:
                canonical = _CANONICAL_HEADERS_NORMALIZED[normalized]
                raise ValueError(
                    f"{path}:{lineno}: malformed section header "
                    f"{stripped!r}; expected exactly {canonical!r}"
                )
        if current_key is not None:
            current_buf.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(current_buf).strip()

    for key, header in _PERSONA_SECTIONS.items():
        if key not in sections or not sections[key]:
            raise ValueError(
                f"persona file {path} is missing section '{header}'"
            )

    return IntelligencePersona(
        core=sections["core"],
        dm_mode=sections["dm_mode"],
        group_addressed_mode=sections["group_addressed_mode"],
        delegated_mode=sections["delegated_mode"],
        tool_use_guide=sections["tool_use_guide"],
    )


# --- config ------------------------------------------------------------------

@dataclass(frozen=True)
class IntelligenceConfig:
    summarizer_model: str
    summarizer_max_tokens: int
    qa_model: str
    qa_max_tokens: int
    transcript_window: int
    max_tool_iterations: int
    timeline_since_hours: int
    max_tweets_per_handle: int


def load_intelligence_config(path: Path) -> IntelligenceConfig:
    """Parse prompts/intelligence.toml. Missing keys raise RuntimeError
    with enough context to locate the offending file and key. [[watch]]
    entries are ignored here — load_watchlist handles those separately."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    def _require(section: str, key: str) -> Any:
        node = data
        for seg in section.split("."):
            if seg not in node:
                raise RuntimeError(
                    f"missing config section [{section}] in {path}"
                )
            node = node[seg]
        if key not in node:
            raise RuntimeError(
                f"missing config key {section}.{key} in {path}"
            )
        return node[key]

    return IntelligenceConfig(
        summarizer_model=str(_require("llm.summarizer", "model")),
        summarizer_max_tokens=int(_require("llm.summarizer", "max_tokens")),
        qa_model=str(_require("llm.qa", "model")),
        qa_max_tokens=int(_require("llm.qa", "max_tokens")),
        transcript_window=int(_require("context", "transcript_window")),
        max_tool_iterations=int(_require("context", "max_tool_iterations")),
        timeline_since_hours=int(_require("twitter", "timeline_since_hours")),
        max_tweets_per_handle=int(_require("twitter", "max_tweets_per_handle")),
    )


# --- tool input schemas ------------------------------------------------------

_GENERATE_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {
            "type": "string",
            "description": "YYYY-MM-DD; defaults to today in user_tz",
        },
    },
    "required": [],
}

_GET_LATEST_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}

_GET_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {"type": "string", "description": "YYYY-MM-DD"},
    },
    "required": ["date"],
}

_LIST_REPORTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "minimum": 1, "maximum": 30},
    },
    "required": [],
}


# --- Intelligence class -------------------------------------------------------

class Intelligence:
    """LLM-backed briefing specialist. Two LLM providers: Opus for the
    deterministic summarization pipeline, Sonnet for the agentic Q&A
    loop. Four tools: generate_daily_report, get_latest_report,
    get_report, list_reports. Never delegates."""

    def __init__(
        self,
        *,
        llm_summarizer: "LLMProvider",
        llm_qa: "LLMProvider",
        twitter: TwitterSource,
        messages_store: "MessagesStore | None",
        persona: IntelligencePersona,
        config: IntelligenceConfig,
        watchlist: list[WatchEntry],
        reports_dir: Path,
        user_tz: ZoneInfo,
    ) -> None:
        self._llm_summarizer = llm_summarizer
        self._llm_qa = llm_qa
        self._twitter = twitter
        self._messages = messages_store
        self._persona = persona
        self._config = config
        self._watchlist = watchlist
        self._reports_dir = reports_dir
        self._user_tz = user_tz
        self._tool_specs = self._build_tool_specs()

    def _build_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="generate_daily_report",
                description=(
                    "Fetch tweets from the watchlist and write a new daily "
                    "report JSON file. Use only when the user clearly asked "
                    "to generate a new report."
                ),
                input_schema=_GENERATE_REPORT_SCHEMA,
            ),
            ToolSpec(
                name="get_latest_report",
                description=(
                    "Read the most recent daily report. DO NOT CALL this "
                    "when today's report is already injected into your "
                    "context — only call it as a fallback."
                ),
                input_schema=_GET_LATEST_REPORT_SCHEMA,
            ),
            ToolSpec(
                name="get_report",
                description="Read a specific past daily report by date.",
                input_schema=_GET_REPORT_SCHEMA,
            ),
            ToolSpec(
                name="list_reports",
                description=(
                    "List available report dates (most recent first)."
                ),
                input_schema=_LIST_REPORTS_SCHEMA,
            ),
        ]

    async def _dispatch_tool(
        self,
        call: ToolCall,
        turn_state: TurnState,
    ) -> tuple[str, bool]:
        """Execute one tool call. Intelligence never delegates, so
        ``turn_state`` is accepted for interface symmetry but not mutated."""
        del turn_state  # unused; Intelligence never delegates
        try:
            return await self._dispatch_tool_inner(call)
        except TwitterSourceError as e:
            log.warning("twitter error in tool %s: %s", call.name, e)
            return f"twitter source error: {e}", True
        except (KeyError, ValueError, TypeError) as e:
            log.warning("input error in tool %s: %s", call.name, e)
            return f"invalid input for {call.name}: {e}", True

    async def _dispatch_tool_inner(
        self,
        call: ToolCall,
    ) -> tuple[str, bool]:
        name = call.name
        inp = call.input

        if name == "generate_daily_report":
            date_str = inp.get("date")
            target_date = (
                date.fromisoformat(date_str)
                if date_str
                else datetime.now(tz=self._user_tz).date()
            )
            report = await generate_daily_report(
                target_date=target_date,
                source=self._twitter,
                llm=self._llm_summarizer,
                summarizer_max_tokens=self._config.summarizer_max_tokens,
                watchlist=self._watchlist,
                reports_dir=self._reports_dir,
                user_tz=self._user_tz,
                timeline_since_hours=self._config.timeline_since_hours,
                max_tweets_per_handle=self._config.max_tweets_per_handle,
            )
            return json.dumps({
                "path": str(self._reports_dir / f"{target_date}.json"),
                "item_count": len(report.get("news_items", [])),
                "tweets_fetched": report["stats"]["tweets_fetched"],
                "handles_failed": len(report["stats"]["errors"]),
            }, ensure_ascii=False), False

        if name == "get_latest_report":
            dates = list_report_dates(self._reports_dir)
            if not dates:
                return "no reports available", False
            latest = read_report(self._reports_dir / f"{dates[0]}.json")
            return json.dumps(latest, ensure_ascii=False), False

        if name == "get_report":
            target = date.fromisoformat(inp["date"])
            path = self._reports_dir / f"{target}.json"
            if not path.exists():
                return f"no report for {target}", False
            report = read_report(path)
            return json.dumps(report, ensure_ascii=False), False

        if name == "list_reports":
            limit = int(inp.get("limit", 7))
            dates = list_report_dates(self._reports_dir)[:limit]
            results = []
            for d in dates:
                rep = read_report(self._reports_dir / f"{d}.json")
                results.append({
                    "date": d.isoformat(),
                    "item_count": len(rep.get("news_items", [])),
                })
            return json.dumps(results, ensure_ascii=False), False

        return f"unknown tool: {name}", True

    async def handle(self, env: Envelope) -> AgentResult | None:
        reason = env.routing_reason
        if reason == "direct_dm":
            return await self._run_chat_turn(env, self._persona.dm_mode)
        if reason in ("mention", "focus"):
            return await self._run_chat_turn(env, self._persona.group_addressed_mode)
        if reason == "default_manager":
            return await self._run_delegated_turn(env)
        log.debug("intelligence: ignoring routing_reason=%s", reason)
        return None

    def _try_read_latest_report(self) -> dict[str, Any] | None:
        dates = list_report_dates(self._reports_dir)
        if not dates:
            return None
        try:
            return read_report(self._reports_dir / f"{dates[0]}.json")
        except (ValueError, OSError) as e:
            log.warning(
                "intelligence: failed to read latest report %s: %s",
                dates[0], e,
            )
            return None

    def _recent_messages(
        self, chat_id: int | None, *, source: str | None = None
    ) -> list[Envelope]:
        """Load transcript envelopes for a chat. DM mode uses
        ``recent_for_dm`` to scope by (chat_id, 'intelligence') because
        Telegram reuses one chat_id across every bot a user DMs."""
        if chat_id is None or self._messages is None:
            return []
        if source == "telegram_dm":
            return self._messages.recent_for_dm(
                chat_id=chat_id,
                agent="intelligence",
                limit=self._config.transcript_window,
            )
        return self._messages.recent_for_chat(
            chat_id=chat_id, limit=self._config.transcript_window
        )

    async def _run_chat_turn(
        self, env: Envelope, mode_section: str
    ) -> AgentResult | None:
        from project0.intelligence.summarizer_prompt import build_qa_user_prompt

        latest = self._try_read_latest_report()
        transcript = self._recent_messages(env.telegram_chat_id, source=env.source)
        system = (
            self._persona.core
            + "\n\n" + mode_section
            + "\n\n" + self._persona.tool_use_guide
        )
        initial_user_text = build_qa_user_prompt(
            latest_report=latest,
            current_date_local=datetime.now(tz=self._user_tz).date(),
            recent_messages=transcript,
            current_user_message=env.body,
        )
        return await self._run_loop(
            system=system,
            initial_user_text=initial_user_text,
        )

    async def _run_delegated_turn(self, env: Envelope) -> AgentResult | None:
        from project0.intelligence.summarizer_prompt import build_delegated_user_prompt

        payload = env.payload or {}
        query = payload.get("query") or env.body or ""
        latest = self._try_read_latest_report()
        system = (
            self._persona.core
            + "\n\n" + self._persona.delegated_mode
            + "\n\n" + self._persona.tool_use_guide
        )
        initial_user_text = build_delegated_user_prompt(
            latest_report=latest,
            current_date_local=datetime.now(tz=self._user_tz).date(),
            query=query,
        )
        return await self._run_loop(
            system=system,
            initial_user_text=initial_user_text,
        )

    async def _run_loop(
        self,
        *,
        system: str,
        initial_user_text: str,
    ) -> AgentResult | None:
        from project0.agents._tool_loop import run_agentic_loop

        loop = await run_agentic_loop(
            llm=self._llm_qa,
            system=system,
            initial_user_text=initial_user_text,
            tools=self._tool_specs,
            dispatch_tool=self._dispatch_tool,
            max_iterations=self._config.max_tool_iterations,
            max_tokens=self._config.qa_max_tokens,
        )
        if loop.errored:
            return None
        # Intelligence never delegates — ignore turn_state.
        return AgentResult(
            reply_text=loop.final_text or "",
            delegate_to=None,
            handoff_text=None,
        )
