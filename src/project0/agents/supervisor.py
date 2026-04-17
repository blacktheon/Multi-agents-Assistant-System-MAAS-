"""Supervisor agent (叶霏) — pulse-scheduled reviewer and DM companion.

叶霏 reads the stored conversation history of Manager, Intelligence, and
Learning (never Secretary), scores each on a four-dimension rubric, writes
a short critique with 0-3 recommendations per review, and exposes the
results through a new /reviews page in the control panel. In DM mode she
also converses with the user about past reviews.

See docs/superpowers/specs/2026-04-17-supervisor-agent-design.md.
"""

from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from project0.envelope import AgentResult, Envelope

if TYPE_CHECKING:
    from project0.llm.provider import LLMProvider
    from project0.store import (
        AgentMemory,
        MessagesStore,
        SupervisorReviewsStore,
        UserFactsReader,
        UserProfile,
    )

log = logging.getLogger(__name__)


# --- persona -----------------------------------------------------------------

@dataclass(frozen=True)
class SupervisorPersona:
    core: str
    dm_mode: str
    pulse_mode: str
    tool_use_guide: str


_PERSONA_SECTIONS = {
    "core":           "# 叶霏 — 角色设定",
    "dm_mode":        "# 模式：私聊",
    "pulse_mode":     "# 模式：定时脉冲",
    "tool_use_guide": "# 模式：工具使用守则",
}


def _normalize_header(h: str) -> str:
    return "".join(h.split()).replace(":", "：")


_CANONICAL_HEADERS_NORMALIZED = {
    _normalize_header(v): v for v in _PERSONA_SECTIONS.values()
}


def load_supervisor_persona(path: Path) -> SupervisorPersona:
    """Parse prompts/supervisor.md into its four sections."""
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

    return SupervisorPersona(
        core=sections["core"],
        dm_mode=sections["dm_mode"],
        pulse_mode=sections["pulse_mode"],
        tool_use_guide=sections["tool_use_guide"],
    )


# --- config ------------------------------------------------------------------

@dataclass(frozen=True)
class SupervisorConfig:
    model: str
    max_tokens_reply: int
    max_tool_iterations: int
    transcript_window: int
    quiet_threshold_seconds: int
    max_wait_seconds: int
    per_tick_limit: int


def load_supervisor_config(path: Path) -> SupervisorConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    def _require(section: str, key: str) -> Any:
        try:
            return data[section][key]
        except KeyError as e:
            raise RuntimeError(
                f"missing config key {section}.{key} in {path}"
            ) from e

    return SupervisorConfig(
        model=str(_require("llm", "model")),
        max_tokens_reply=int(_require("llm", "max_tokens_reply")),
        max_tool_iterations=int(_require("llm", "max_tool_iterations")),
        transcript_window=int(_require("context", "transcript_window")),
        quiet_threshold_seconds=int(_require("review", "quiet_threshold_seconds")),
        max_wait_seconds=int(_require("review", "max_wait_seconds")),
        per_tick_limit=int(_require("review", "per_tick_limit")),
    )


# Rubric weights applied to the four dimensions to compute score_overall.
# Hard-coded for v1.0 — tuning is a v1.1 concern.
RUBRIC_WEIGHTS: dict[str, float] = {
    "helpfulness": 0.35,
    "correctness": 0.30,
    "tone":        0.15,
    "efficiency":  0.20,
}


REVIEWED_AGENTS: tuple[str, ...] = ("manager", "intelligence", "learning")


# --- idle gate --------------------------------------------------------------

@dataclass(frozen=True)
class GateResult:
    is_quiet: bool
    should_run: bool
    forced_after_cap: bool = False


class IdleGate:
    """Checks whether Supervisor should run a review right now.

    Quiet = no user-originated envelope in the last ``quiet_threshold_seconds``.
    If not quiet, the gate records ``idle_gate:pending_since_ts`` on the
    agent's private memory so a subsequent ``review_retry`` pulse can pick up
    where we left off and so the max-wait cap is enforced across process
    restarts.

    Activity scope: only ``from_kind='user'`` envelopes count. Agent-to-agent
    internal chatter, listener observations, and pulses do not count as
    activity (see spec §3.2).
    """

    def __init__(
        self,
        *,
        messages_store: "MessagesStore",
        memory: "AgentMemory",
        quiet_threshold_seconds: int,
        max_wait_seconds: int,
    ) -> None:
        self._messages = messages_store
        self._memory = memory
        self._quiet = quiet_threshold_seconds
        self._max_wait = max_wait_seconds

    def check(self, *, now: datetime) -> GateResult:
        cutoff = now - timedelta(seconds=self._quiet)
        cutoff_iso = cutoff.astimezone(UTC).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        is_quiet = not self._messages.has_user_activity_since(cutoff_iso)

        pending = self._memory.get("idle_gate:pending_since_ts")

        if is_quiet:
            return GateResult(is_quiet=True, should_run=True)

        if pending is None:
            self._memory.set(
                "idle_gate:pending_since_ts",
                now.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
            return GateResult(is_quiet=False, should_run=False)

        try:
            pending_dt = datetime.fromisoformat(str(pending).replace("Z", "+00:00"))
        except ValueError:
            # Corrupt memory value — reset and treat as "just started".
            self._memory.set(
                "idle_gate:pending_since_ts",
                now.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
            return GateResult(is_quiet=False, should_run=False)

        elapsed = (now - pending_dt).total_seconds()
        if elapsed >= self._max_wait:
            return GateResult(is_quiet=False, should_run=True, forced_after_cap=True)
        return GateResult(is_quiet=False, should_run=False)

    def clear_pending(self) -> None:
        self._memory.delete("idle_gate:pending_since_ts")

    def has_pending(self) -> bool:
        return self._memory.get("idle_gate:pending_since_ts") is not None
