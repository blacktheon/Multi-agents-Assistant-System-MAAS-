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

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
