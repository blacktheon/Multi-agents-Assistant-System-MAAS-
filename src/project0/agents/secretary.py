"""Secretary agent — first real LLM-backed agent in Project 0.

Four entry paths, dispatched on Envelope.routing_reason:
  - listener_observation : passive group observer with rich cooldown gate
  - mention / focus      : addressed in group, always replies
  - direct_dm            : DM, always replies, more personal tone
  - manager_delegation (payload kind reminder_request) : Manager-directed
    warm reminder, always replies

Character, voice, and mode-specific instructions live in prompts/secretary.md.
Numeric config (cooldown thresholds, model, sentinel patterns) lives in
prompts/secretary.toml. Both are loaded once at startup.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from project0.agents._tool_loop import run_agentic_loop
from project0.envelope import AgentResult, Envelope
from project0.llm.provider import LLMProvider, LLMProviderError, Msg, SystemBlocks
from project0.llm.tools import ToolCall, ToolSpec
from project0.store import (
    AgentMemory,
    MessagesStore,
    UserFactsReader,
    UserFactsWriter,
    UserProfile,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SecretaryPersona:
    core: str
    listener_mode: str
    group_addressed_mode: str
    dm_mode: str
    reminder_mode: str


_PERSONA_SECTIONS = {
    "core": "# 秘书 — 角色设定",
    "listener_mode": "# 模式：群聊旁观",
    "group_addressed_mode": "# 模式：群聊点名",
    "dm_mode": "# 模式：私聊",
    "reminder_mode": "# 模式：经理委托提醒",
}


_CANONICAL_HEADERS_NORMALIZED = {
    # Collapse whitespace for near-miss comparison.
    "".join(h.split()): h for h in _PERSONA_SECTIONS.values()
}


def load_persona(path: Path) -> SecretaryPersona:
    """Parse prompts/secretary.md into its five sections. Each section
    starts with one of the canonical Chinese headers below; the header line
    must match exactly (after stripping trailing whitespace). Lines starting
    with '#' that look close to a canonical header but don't match exactly
    raise ValueError with a suggestion — this catches missing-space and
    dash-mismatch typos before they turn into confusing 'missing section'
    errors."""
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
            current_buf = []
            continue
        if stripped.startswith("#"):
            normalized = "".join(stripped.split())
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

    return SecretaryPersona(
        core=sections["core"],
        listener_mode=sections["listener_mode"],
        group_addressed_mode=sections["group_addressed_mode"],
        dm_mode=sections["dm_mode"],
        reminder_mode=sections["reminder_mode"],
    )


@dataclass(frozen=True)
class SecretaryConfig:
    t_min_seconds: int
    n_min_messages: int
    l_min_weighted_chars: int
    transcript_window: int
    model: str
    max_tokens_reply: int
    max_tokens_listener: int
    skip_sentinels: list[str]


def load_config(path: Path) -> SecretaryConfig:
    """Parse prompts/secretary.toml. Missing keys raise RuntimeError with
    enough context to locate the offending file and key."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    def _require(section: str, key: str) -> Any:
        try:
            return data[section][key]
        except KeyError as e:
            raise RuntimeError(
                f"missing config key {section}.{key} in {path}"
            ) from e

    return SecretaryConfig(
        t_min_seconds=int(_require("cooldown", "t_min_seconds")),
        n_min_messages=int(_require("cooldown", "n_min_messages")),
        l_min_weighted_chars=int(_require("cooldown", "l_min_weighted_chars")),
        transcript_window=int(_require("context", "transcript_window")),
        model=str(_require("llm", "model")),
        max_tokens_reply=int(_require("llm", "max_tokens_reply")),
        max_tokens_listener=int(_require("llm", "max_tokens_listener")),
        skip_sentinels=list(_require("skip_sentinels", "patterns")),
    )


def weighted_len(s: str) -> int:
    """Count characters with CJK characters weighted 3x. Chinese carries
    more meaning per character than English, so a 60-char Chinese message
    and a 180-char English message represent roughly the same conversational
    density. The cooldown L_min threshold uses this weighted count."""
    total = 0
    for c in s:
        cp = ord(c)
        # Common CJK Unified Ideographs, extensions, and compatibility forms.
        if (
            0x4E00 <= cp <= 0x9FFF      # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF   # Extension A
            or 0x20000 <= cp <= 0x2A6DF # Extension B
            or 0xF900 <= cp <= 0xFAFF   # Compatibility Ideographs
            or 0x3040 <= cp <= 0x30FF   # Hiragana + Katakana (similar density)
        ):
            total += 3
        else:
            total += 1
    return total


def is_skip_sentinel(text: str, sentinels: list[str]) -> bool:
    """Return True if the model's response means 'skip this turn'. Matches
    both exact-equal (after strip+lower) and starts-with-then-non-alnum to
    catch cases like '[skip] nothing really fits here'."""
    if not text or not sentinels:
        return False
    t = text.strip().lower()
    if not t:
        return False
    for raw in sentinels:
        s = raw.strip().lower()
        if not s:
            continue
        if t == s:
            return True
        if t.startswith(s):
            # Next character must not be alphanumeric (avoid matching
            # '[skipthis]' against '[skip]').
            tail = t[len(s):]
            if not tail or not tail[0].isalnum():
                return True
    return False


def remember_about_user_tool_spec() -> ToolSpec:
    """Factory for Secretary's ``remember_about_user`` tool spec. Exposed at
    module scope so tests can assert the schema without instantiating a
    Secretary."""
    return ToolSpec(
        name="remember_about_user",
        description=(
            "Save a short factual note about the user to long-term memory. "
            "Use when the user tells you something personal worth remembering "
            "(birthday, preferences, current work, hobbies). Keep facts to one "
            "short sentence. Do not save anything the user asked you to forget."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "fact_text": {
                    "type": "string",
                    "description": "One short sentence stating the fact.",
                },
                "topic": {
                    "type": "string",
                    "description": "Optional tag, e.g. 'food', 'personal'.",
                },
            },
            "required": ["fact_text"],
        },
    )


class Secretary:
    """First real LLM-backed agent. Dispatches on Envelope.routing_reason.

    Returns AgentResult for paths that reply, or None for paths that do
    nothing (listener path decided to stay silent, cooldown not open, etc).
    The orchestrator treats None as 'observed, no outbound action'.
    """

    def __init__(
        self,
        *,
        llm: LLMProvider,
        memory: AgentMemory,
        messages_store: MessagesStore,
        persona: SecretaryPersona,
        config: SecretaryConfig,
        user_profile: UserProfile | None = None,
        user_facts_reader: UserFactsReader | None = None,
        user_facts_writer: UserFactsWriter | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._messages = messages_store
        self._persona = persona
        self._config = config
        self._user_profile = user_profile
        self._user_facts_reader = user_facts_reader
        self._user_facts_writer = user_facts_writer

    async def handle(self, env: Envelope) -> AgentResult | None:
        reason = env.routing_reason

        if reason == "listener_observation":
            return await self._handle_listener(env)
        if reason in ("mention", "focus"):
            return await self._handle_addressed(env)
        if reason == "direct_dm":
            return await self._handle_dm(env)
        if reason == "manager_delegation":
            if env.payload and env.payload.get("kind") == "reminder_request":
                return await self._handle_reminder(env)
            log.warning(
                "secretary: manager_delegation without reminder_request payload"
            )
            return None

        log.debug("secretary: ignoring routing_reason=%s", reason)
        return None

    def _assemble_system_blocks(self, *, mode: str) -> SystemBlocks:
        """Build a two-segment system prompt. Segment 1 (stable) contains
        persona + mode + profile. Segment 2 (facts) contains the user_facts
        prompt block. A Secretary fact write busts only Segment 2 on the
        next call; Segment 1 stays warm."""
        mode_section = {
            "listener": self._persona.listener_mode,
            "addressed": self._persona.group_addressed_mode,
            "dm": self._persona.dm_mode,
            "reminder": self._persona.reminder_mode,
        }[mode]

        stable_parts = [self._persona.core, "", mode_section]
        if self._user_profile is not None:
            block = self._user_profile.as_prompt_block()
            if block:
                stable_parts.append("")
                stable_parts.append(block)
        stable = "\n".join(stable_parts)

        facts: str | None = None
        if self._user_facts_reader is not None:
            facts_block = self._user_facts_reader.as_prompt_block()
            facts = facts_block if facts_block else None

        return SystemBlocks(stable=stable, facts=facts)

    def _cooldown_key(self, base: str, chat_id: int) -> str:
        return f"{base}_{chat_id}"

    def _cooldown_check_and_update(
        self, chat_id: int, body: str
    ) -> bool:
        """Update the cooldown counters with the new message and return True
        if all three thresholds have been exceeded. Pure code; no LLM call."""
        now = datetime.now(UTC)
        cfg = self._config

        last_at_key = self._cooldown_key("last_reply_at", chat_id)
        msgs_key = self._cooldown_key("msgs_since_reply", chat_id)
        chars_key = self._cooldown_key("chars_since_reply", chat_id)

        last_at_raw = self._memory.get(last_at_key)
        if last_at_raw is None:
            # Never replied → treat as forever ago. t_min is satisfied.
            last_at_elapsed = cfg.t_min_seconds + 1
        else:
            try:
                last_at = datetime.fromisoformat(last_at_raw.replace("Z", "+00:00"))
                last_at_elapsed = int((now - last_at).total_seconds())
            except (ValueError, AttributeError):
                log.warning(
                    "secretary: corrupt last_reply_at for chat=%s value=%r; "
                    "treating as forever-ago",
                    chat_id,
                    last_at_raw,
                )
                last_at_elapsed = cfg.t_min_seconds + 1

        msgs = int(self._memory.get(msgs_key) or 0) + 1
        chars = int(self._memory.get(chars_key) or 0) + weighted_len(body)

        self._memory.set(msgs_key, msgs)
        self._memory.set(chars_key, chars)

        return (
            last_at_elapsed >= cfg.t_min_seconds
            and msgs >= cfg.n_min_messages
            and chars >= cfg.l_min_weighted_chars
        )

    # Path handlers are implemented in later tasks. For now they return None.
    async def _handle_listener(self, env: Envelope) -> AgentResult | None:
        chat_id = env.telegram_chat_id
        if chat_id is None:
            return None
        if not self._cooldown_check_and_update(chat_id, env.body):
            return None
        # Cooldown open → ask the LLM. The actual call happens in Task 11.
        return await self._listener_llm_call(env)

    def _format_transcript(self, envs: list[Envelope]) -> str:
        """Turn a list of envelopes into a speaker-labeled transcript. Lines
        are in chronological order (oldest first). Secretary's own lines are
        labeled 'secretary:' so the model sees its own voice and stays
        consistent. Other agents are labeled '[other-agent: NAME]:' so the
        model knows it is overhearing, not being addressed."""
        lines: list[str] = []
        for e in envs:
            if e.from_kind == "user":
                lines.append(f"user: {e.body}")
            elif e.from_kind == "agent":
                speaker = e.from_agent or "unknown"
                if speaker == "secretary":
                    lines.append(f"secretary: {e.body}")
                else:
                    lines.append(f"[other-agent: {speaker}]: {e.body}")
            else:
                continue
        return "\n".join(lines)

    def _load_transcript(self, chat_id: int) -> str:
        """Group-scoped transcript. Use only from listener_observation /
        @mention paths, never from DM — DMs share a telegram_chat_id
        across every bot the user opens, so DM transcripts must be
        scoped by (chat_id, agent). See ``_load_dm_transcript``."""
        envs = self._messages.recent_for_chat(
            chat_id=chat_id, limit=self._config.transcript_window
        )
        return self._format_transcript(envs)

    def _load_dm_transcript(self, chat_id: int) -> str:
        """DM-scoped transcript. Filters by (chat_id, 'secretary') so that
        Intelligence's and Manager's DM replies don't contaminate
        Secretary's DM context even though they share the same
        telegram_chat_id (the user's user_id)."""
        envs = self._messages.recent_for_dm(
            chat_id=chat_id, agent="secretary", limit=self._config.transcript_window
        )
        return self._format_transcript(envs)

    def _reset_cooldown_after_reply(self, chat_id: int) -> None:
        now_iso = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        self._memory.set(self._cooldown_key("last_reply_at", chat_id), now_iso)
        self._memory.set(self._cooldown_key("msgs_since_reply", chat_id), 0)
        self._memory.set(self._cooldown_key("chars_since_reply", chat_id), 0)

    async def _run_with_tool_loop(
        self,
        *,
        env: Envelope,
        purpose: str,
        mode: str,
        initial_user_text: str,
        max_tokens: int,
    ) -> str | None:
        """Run a bounded two-iteration-deep tool loop. Secretary has exactly
        one tool (remember_about_user). A successful call writes to user_facts
        and feeds the result back to the model, which then produces the final
        reply. If the model emits no tool call, its text is the reply
        directly. When the writer is not wired, the tool list is empty and
        we fall back to a plain ``complete`` call — this preserves behavior
        in tests / setups that don't wire the writer."""
        system = self._assemble_system_blocks(mode=mode)

        # No writer → no tool to expose. Fall back to plain complete so we
        # don't feed an empty tools list to the provider.
        if self._user_facts_writer is None:
            try:
                return await self._llm.complete(
                    system=system,
                    messages=[Msg(role="user", content=initial_user_text)],
                    max_tokens=max_tokens,
                    agent="secretary",
                    purpose=purpose,
                    envelope_id=env.id,
                )
            except LLMProviderError as e:
                log.warning("secretary %s LLM call failed: %s", purpose, e)
                return None

        tools: list[ToolSpec] = [remember_about_user_tool_spec()]

        async def _dispatch(call: ToolCall, _state: object) -> tuple[str, bool]:
            if call.name == "remember_about_user":
                writer = self._user_facts_writer
                if writer is None:  # pragma: no cover — guarded above
                    return ("writer not available", True)
                fact = call.input.get("fact_text") or ""
                topic = call.input.get("topic")
                if not isinstance(fact, str) or not fact.strip():
                    return ("fact_text required", True)
                try:
                    fid = writer.add(fact, topic=topic if isinstance(topic, str) else None)
                except Exception as e:  # noqa: BLE001 — surface any write error to the model
                    log.warning("user_facts write failed: %s", e)
                    return (f"error: {e}", True)
                return (f'{{"ok": true, "fact_id": {fid}}}', False)
            return (f"unknown tool: {call.name}", True)

        try:
            result = await run_agentic_loop(
                llm=self._llm,
                system=system,
                initial_user_text=initial_user_text,
                tools=tools,
                dispatch_tool=_dispatch,
                max_iterations=2,
                max_tokens=max_tokens,
                agent="secretary",
                purpose=purpose,
                envelope_id=env.id,
            )
        except LLMProviderError as e:
            log.warning("secretary tool loop failed: %s", e)
            return None

        if result.errored:
            return None
        return result.final_text

    async def _listener_llm_call(self, env: Envelope) -> AgentResult | None:
        chat_id = env.telegram_chat_id
        assert chat_id is not None
        transcript = self._load_transcript(chat_id)
        user_msg = f"对话记录(最后一条是用户刚发的):\n{transcript}"

        text = await self._run_with_tool_loop(
            env=env,
            purpose="listener",
            mode="listener",
            initial_user_text=user_msg,
            max_tokens=self._config.max_tokens_listener,
        )
        if text is None:
            return None
        if is_skip_sentinel(text, self._config.skip_sentinels):
            log.info("secretary considered, passed (skip sentinel)")
            return None

        self._reset_cooldown_after_reply(chat_id)
        return AgentResult(reply_text=text, delegate_to=None, handoff_text=None)

    async def _handle_addressed(self, env: Envelope) -> AgentResult | None:
        """Group path triggered by @mention or sticky focus. No cooldown.
        Uses the group_addressed_mode persona section."""
        return await self._addressed_llm_call(
            env=env,
            max_tokens=self._config.max_tokens_reply,
            preface="对话记录(你刚被点名):",
        )

    async def _handle_dm(self, env: Envelope) -> AgentResult | None:
        """DM path. Always replies, separate cooldown namespace (per chat_id)."""
        return await self._addressed_llm_call(
            env=env,
            max_tokens=self._config.max_tokens_reply,
            preface="私聊记录:",
        )

    async def _addressed_llm_call(
        self,
        *,
        env: Envelope,
        max_tokens: int,
        preface: str,
    ) -> AgentResult | None:
        chat_id = env.telegram_chat_id
        if chat_id is None:
            transcript = ""
        elif env.source == "telegram_dm":
            # DM path: Telegram reuses one chat_id across all bots this
            # user DMs, so filter by (chat_id, secretary) to avoid
            # pulling Intelligence/Manager DM content into Secretary's
            # prompt. See store.recent_for_dm for details.
            transcript = self._load_dm_transcript(chat_id)
        else:
            transcript = self._load_transcript(chat_id)
        mode = "dm" if env.source == "telegram_dm" else "addressed"
        # Scene context so the model doesn't guess group vs DM. Secretary's
        # tone shifts between the two (flirtier in DM, more reserved in a
        # group with other agents listening), so getting this wrong is a
        # UX bug, not a stylistic quibble.
        if env.source == "telegram_group":
            scene = "当前场景：Telegram 群聊（可能有其他人或其他 agent 看得见你说的话）"
        elif env.source == "telegram_dm":
            scene = "当前场景：Telegram 私聊（只有老公一个人看得见）"
        else:
            scene = f"当前场景：{env.source}"
        user_msg = f"{scene}\n\n{preface}\n{transcript}"

        text = await self._run_with_tool_loop(
            env=env,
            purpose="reply",
            mode=mode,
            initial_user_text=user_msg,
            max_tokens=max_tokens,
        )
        if text is None:
            return None

        # Reset group cooldown when Secretary speaks directly so the listener
        # path doesn't immediately fire again on the next message. Done only
        # for group chats (DMs have separate namespaces per chat_id anyway).
        if chat_id is not None and env.source == "telegram_group":
            self._reset_cooldown_after_reply(chat_id)

        return AgentResult(reply_text=text, delegate_to=None, handoff_text=None)

    async def _handle_reminder(self, env: Envelope) -> AgentResult | None:
        payload = env.payload or {}
        appointment = (payload.get("appointment") or "").strip()
        when = (payload.get("when") or "").strip()
        note = (payload.get("note") or "").strip()

        if env.source == "telegram_group" or env.telegram_chat_id is not None:
            scene_line = "当前场景：Telegram 群聊（可能有其他人或其他 agent 看得见你说的话）"
        else:
            scene_line = "当前场景：Telegram 私聊（只有老公一个人看得见）"
        parts = [scene_line, "", "Manager 让你提醒用户一件事。用你自己的口吻温柔地传达："]
        if appointment:
            parts.append(f"- 事情: {appointment}")
        if when:
            parts.append(f"- 时间: {when}")
        if note:
            parts.append(f"- 备注: {note}")
        parts.append("不要编造任何 Manager 没给你的细节。")
        user_msg = "\n".join(parts)

        text = await self._run_with_tool_loop(
            env=env,
            purpose="reminder",
            mode="reminder",
            initial_user_text=user_msg,
            max_tokens=self._config.max_tokens_reply,
        )
        if text is None:
            return None

        return AgentResult(reply_text=text, delegate_to=None, handoff_text=None)
