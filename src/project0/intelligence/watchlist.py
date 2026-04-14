"""Watchlist loader. Reads the [[watch]] array from an intelligence TOML
file and returns a list of frozen WatchEntry records.

Static for 6d. Mutable dynamic-follow tooling lives in 6h."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WatchEntry:
    handle: str
    tags: tuple[str, ...]
    notes: str


def load_watchlist(path: Path) -> list[WatchEntry]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw_entries = data.get("watch") or []
    if not isinstance(raw_entries, list):
        raise RuntimeError(f"{path}: [[watch]] must be an array of tables")

    seen: set[str] = set()
    out: list[WatchEntry] = []
    for i, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise RuntimeError(f"{path}: [[watch]] entry {i} is not a table")
        handle_raw = raw.get("handle")
        if not isinstance(handle_raw, str) or not handle_raw.strip():
            raise RuntimeError(
                f"{path}: [[watch]] entry {i}: missing or empty 'handle'"
            )
        handle = handle_raw.strip().lstrip("@").lower()
        if handle in seen:
            raise RuntimeError(f"{path}: duplicate handle {handle!r}")
        seen.add(handle)

        tags_raw: Any = raw.get("tags") or []
        if not isinstance(tags_raw, list) or not all(isinstance(t, str) for t in tags_raw):
            raise RuntimeError(
                f"{path}: [[watch]] entry {i}: 'tags' must be a list of strings"
            )

        notes_raw = raw.get("notes") or ""
        if not isinstance(notes_raw, str):
            raise RuntimeError(
                f"{path}: [[watch]] entry {i}: 'notes' must be a string"
            )

        out.append(WatchEntry(handle=handle, tags=tuple(tags_raw), notes=notes_raw))

    return out
