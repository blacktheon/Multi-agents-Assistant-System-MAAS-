"""Manager agent — loader functions for persona and config.

The real Manager class lands in later tasks (11–13). This module already
exports ``load_manager_persona`` and ``load_manager_config`` so the
composition-root wiring in main.py can import them incrementally.

For now it still exports ``manager_stub`` so ``agents/registry.py`` keeps
importing cleanly; Task 13 removes the stub once Manager is fully wired.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project0.envelope import AgentResult, Envelope


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


# --- placeholder stub (removed in Task 14) -----------------------------------

async def manager_stub(env: Envelope) -> AgentResult:
    """Legacy stub kept only so agents/registry.py imports cleanly until
    Task 14 swaps in the real Manager class. Behavior is the original
    hardcoded rule: delegate to Intelligence if 'news' in body, else echo."""
    if "news" in env.body.lower():
        return AgentResult(
            reply_text=None,
            delegate_to="intelligence",
            handoff_text="→ forwarding to @intelligence",
        )
    return AgentResult(
        reply_text=f"[manager-stub] acknowledged: {env.body}",
        delegate_to=None,
        handoff_text=None,
    )
