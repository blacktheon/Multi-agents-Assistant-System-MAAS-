"""Learning agent — persona/config loading, tool dispatch, pulse handling.

温书瑶 (Wen Shuyao) — the Learning Agent. Manages knowledge base entries
via Notion, schedules spaced-repetition reviews, and summarizes content
the user sends (links or raw text).
"""

from __future__ import annotations

import contextlib
import json
import logging
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from project0.agents._tool_loop import (  # re-exported for tests
    TurnState,
    run_agentic_loop,
)
from project0.envelope import AgentResult, Envelope
from project0.llm.provider import SystemBlocks
from project0.llm.tools import ToolCall, ToolSpec
from project0.notion.model import NotionClientError

if TYPE_CHECKING:
    from collections.abc import Callable

    from project0.llm.provider import LLMProvider
    from project0.notion.client import NotionClient
    from project0.store import (
        KnowledgeIndexStore,
        MessagesStore,
        ReviewScheduleStore,
        UserFactsReader,
        UserProfile,
    )

log = logging.getLogger(__name__)


# --- persona -----------------------------------------------------------------

@dataclass(frozen=True)
class LearningPersona:
    core: str
    dm_mode: str
    group_addressed_mode: str
    pulse_mode: str
    tool_use_guide: str


_PERSONA_SECTIONS = {
    "core":                 "# 学习助手 — 角色设定",
    "dm_mode":              "# 模式：私聊",
    "group_addressed_mode": "# 模式：群聊点名",
    "pulse_mode":           "# 模式：定时脉冲",
    "tool_use_guide":       "# 模式：工具使用守则",
}


def _normalize_header(h: str) -> str:
    """Collapse whitespace and normalise colon variants so that near-miss
    headers (e.g. half-width ':' instead of full-width '：') are detected."""
    return "".join(h.split()).replace(":", "：")


_CANONICAL_HEADERS_NORMALIZED = {
    _normalize_header(h): h for h in _PERSONA_SECTIONS.values()
}


def load_learning_persona(path: Path) -> LearningPersona:
    """Parse prompts/learning.md into its five sections. Each section starts
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

    return LearningPersona(
        core=sections["core"],
        dm_mode=sections["dm_mode"],
        group_addressed_mode=sections["group_addressed_mode"],
        pulse_mode=sections["pulse_mode"],
        tool_use_guide=sections["tool_use_guide"],
    )


# --- config ------------------------------------------------------------------

@dataclass(frozen=True)
class LearningConfig:
    model: str
    max_tokens_reply: int
    max_tool_iterations: int
    transcript_window: int
    sync_interval_seconds: int
    reminder_interval_seconds: int
    intervals_days: list[int]
    max_summary_tokens: int


def load_learning_config(path: Path) -> LearningConfig:
    """Parse prompts/learning.toml. Missing keys raise RuntimeError with
    enough context to locate the offending file and key. [[pulse]] entries
    are intentionally ignored here — the pulse scheduler handles those
    separately."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    def _require(section: str, key: str) -> Any:
        try:
            return data[section][key]
        except KeyError as e:
            raise RuntimeError(
                f"missing config key {section}.{key} in {path}"
            ) from e

    return LearningConfig(
        model=str(_require("llm", "model")),
        max_tokens_reply=int(_require("llm", "max_tokens_reply")),
        max_tool_iterations=int(_require("llm", "max_tool_iterations")),
        transcript_window=int(_require("context", "transcript_window")),
        sync_interval_seconds=int(_require("notion", "sync_interval_seconds")),
        reminder_interval_seconds=int(_require("review", "reminder_interval_seconds")),
        intervals_days=list(_require("review", "intervals_days")),
        max_summary_tokens=int(_require("processing", "max_summary_tokens")),
    )


# --- tool input schemas ------------------------------------------------------

_PROCESS_LINK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "The URL to fetch and summarize."},
        "user_notes": {"type": "string", "description": "Optional user notes about this link."},
    },
    "required": ["url"],
}

_PROCESS_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text":       {"type": "string", "description": "The raw text to summarize and store."},
        "user_notes": {"type": "string", "description": "Optional notes from the user."},
    },
    "required": ["text"],
}

_LIST_UPCOMING_REVIEWS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "days_ahead": {
            "type": "integer",
            "minimum": 1,
            "description": "How many days ahead to look. Default 7.",
        },
    },
    "required": [],
}

_MARK_REVIEWED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page_id": {"type": "string", "description": "Notion page ID to mark as reviewed."},
    },
    "required": ["page_id"],
}

_LIST_ENTRIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tag":   {"type": "string", "description": "Optional tag to filter entries by."},
        "limit": {"type": "integer", "minimum": 1, "description": "Max entries to return."},
    },
    "required": [],
}

_GET_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page_id": {"type": "string", "description": "Notion page ID of the entry to fetch."},
    },
    "required": ["page_id"],
}


# --- Learning class ----------------------------------------------------------

class LearningAgent:
    def __init__(
        self,
        *,
        llm: LLMProvider | None,
        notion: NotionClient | None,
        knowledge_index: KnowledgeIndexStore | None,
        review_schedule: ReviewScheduleStore | None,
        messages_store: MessagesStore | None,
        persona: LearningPersona,
        config: LearningConfig,
        user_tz: ZoneInfo | None = None,
        clock: Callable[[], datetime] | None = None,
        user_profile: UserProfile | None = None,
        user_facts_reader: UserFactsReader | None = None,
    ) -> None:
        self._llm = llm
        self._notion = notion
        self._knowledge_index = knowledge_index
        self._review_schedule = review_schedule
        self._messages = messages_store
        self._persona = persona
        self._config = config
        self._user_tz = user_tz or ZoneInfo("UTC")
        self._clock = clock
        self._user_profile = user_profile
        self._user_facts_reader = user_facts_reader
        self._tool_specs = self._build_tool_specs()

    def _now_local(self) -> datetime:
        if self._clock is not None:
            return self._clock().astimezone(self._user_tz)
        return datetime.now(UTC).astimezone(self._user_tz)

    def _today_iso(self) -> str:
        return self._now_local().date().isoformat()

    def _build_tool_specs(self) -> list[ToolSpec]:
        """Return the six tool specs advertised to the model."""
        return [
            ToolSpec(
                name="process_link",
                description="Fetch a URL, extract and summarize its content, store in Notion.",
                input_schema=_PROCESS_LINK_SCHEMA,
            ),
            ToolSpec(
                name="process_text",
                description="Summarize raw text and store as a knowledge entry in Notion.",
                input_schema=_PROCESS_TEXT_SCHEMA,
            ),
            ToolSpec(
                name="list_upcoming_reviews",
                description="List knowledge entries due for review.",
                input_schema=_LIST_UPCOMING_REVIEWS_SCHEMA,
            ),
            ToolSpec(
                name="mark_reviewed",
                description="Mark a knowledge entry as reviewed, advancing its review schedule.",
                input_schema=_MARK_REVIEWED_SCHEMA,
            ),
            ToolSpec(
                name="list_entries",
                description="List active knowledge entries, optionally filtered by tag.",
                input_schema=_LIST_ENTRIES_SCHEMA,
            ),
            ToolSpec(
                name="get_entry",
                description="Fetch full details of a knowledge entry by Notion page ID.",
                input_schema=_GET_ENTRY_SCHEMA,
            ),
        ]

    # --- tool dispatch -------------------------------------------------------

    async def _dispatch_tool(
        self,
        call: ToolCall,
        turn_state: TurnState,
    ) -> tuple[str, bool]:
        """Execute one tool call and return (content_str, is_error)."""
        try:
            return await self._dispatch_tool_inner(call, turn_state)
        except NotionClientError as exc:
            log.warning("notion error in tool %s: %s", call.name, exc)
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

        if name == "process_link":
            return await self._tool_process_link(
                url=inp["url"],
                user_notes=inp.get("user_notes"),
            )

        if name == "process_text":
            return await self._tool_process_text(
                text=inp["text"],
                user_notes=inp.get("user_notes"),
            )

        if name == "list_upcoming_reviews":
            return await self._tool_list_upcoming_reviews(
                days_ahead=int(inp.get("days_ahead", 7)),
            )

        if name == "mark_reviewed":
            return await self._tool_mark_reviewed(page_id=inp["page_id"])

        if name == "list_entries":
            return await self._tool_list_entries(
                tag=inp.get("tag"),
                limit=int(inp["limit"]) if "limit" in inp else None,
            )

        if name == "get_entry":
            return await self._tool_get_entry(page_id=inp["page_id"])

        return f"unknown tool: {name}", True

    # --- individual tool implementations -------------------------------------

    async def _tool_process_link(
        self, url: str, user_notes: str | None
    ) -> tuple[str, bool]:
        """Fetch URL, extract text via trafilatura, summarize, store in Notion."""
        import httpx
        import trafilatura  # type: ignore[import-untyped]

        headers = {"User-Agent": "MAAS/1.0 (Knowledge Bot; +https://github.com)"}
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=30, headers=headers
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.HTTPError as e:
            return f"failed to fetch URL: {e}", True
        html = resp.text

        extracted = trafilatura.extract(html)
        if not extracted:
            return "could not extract readable content from the URL", True

        summary_data = await self._summarize_content(extracted, user_notes)

        assert self._notion is not None
        entry = await self._notion.create_page(
            title=summary_data["title"],
            body_markdown=summary_data["summary"],
            source_url=url,
            source_type="link",
            tags=summary_data.get("tags", []),
            user_notes=user_notes,
        )

        self._index_entry(entry)
        self._schedule_review(entry.page_id)

        result = {
            "page_id": entry.page_id,
            "title": entry.title,
            "tags": entry.tags,
            "next_review": self._next_review_date(),
        }
        return json.dumps(result, ensure_ascii=False), False

    async def _tool_process_text(
        self, text: str, user_notes: str | None
    ) -> tuple[str, bool]:
        """Summarize raw text, store in Notion."""
        summary_data = await self._summarize_content(text, user_notes)

        assert self._notion is not None
        entry = await self._notion.create_page(
            title=summary_data["title"],
            body_markdown=summary_data["summary"],
            source_type="text",
            tags=summary_data.get("tags", []),
            user_notes=user_notes,
        )

        self._index_entry(entry)
        self._schedule_review(entry.page_id)

        result = {
            "page_id": entry.page_id,
            "title": entry.title,
            "tags": entry.tags,
            "next_review": self._next_review_date(),
        }
        return json.dumps(result, ensure_ascii=False), False

    async def _tool_list_upcoming_reviews(
        self, days_ahead: int = 7
    ) -> tuple[str, bool]:
        assert self._review_schedule is not None
        today = self._now_local().date()
        end_date = today + timedelta(days=days_ahead)
        items = self._review_schedule.due_items(end_date.isoformat())
        result = [
            {
                "page_id": item["notion_page_id"],
                "title": item["title"],
                "next_review": item["next_review"],
                "times_reviewed": item["times_reviewed"],
                "tags": item.get("tags", []),
            }
            for item in items
        ]
        return json.dumps(result, ensure_ascii=False), False

    async def _tool_mark_reviewed(self, page_id: str) -> tuple[str, bool]:
        assert self._review_schedule is not None
        today = self._today_iso()
        self._review_schedule.mark_reviewed(page_id, today)
        return json.dumps({"marked": page_id, "reviewed_on": today}), False

    async def _tool_list_entries(
        self, tag: str | None = None, limit: int | None = None
    ) -> tuple[str, bool]:
        assert self._knowledge_index is not None
        entries = self._knowledge_index.list_active()
        if tag:
            entries = [e for e in entries if tag in e.get("tags", [])]
        if limit:
            entries = entries[:limit]
        result = [
            {
                "page_id": e["notion_page_id"],
                "title": e["title"],
                "source_url": e.get("source_url"),
                "tags": e.get("tags", []),
                "created_at": e.get("created_at"),
            }
            for e in entries
        ]
        return json.dumps(result, ensure_ascii=False), False

    async def _tool_get_entry(self, page_id: str) -> tuple[str, bool]:
        assert self._notion is not None
        entry = await self._notion.get_page(page_id)
        result = {
            "page_id": entry.page_id,
            "title": entry.title,
            "source_url": entry.source_url,
            "source_type": entry.source_type,
            "tags": entry.tags,
            "user_notes": entry.user_notes,
            "body": entry.body,
            "created_at": entry.created_at.isoformat(),
        }
        return json.dumps(result, ensure_ascii=False), False

    # --- helpers -------------------------------------------------------------

    async def _summarize_content(
        self, content: str, user_notes: str | None
    ) -> dict[str, Any]:
        """Use a separate LLM call to extract title, summary, and tags."""
        assert self._llm is not None
        prompt = (
            "你是一个知识整理助手。请阅读以下内容，返回一个 JSON 对象，包含：\n"
            '- "title": 简短中文标题（15字以内）\n'
            '- "summary": 要点总结（用 Markdown 格式，简洁有力）\n'
            '- "tags": 标签列表（1-5个英文标签）\n\n'
            f"内容：\n{content[:8000]}\n"
        )
        if user_notes:
            prompt += f"\n用户备注：{user_notes}\n"
        prompt += "\n请直接返回 JSON，不要额外解释。"

        from project0.llm.provider import Msg
        raw = await self._llm.complete(
            system="你是一个知识整理助手。只返回合法 JSON。",
            messages=[Msg(role="user", content=prompt)],
            max_tokens=self._config.max_summary_tokens,
            agent="learning",
            purpose="summarize",
        )

        # Parse the JSON response — strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            # Remove opening and closing fences
            lines = text.splitlines()
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines)
        return json.loads(text)

    def _index_entry(self, entry: Any) -> None:
        """Update local knowledge_index with an entry from Notion."""
        if self._knowledge_index is None:
            return
        self._knowledge_index.upsert(
            notion_page_id=entry.page_id,
            title=entry.title,
            source_url=entry.source_url,
            source_type=entry.source_type,
            tags=entry.tags,
            user_notes=entry.user_notes,
            status=entry.status,
            created_at=entry.created_at.isoformat(),
            last_edited=entry.last_edited.isoformat(),
        )

    def _schedule_review(self, page_id: str) -> None:
        """Create a review schedule entry for a newly created knowledge entry."""
        if self._review_schedule is None:
            return
        first_interval = self._config.intervals_days[0] if self._config.intervals_days else 1
        first_date = (self._now_local().date() + timedelta(days=first_interval)).isoformat()
        self._review_schedule.create(page_id, first_date)

    def _next_review_date(self) -> str:
        """Return the date string for the first review of a new entry."""
        first_interval = self._config.intervals_days[0] if self._config.intervals_days else 1
        return (self._now_local().date() + timedelta(days=first_interval)).isoformat()

    # --- handle routing ------------------------------------------------------

    async def handle(self, env: Envelope) -> AgentResult | None:
        reason = env.routing_reason
        if reason == "direct_dm":
            return await self._run_chat_turn(env, self._persona.dm_mode)
        if reason in ("mention", "focus"):
            return await self._run_chat_turn(env, self._persona.group_addressed_mode)
        if reason == "pulse":
            return await self._run_pulse_turn(env)
        log.debug("learning: ignoring routing_reason=%s", reason)
        return None

    def _assemble_system_blocks(self, mode_section: str) -> SystemBlocks:
        """Build a two-segment system prompt."""
        stable_parts = [
            self._persona.core,
            "",
            mode_section,
            "",
            self._persona.tool_use_guide,
        ]
        if self._user_profile is not None:
            block = self._user_profile.as_prompt_block()
            if block:
                stable_parts.append("")
                stable_parts.append(block)
        stable = "\n".join(stable_parts)

        facts: str | None = None
        if self._user_facts_reader is not None:
            b = self._user_facts_reader.as_prompt_block()
            facts = b if b else None

        return SystemBlocks(stable=stable, facts=facts)

    def _load_transcript(
        self, chat_id: int | None, *, source: str | None = None
    ) -> str:
        """Load transcript for a chat. In DM mode the result is scoped to
        this agent via ``recent_for_dm`` because Telegram reuses one
        chat_id across every bot the user DMs."""
        if chat_id is None or self._messages is None:
            return ""
        if source == "telegram_dm":
            envs = self._messages.recent_for_dm(
                chat_id=chat_id,
                agent="learning",
                limit=self._config.transcript_window,
            )
        else:
            envs = self._messages.recent_for_chat(
                chat_id=chat_id,
                visible_to="learning",
                limit=self._config.transcript_window,
            )
        lines: list[str] = []
        for e in envs:
            if e.from_kind == "user":
                lines.append(f"user: {e.body}")
            elif e.from_kind == "agent":
                speaker = e.from_agent or "unknown"
                lines.append(f"{speaker}: {e.body}")
        return "\n".join(lines)

    # --- chat turn -----------------------------------------------------------

    async def _run_chat_turn(
        self, env: Envelope, mode_section: str
    ) -> AgentResult | None:
        system = self._assemble_system_blocks(mode_section)
        transcript = self._load_transcript(env.telegram_chat_id, source=env.source)
        initial_user_text = (
            f"对话记录:\n{transcript}\n\n最新用户消息: {env.body}"
            if transcript else f"最新用户消息: {env.body}"
        )
        return await self._agentic_loop(
            env=env,
            system=system,
            initial_user_text=initial_user_text,
            max_tokens=self._config.max_tokens_reply,
            is_pulse=False,
        )

    # --- pulse turn ----------------------------------------------------------

    async def _run_pulse_turn(self, env: Envelope) -> AgentResult | None:
        payload = env.payload or {}
        pulse_name = payload.get("pulse_name", env.body)

        if pulse_name == "notion_sync":
            return await self._run_notion_sync()

        if pulse_name == "review_reminder":
            return await self._run_review_reminder(env)

        log.warning("learning: unknown pulse_name=%s", pulse_name)
        return None

    async def _run_notion_sync(self) -> AgentResult | None:
        """Query Notion for changes since last sync, update local index.
        Silent — never produces a user-visible reply."""
        if self._notion is None or self._knowledge_index is None:
            log.warning("learning: notion_sync skipped — missing notion or knowledge_index")
            return None

        last_ts = self._knowledge_index.last_sync_timestamp()
        since = (
            datetime.fromisoformat(last_ts) if last_ts
            else datetime(2000, 1, 1, tzinfo=UTC)
        )

        entries = await self._notion.query_changed_since(since)
        for entry in entries:
            self._index_entry(entry)
            if entry.status == "active":
                existing = self._knowledge_index.get(entry.page_id)
                if existing and self._review_schedule is not None:
                    with contextlib.suppress(Exception):
                        self._schedule_review(entry.page_id)

        # Detect deletions: pages that exist locally but are gone from Notion.
        # query_changed_since only returns pages that still exist, so deleted
        # pages silently disappear. We do a full query_all and compare.
        try:
            all_notion = await self._notion.query_all(limit=200)
        except NotionClientError as e:
            log.warning("learning: notion_sync full query failed: %s", e)
            all_notion = None

        if all_notion is not None:
            notion_ids = {e.page_id for e in all_notion}
            local_active = self._knowledge_index.list_active()
            for local in local_active:
                if local["notion_page_id"] not in notion_ids:
                    self._knowledge_index.upsert(
                        notion_page_id=local["notion_page_id"],
                        title=local["title"],
                        source_url=local.get("source_url"),
                        source_type=local["source_type"],
                        tags=local.get("tags", []),
                        user_notes=local.get("user_notes"),
                        status="deleted",
                        created_at=local["created_at"],
                        last_edited=local["last_edited"],
                    )
                    if self._review_schedule is not None:
                        self._review_schedule.set_active(
                            local["notion_page_id"], False
                        )
                    log.info(
                        "learning: notion_sync deactivated deleted entry: %s",
                        local["title"],
                    )

        log.info("learning: notion_sync processed %d entries", len(entries))
        return None

    async def _run_review_reminder(self, env: Envelope) -> AgentResult | None:
        """Check due items and generate a reminder via LLM if any are due."""
        if self._review_schedule is None:
            return None

        today = self._today_iso()
        due = self._review_schedule.due_items(today)
        if not due:
            return None

        # Build a reminder via the LLM
        system = self._assemble_system_blocks(self._persona.pulse_mode)
        items_text = "\n".join(
            f"- {item['title']} (page_id: {item['notion_page_id']}, "
            f"已复习 {item['times_reviewed']} 次)"
            for item in due
        )
        initial_user_text = (
            f"定时脉冲被触发: review_reminder\n\n"
            f"以下条目到期需要复习：\n{items_text}\n\n"
            f"请用温柔的语气提醒少爷复习这些条目。"
        )
        return await self._agentic_loop(
            env=env,
            system=system,
            initial_user_text=initial_user_text,
            max_tokens=self._config.max_tokens_reply,
            is_pulse=True,
        )

    # --- agentic loop wrapper ------------------------------------------------

    async def _agentic_loop(
        self,
        *,
        env: Envelope,
        system: SystemBlocks,
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
            agent="learning",
            purpose="tool_loop",
            envelope_id=env.id,
        )
        if loop.errored:
            return None

        # Pulse review_reminder: the LLM generates the reminder text.
        # Return it as a reply so the orchestrator can send it.
        if is_pulse and loop.final_text:
            return AgentResult(
                reply_text=loop.final_text,
                delegate_to=None,
                handoff_text=None,
            )

        # Pulse with no text (notion_sync should not reach here, but guard)
        if is_pulse:
            return None

        return AgentResult(
            reply_text=loop.final_text or "",
            delegate_to=None,
            handoff_text=None,
        )
