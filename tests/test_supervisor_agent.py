"""Tests for the Supervisor agent (叶霏): persona/config loading, idle gate,
cursor advancement, review engine, and handle() routing."""
from __future__ import annotations

from pathlib import Path

import pytest


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def test_load_persona_has_all_sections() -> None:
    from project0.agents.supervisor import load_supervisor_persona
    persona = load_supervisor_persona(PROMPTS_DIR / "supervisor.md")
    assert "叶霏" in persona.core
    assert "角色设定" in persona.core
    assert "私聊" in persona.dm_mode
    assert "欧尼酱" in persona.dm_mode
    assert "脉冲" in persona.pulse_mode
    assert "工具" in persona.tool_use_guide


def test_load_persona_raises_on_missing_section(tmp_path: Path) -> None:
    from project0.agents.supervisor import load_supervisor_persona
    md = tmp_path / "bad.md"
    md.write_text("# 叶霏 — 角色设定\njust core\n", encoding="utf-8")
    with pytest.raises(ValueError, match="模式：私聊"):
        load_supervisor_persona(md)


def test_load_config_parses_all_fields() -> None:
    from project0.agents.supervisor import load_supervisor_config
    cfg = load_supervisor_config(PROMPTS_DIR / "supervisor.toml")
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.max_tokens_reply == 1024
    assert cfg.max_tool_iterations == 6
    assert cfg.transcript_window == 10
    assert cfg.quiet_threshold_seconds == 300
    assert cfg.max_wait_seconds == 3600
    assert cfg.per_tick_limit == 200


def test_load_config_raises_on_missing_key(tmp_path: Path) -> None:
    from project0.agents.supervisor import load_supervisor_config
    toml_path = tmp_path / "partial.toml"
    toml_path.write_text(
        """
[llm]
model = "test"
max_tokens_reply = 100
max_tool_iterations = 3

[context]
transcript_window = 5
""",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="review.quiet_threshold_seconds"):
        load_supervisor_config(toml_path)


# --- idle gate + cursor helpers ---------------------------------------------

import sqlite3
from datetime import UTC, datetime, timedelta

from project0.envelope import Envelope


def _insert_user_envelope_now(store, chat_id: int, body: str, msg_id: int) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    env = Envelope(
        id=None, ts=now, parent_id=None, source="telegram_group",
        telegram_chat_id=chat_id, telegram_msg_id=msg_id,
        received_by_bot=None, from_kind="user", from_agent=None,
        to_agent="manager", body=body,
    )
    store.messages().insert(env)


def _insert_user_envelope_at(store, chat_id: int, body: str, msg_id: int, when: datetime) -> None:
    ts = when.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    env = Envelope(
        id=None, ts=ts, parent_id=None, source="telegram_group",
        telegram_chat_id=chat_id, telegram_msg_id=msg_id,
        received_by_bot=None, from_kind="user", from_agent=None,
        to_agent="manager", body=body,
    )
    store.messages().insert(env)


def test_idle_gate_quiet_when_no_recent_user_activity(tmp_path) -> None:
    from project0.agents.supervisor import IdleGate
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    memory = store.agent_memory("supervisor")

    gate = IdleGate(
        messages_store=store.messages(),
        memory=memory,
        quiet_threshold_seconds=300,
        max_wait_seconds=3600,
    )
    result = gate.check(now=datetime.now(UTC))
    assert result.is_quiet is True
    assert result.should_run is True
    assert memory.get("idle_gate:pending_since_ts") is None


def test_idle_gate_busy_sets_pending_and_returns_early(tmp_path) -> None:
    from project0.agents.supervisor import IdleGate
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    memory = store.agent_memory("supervisor")

    _insert_user_envelope_now(store, chat_id=100, body="hello", msg_id=1)

    gate = IdleGate(
        messages_store=store.messages(),
        memory=memory,
        quiet_threshold_seconds=300,
        max_wait_seconds=3600,
    )
    result = gate.check(now=datetime.now(UTC))
    assert result.is_quiet is False
    assert result.should_run is False
    assert memory.get("idle_gate:pending_since_ts") is not None


def test_idle_gate_forces_run_after_cap(tmp_path) -> None:
    from project0.agents.supervisor import IdleGate
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    memory = store.agent_memory("supervisor")

    past = (datetime.now(UTC) - timedelta(minutes=61)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    memory.set("idle_gate:pending_since_ts", past)

    _insert_user_envelope_now(store, chat_id=100, body="still busy", msg_id=1)

    gate = IdleGate(
        messages_store=store.messages(),
        memory=memory,
        quiet_threshold_seconds=300,
        max_wait_seconds=3600,
    )
    result = gate.check(now=datetime.now(UTC))
    assert result.should_run is True
    assert result.forced_after_cap is True


def test_idle_gate_clears_pending_on_quiet_run(tmp_path) -> None:
    from project0.agents.supervisor import IdleGate
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    memory = store.agent_memory("supervisor")

    memory.set("idle_gate:pending_since_ts", "2026-04-17T08:00:00Z")

    gate = IdleGate(
        messages_store=store.messages(),
        memory=memory,
        quiet_threshold_seconds=300,
        max_wait_seconds=3600,
    )
    result = gate.check(now=datetime.now(UTC))
    assert result.should_run is True
    gate.clear_pending()
    assert memory.get("idle_gate:pending_since_ts") is None
