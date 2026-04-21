"""File-path resolution for TOML and persona edit routes.

Editable files are discovered at runtime by scanning ``prompts/`` for
``*.md`` (personas) and ``*.toml`` (configs). Name-level validation
prevents path traversal: names must match ``[a-z0-9_]+``, contain no
``/`` or ``..``, and the resolved file must exist inside ``prompts/``.
Dropping a new file into ``prompts/`` is enough to make it editable —
no code change required.
"""

from __future__ import annotations

import re
from pathlib import Path

_NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")


def _validate_name(name: str) -> None:
    if not name or not _NAME_PATTERN.match(name):
        raise ValueError(f"invalid name: {name!r}")


def list_persona_files(*, project_root: Path) -> list[str]:
    """Return sorted basenames (without extension) of every ``prompts/*.md``.

    Only files directly inside ``prompts/`` whose basename matches the
    name pattern are returned; anything else (subdirectories, hidden
    files, non-conforming names) is silently skipped.
    """
    return _list_files(project_root=project_root, suffix=".md")


def list_toml_files(*, project_root: Path) -> list[str]:
    """Return sorted basenames (without extension) of every ``prompts/*.toml``."""
    return _list_files(project_root=project_root, suffix=".toml")


def _list_files(*, project_root: Path, suffix: str) -> list[str]:
    prompts_dir = project_root / "prompts"
    if not prompts_dir.is_dir():
        return []
    out: list[str] = []
    for p in prompts_dir.iterdir():
        if not p.is_file():
            continue
        if p.suffix != suffix:
            continue
        stem = p.stem
        if not _NAME_PATTERN.match(stem):
            continue
        out.append(stem)
    return sorted(out)


def toml_path(name: str, *, project_root: Path) -> Path:
    _validate_name(name)
    return project_root / "prompts" / f"{name}.toml"


def persona_path(name: str, *, project_root: Path) -> Path:
    _validate_name(name)
    return project_root / "prompts" / f"{name}.md"
