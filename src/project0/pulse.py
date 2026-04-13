"""Pulse primitive: scheduled wake-up envelopes for agents.

A pulse is a generic, domain-agnostic trigger. Each agent's TOML config
file may declare one or more ``[[pulse]]`` entries; the orchestrator
runs one scheduler task per entry, and each tick dispatches an Envelope
with ``source='pulse'`` and ``routing_reason='pulse'`` to the named
agent. The payload dict is pass-through — the orchestrator does not
interpret it. Domain logic (e.g. 'is there a calendar event soon')
lives entirely inside the target agent.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from project0.envelope import Envelope

if TYPE_CHECKING:
    from project0.orchestrator import Orchestrator

log = logging.getLogger(__name__)

_MIN_EVERY_SECONDS = 10


@dataclass(frozen=True)
class PulseEntry:
    name: str
    every_seconds: int
    chat_id: int | None
    payload: dict[str, Any] = field(default_factory=dict)


def load_pulse_entries(toml_path: Path) -> list[PulseEntry]:
    """Parse ``[[pulse]]`` entries from the given TOML file.

    Missing ``[[pulse]]`` array → empty list (valid).
    ``chat_id_env`` missing from os.environ → RuntimeError.
    ``every_seconds < 10`` → RuntimeError.
    Duplicate ``name`` → RuntimeError.
    """
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    raw_entries = data.get("pulse", [])
    if not isinstance(raw_entries, list):
        raise RuntimeError(f"{toml_path}: [[pulse]] must be an array of tables")

    entries: list[PulseEntry] = []
    seen: set[str] = set()
    for idx, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise RuntimeError(f"{toml_path}: pulse entry #{idx} is not a table")
        try:
            name = str(raw["name"])
            every = int(raw["every_seconds"])
        except KeyError as e:
            raise RuntimeError(
                f"{toml_path}: pulse entry #{idx} missing required key {e.args[0]!r}"
            ) from e

        if not name:
            raise RuntimeError(f"{toml_path}: pulse entry #{idx} has empty name")
        if name in seen:
            raise RuntimeError(f"{toml_path}: duplicate pulse name {name!r}")
        seen.add(name)

        if every < _MIN_EVERY_SECONDS:
            raise RuntimeError(
                f"{toml_path}: pulse {name!r} every_seconds={every} is below "
                f"floor {_MIN_EVERY_SECONDS}"
            )

        chat_id: int | None = None
        chat_id_env = raw.get("chat_id_env")
        if chat_id_env is not None:
            env_name = str(chat_id_env)
            raw_val = os.environ.get(env_name)
            if raw_val is None or not raw_val.strip():
                raise RuntimeError(
                    f"{toml_path}: pulse {name!r} references chat_id_env="
                    f"{env_name!r} but the env var is missing or empty"
                )
            try:
                chat_id = int(raw_val.strip())
            except ValueError as e:
                raise RuntimeError(
                    f"{toml_path}: pulse {name!r} env var {env_name}="
                    f"{raw_val!r} is not an integer"
                ) from e

        payload = raw.get("payload", {}) or {}
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"{toml_path}: pulse {name!r} payload must be a table"
            )

        entries.append(
            PulseEntry(
                name=name,
                every_seconds=every,
                chat_id=chat_id,
                payload=dict(payload),
            )
        )

    return entries
