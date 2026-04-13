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
from pathlib import Path
from typing import Any

from project0.envelope import AgentResult, Envelope
from project0.llm.provider import LLMProvider, LLMProviderError, Msg
from project0.store import AgentMemory, MessagesStore

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
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._messages = messages_store
        self._persona = persona
        self._config = config

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

    def _cooldown_key(self, base: str, chat_id: int) -> str:
        return f"{base}_{chat_id}"

    def _cooldown_check_and_update(
        self, chat_id: int, body: str
    ) -> bool:
        """Update the cooldown counters with the new message and return True
        if all three thresholds have been exceeded. Pure code; no LLM call."""
        from datetime import UTC, datetime

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

    async def _listener_llm_call(self, env: Envelope) -> AgentResult | None:
        # Placeholder; implemented in Task 11. For now fire the LLM so
        # Task 10's test can observe a call count.
        try:
            _ = await self._llm.complete(
                system=self._persona.core + "\n\n" + self._persona.listener_mode,
                messages=[Msg(role="user", content=env.body)],
                max_tokens=self._config.max_tokens_listener,
            )
        except LLMProviderError as e:
            log.warning("secretary listener LLM call failed: %s", e)
        return None

    async def _handle_addressed(self, env: Envelope) -> AgentResult | None:
        return None

    async def _handle_dm(self, env: Envelope) -> AgentResult | None:
        return None

    async def _handle_reminder(self, env: Envelope) -> AgentResult | None:
        return None
