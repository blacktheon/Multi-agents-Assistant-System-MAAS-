"""Manager agent — loader functions for persona and config."""

from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from project0.agents._tool_loop import LoopResult, TurnState, run_agentic_loop  # re-exported for tests
from project0.calendar.errors import GoogleCalendarError
from project0.envelope import AgentResult, Envelope
from project0.llm.provider import LLMProviderError
from project0.llm.tools import ToolCall, ToolSpec

if TYPE_CHECKING:
    from collections.abc import Callable

    from project0.calendar.client import GoogleCalendar
    from project0.llm.provider import LLMProvider
    from project0.store import AgentMemory, MessagesStore

log = logging.getLogger(__name__)


# --- persona -----------------------------------------------------------------

@dataclass(frozen=True)
class ManagerPersona:
    core: str
    dm_mode: str
    group_addressed_mode: str
    pulse_mode: str
    tool_use_guide: str


_PERSONA_SECTIONS = {
    "core":                 "# 经理 — 角色设定",
    "dm_mode":               "# 模式：私聊",
    "group_addressed_mode":  "# 模式：群聊点名",
    "pulse_mode":            "# 模式：定时脉冲",
    "tool_use_guide":        "# 模式：工具使用守则",
}

def _normalize_header(h: str) -> str:
    """Collapse whitespace and normalise colon variants so that near-miss
    headers (e.g. half-width ':' instead of full-width '：') are detected."""
    return "".join(h.split()).replace(":", "：")


_CANONICAL_HEADERS_NORMALIZED = {
    _normalize_header(h): h for h in _PERSONA_SECTIONS.values()
}


def load_manager_persona(path: Path) -> ManagerPersona:
    """Parse prompts/manager.md into its five sections. Each section starts
    with one of the canonical Chinese headers; the header line must match
    exactly (after stripping trailing whitespace). Lines starting with '#'
    that look close to a canonical header but don't match exactly raise
    ValueError — this catches missing-space and colon-mismatch typos before
    they turn into confusing 'missing section' errors."""
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
            # Include the header line so callers can verify section identity
            # by checking for Chinese keywords that appear in the header.
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
            raise ValueError(f"persona file {path} is missing section '{header}'")

    return ManagerPersona(
        core=sections["core"],
        dm_mode=sections["dm_mode"],
        group_addressed_mode=sections["group_addressed_mode"],
        pulse_mode=sections["pulse_mode"],
        tool_use_guide=sections["tool_use_guide"],
    )


# --- config ------------------------------------------------------------------

@dataclass(frozen=True)
class ManagerConfig:
    model: str
    max_tokens_reply: int
    max_tool_iterations: int
    transcript_window: int


def load_manager_config(path: Path) -> ManagerConfig:
    """Parse prompts/manager.toml. Missing keys raise RuntimeError with
    enough context to locate the offending file and key. [[pulse]] entries
    are intentionally ignored here — Task 5's load_pulse_entries handles
    those separately."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    def _require(section: str, key: str) -> Any:
        try:
            return data[section][key]
        except KeyError as e:
            raise RuntimeError(
                f"missing config key {section}.{key} in {path}"
            ) from e

    return ManagerConfig(
        model=str(_require("llm", "model")),
        max_tokens_reply=int(_require("llm", "max_tokens_reply")),
        max_tool_iterations=int(_require("llm", "max_tool_iterations")),
        transcript_window=int(_require("context", "transcript_window")),
    )


# --- tool input schemas ------------------------------------------------------

_LIST_EVENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "time_min":    {"type": "string", "description": "ISO8601 start time (inclusive)"},
        "time_max":    {"type": "string", "description": "ISO8601 end time (exclusive)"},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 250},
    },
    "required": ["time_min", "time_max"],
}

_CREATE_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary":     {"type": "string"},
        "start":       {"type": "string", "description": "ISO8601 aware datetime"},
        "end":         {"type": "string", "description": "ISO8601 aware datetime"},
        "description": {"type": "string"},
        "location":    {"type": "string"},
    },
    "required": ["summary", "start", "end"],
}

_UPDATE_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "event_id":    {"type": "string"},
        "summary":     {"type": "string"},
        "start":       {"type": "string"},
        "end":         {"type": "string"},
        "description": {"type": "string"},
        "location":    {"type": "string"},
    },
    "required": ["event_id"],
}

_DELETE_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"event_id": {"type": "string"}},
    "required": ["event_id"],
}

_DELEGATE_SECRETARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reminder_text": {"type": "string"},
        "appointment":   {"type": "string"},
        "when":          {"type": "string"},
        "note":          {"type": "string"},
    },
    "required": ["reminder_text"],
}

_DELEGATE_INTEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}


# --- Manager class -----------------------------------------------------------

class Manager:
    def __init__(
        self,
        *,
        llm: "LLMProvider | None",
        calendar: "GoogleCalendar | None",
        memory: "AgentMemory | None",
        messages_store: "MessagesStore | None",
        persona: ManagerPersona,
        config: ManagerConfig,
        user_tz: ZoneInfo = ZoneInfo("UTC"),
        clock: "Callable[[], datetime] | None" = None,
    ) -> None:
        self._llm = llm
        self._calendar = calendar
        self._memory = memory
        self._messages = messages_store
        self._persona = persona
        self._config = config
        self._user_tz = user_tz
        self._clock = clock
        self._tool_specs = self._build_tool_specs()

    def _now_local(self) -> datetime:
        if self._clock is not None:
            return self._clock().astimezone(self._user_tz)
        from datetime import UTC
        return datetime.now(UTC).astimezone(self._user_tz)

    def _current_time_preamble(self) -> str:
        """Give the model an unambiguous clock plus pre-computed '今天' and
        '明天' windows so it doesn't have to reason about the sleep-based
        time-word rule. The rule: before 07:00, the user is still in last
        night's tail, so '明天' means today's calendar date from 07:00; at
        07:00 and later it means the next calendar date from 07:00."""
        now = self._now_local()
        weekday_zh = "一二三四五六日"[now.weekday()]

        if now.hour < 7:
            # Pre-dawn: we're still "last night". Today's waking day is
            # today's date from 07:00 onward; tomorrow is still ahead of us.
            today_date = now.date()
            tomorrow_date = today_date  # "next waking day" is today's date
            tomorrow_shift = 0
            tail_note = "（现在还不到 07:00，你还在昨晚的延长线上）"
        else:
            today_date = now.date()
            tomorrow_date = today_date
            tomorrow_shift = 1
            tail_note = ""

        today_start = now
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        from datetime import timedelta
        tmr_start = now.replace(hour=7, minute=0, second=0, microsecond=0) + timedelta(days=tomorrow_shift)
        tmr_end = now.replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(days=tomorrow_shift)

        def _fmt(dt: datetime) -> str:
            return dt.strftime("%Y-%m-%d %H:%M")

        def _iso(dt: datetime) -> str:
            return dt.isoformat()

        lines = [
            f"当前时间：{now.strftime('%Y-%m-%d %H:%M')} 星期{weekday_zh}（{self._user_tz.key}）",
            f"「今天」= 从现在到今天 23:59：{_fmt(today_start)} 至 {_fmt(today_end)}",
            f"  · time_min = {_iso(today_start)}",
            f"  · time_max = {_iso(today_end)}",
            f"「明天」= {_fmt(tmr_start)} 至 {_fmt(tmr_end)}{tail_note}",
            f"  · time_min = {_iso(tmr_start)}",
            f"  · time_max = {_iso(tmr_end)}",
            "查日历时，用户说「今天」就用上面「今天」的 time_min/time_max，说「明天」就用「明天」的。别自己另算一个窗口。",
        ]
        return "\n".join(lines)

    def _build_tool_specs(self) -> list[ToolSpec]:
        """Return the six tool specs advertised to the model."""
        return [
            ToolSpec(
                name="calendar_list_events",
                description="List calendar events within a time range.",
                input_schema=_LIST_EVENTS_SCHEMA,
            ),
            ToolSpec(
                name="calendar_create_event",
                description="Create a new calendar event.",
                input_schema=_CREATE_EVENT_SCHEMA,
            ),
            ToolSpec(
                name="calendar_update_event",
                description="Update an existing calendar event by ID.",
                input_schema=_UPDATE_EVENT_SCHEMA,
            ),
            ToolSpec(
                name="calendar_delete_event",
                description="Delete a calendar event by ID.",
                input_schema=_DELETE_EVENT_SCHEMA,
            ),
            ToolSpec(
                name="delegate_to_secretary",
                description=(
                    "Hand off a reminder or appointment-related task to the Secretary agent."
                ),
                input_schema=_DELEGATE_SECRETARY_SCHEMA,
            ),
            ToolSpec(
                name="delegate_to_intelligence",
                description="Ask the Intelligence agent to answer a research or news query.",
                input_schema=_DELEGATE_INTEL_SCHEMA,
            ),
        ]

    async def _dispatch_tool(
        self,
        call: ToolCall,
        turn_state: TurnState,
    ) -> tuple[str, bool]:
        """Execute one tool call and return (content_str, is_error).

        On success ``is_error`` is False and ``content_str`` is JSON or a
        plain string. On any known exception or unknown tool name,
        ``is_error`` is True and ``content_str`` is a human-readable message.
        """
        try:
            return await self._dispatch_tool_inner(call, turn_state)
        except GoogleCalendarError as exc:
            log.warning("calendar error in tool %s: %s", call.name, exc)
            return str(exc), True
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("input error in tool %s: %s", call.name, exc)
            return f"invalid input for {call.name}: {exc}", True

    async def _dispatch_tool_inner(
        self,
        call: ToolCall,
        turn_state: TurnState,
    ) -> tuple[str, bool]:
        name = call.name
        inp = call.input

        if name == "calendar_list_events":
            time_min = datetime.fromisoformat(inp["time_min"])
            time_max = datetime.fromisoformat(inp["time_max"])
            max_results = int(inp.get("max_results", 250))
            events = await self._calendar.list_events(time_min, time_max, max_results)
            result = [
                {
                    "id": e.id,
                    "summary": e.summary,
                    "start": e.start.isoformat(),
                    "end": e.end.isoformat(),
                    "all_day": e.all_day,
                    "description": e.description,
                    "location": e.location,
                    "html_link": e.html_link,
                }
                for e in events
            ]
            return json.dumps(result, ensure_ascii=False), False

        if name == "calendar_create_event":
            summary = inp["summary"]
            start = datetime.fromisoformat(inp["start"])
            end = datetime.fromisoformat(inp["end"])
            description = inp.get("description")
            location = inp.get("location")
            event = await self._calendar.create_event(
                summary, start, end, description, location
            )
            result = {
                "id": event.id,
                "summary": event.summary,
                "start": event.start.isoformat(),
                "end": event.end.isoformat(),
                "html_link": event.html_link,
            }
            return json.dumps(result, ensure_ascii=False), False

        if name == "calendar_update_event":
            event_id = inp["event_id"]
            start = datetime.fromisoformat(inp["start"]) if "start" in inp else None
            end = datetime.fromisoformat(inp["end"]) if "end" in inp else None
            event = await self._calendar.update_event(
                event_id,
                summary=inp.get("summary"),
                start=start,
                end=end,
                description=inp.get("description"),
                location=inp.get("location"),
            )
            result = {
                "id": event.id,
                "summary": event.summary,
                "start": event.start.isoformat(),
                "end": event.end.isoformat(),
                "html_link": event.html_link,
            }
            return json.dumps(result, ensure_ascii=False), False

        if name == "calendar_delete_event":
            event_id = inp["event_id"]
            await self._calendar.delete_event(event_id)
            return json.dumps({"deleted": event_id}), False

        if name == "delegate_to_secretary":
            reminder_text = inp["reminder_text"]
            turn_state.delegation_target = "secretary"
            turn_state.delegation_handoff = reminder_text
            turn_state.delegation_payload = {
                "kind": "reminder_request",
                "appointment": inp.get("appointment"),
                "when": inp.get("when"),
                "note": inp.get("note"),
            }
            return "delegated", False

        if name == "delegate_to_intelligence":
            query = inp["query"]
            turn_state.delegation_target = "intelligence"
            turn_state.delegation_handoff = query
            turn_state.delegation_payload = {"kind": "query", "query": query}
            return "delegated", False

        return f"unknown tool: {name}", True

    async def handle(self, env: Envelope) -> AgentResult | None:
        reason = env.routing_reason
        if reason == "direct_dm":
            return await self._run_chat_turn(env, self._persona.dm_mode)
        if reason in ("mention", "focus", "default_manager"):
            return await self._run_chat_turn(env, self._persona.group_addressed_mode)
        if reason == "pulse":
            return await self._run_pulse_turn(env)
        log.debug("manager: ignoring routing_reason=%s", reason)
        return None

    def _build_system_prompt(self, mode_section: str) -> str:
        return (
            self._persona.core
            + "\n\n" + mode_section
            + "\n\n" + self._persona.tool_use_guide
        )

    def _load_transcript(self, chat_id: int | None) -> str:
        if chat_id is None or self._messages is None:
            return ""
        envs = self._messages.recent_for_chat(
            chat_id=chat_id, limit=self._config.transcript_window
        )
        lines: list[str] = []
        for e in envs:
            if e.from_kind == "user":
                lines.append(f"user: {e.body}")
            elif e.from_kind == "agent":
                speaker = e.from_agent or "unknown"
                lines.append(f"{speaker}: {e.body}")
        return "\n".join(lines)

    async def _run_chat_turn(
        self, env: Envelope, mode_section: str
    ) -> AgentResult | None:
        system = self._build_system_prompt(mode_section)
        transcript = self._load_transcript(env.telegram_chat_id)
        preamble = self._current_time_preamble()
        initial_user_text = (
            f"{preamble}\n\n对话记录:\n{transcript}\n\n最新用户消息: {env.body}"
            if transcript else f"{preamble}\n\n最新用户消息: {env.body}"
        )
        return await self._agentic_loop(
            system=system,
            initial_user_text=initial_user_text,
            max_tokens=self._config.max_tokens_reply,
            is_pulse=False,
        )

    async def _run_pulse_turn(self, env: Envelope) -> AgentResult | None:
        system = self._build_system_prompt(self._persona.pulse_mode)
        payload = env.payload or {}
        pulse_name = payload.get("pulse_name", env.body)
        payload_json = json.dumps(payload, ensure_ascii=False)
        transcript = self._load_transcript(env.telegram_chat_id)
        preamble = self._current_time_preamble()
        initial_user_text = (
            f"{preamble}\n\n定时脉冲被触发: {pulse_name}\n"
            f"payload: {payload_json}"
        )
        if transcript:
            initial_user_text += f"\n\n最近对话:\n{transcript}"
        return await self._agentic_loop(
            system=system,
            initial_user_text=initial_user_text,
            max_tokens=self._config.max_tokens_reply,
            is_pulse=True,
        )

    async def _agentic_loop(
        self,
        *,
        system: str,
        initial_user_text: str,
        max_tokens: int,
        is_pulse: bool,
    ) -> AgentResult | None:
        assert self._llm is not None
        loop = await run_agentic_loop(
            llm=self._llm,
            system=system,
            initial_user_text=initial_user_text,
            tools=self._tool_specs,
            dispatch_tool=self._dispatch_tool,
            max_iterations=self._config.max_tool_iterations,
            max_tokens=max_tokens,
        )
        if loop.errored:
            return None

        turn_state = loop.turn_state
        if turn_state.delegation_target is not None:
            # Delegation queued: suppress the trailing text, return as delegation.
            return AgentResult(
                reply_text=None,
                delegate_to=turn_state.delegation_target,
                handoff_text=turn_state.delegation_handoff,
                delegation_payload=turn_state.delegation_payload,
            )

        # Pulse path: a plain-text result means "nothing to do". Return None so
        # the orchestrator does NOT emit a visible Telegram message. The pulse
        # envelope itself is already persisted by handle_pulse as the audit
        # trail; Manager's internal reasoning does not need to reach the user.
        if is_pulse:
            return None

        return AgentResult(
            reply_text=loop.final_text or "",
            delegate_to=None,
            handoff_text=None,
        )

