"""Intelligence agent — LLM-backed briefing specialist.

6d scope: Twitter/X ingestion, one-Opus-call daily report generation,
shallow Q&A over the latest report via a Sonnet tool-use loop.

Persona has five canonical Chinese sections (mirrors Manager). The
Intelligence class takes TWO LLM providers: one Opus (summarizer) and
one Sonnet (Q&A). The class itself is completed in Tasks 10–12; this
file currently holds loaders and dataclasses only."""
from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


# --- legacy stub (removed in Task 13 once Intelligence is fully wired) ------
# Kept so registry.py still imports until register_intelligence exists.

from project0.envelope import AgentResult, Envelope  # noqa: E402


async def intelligence_stub(env: Envelope) -> AgentResult:  # pragma: no cover
    return AgentResult(
        reply_text=f"[intelligence-stub] acknowledged: {env.body}",
        delegate_to=None,
        handoff_text=None,
    )
