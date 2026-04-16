"""File-path resolution for TOML and persona edit routes.

All editable TOML and persona files use a three-name allowlist. Any URL
parameter outside this list is a 404 at the route layer; this module
centralizes the allowlist so tests and routes never drift apart.
"""

from __future__ import annotations

from pathlib import Path

ALLOWED_AGENT_NAMES: tuple[str, ...] = ("manager", "secretary", "intelligence", "learning")


def toml_path(name: str, *, project_root: Path) -> Path:
    if name not in ALLOWED_AGENT_NAMES:
        raise ValueError(f"unknown agent name: {name!r}")
    return project_root / "prompts" / f"{name}.toml"


def persona_path(name: str, *, project_root: Path) -> Path:
    if name not in ALLOWED_AGENT_NAMES:
        raise ValueError(f"unknown agent name: {name!r}")
    return project_root / "prompts" / f"{name}.md"
