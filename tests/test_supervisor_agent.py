"""Tests for the Supervisor agent (叶霏): persona/config loading, idle gate,
cursor advancement, review engine, and handle() routing."""
from __future__ import annotations

from pathlib import Path

import json

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


# --- review engine ----------------------------------------------------------

from dataclasses import dataclass as _dc


@_dc
class _FakeLLM:
    """Minimal stand-in for LLMProvider used only by ReviewEngine tests."""
    next_response: str
    calls: list[dict] | None = None

    async def complete(
        self, *, system, messages, max_tokens, agent, purpose,
        envelope_id=None, thinking_budget_tokens=None,
    ) -> str:
        if self.calls is None:
            self.calls = []
        self.calls.append({
            "agent": agent, "purpose": purpose,
            "messages": [m.content for m in messages],
        })
        return self.next_response


import asyncio


def test_review_engine_happy_path() -> None:
    from project0.agents.supervisor import ReviewEngine
    from project0.envelope import Envelope

    fake_llm_response = json.dumps({
        "agent": "manager",
        "envelope_id_from": 1,
        "envelope_id_to": 10,
        "envelope_count": 2,
        "score_helpfulness": 80,
        "score_correctness": 75,
        "score_tone": 85,
        "score_efficiency": 70,
        "critique_text": "Manager 这一段回应及时,日程查询准确。",
        "recommendations": [
            {"target": "prompt", "summary": "更主动提醒",
             "detail": "可以在确认日程后主动问一句是否需要提醒。"}
        ],
    }, ensure_ascii=False)
    fake_llm = _FakeLLM(next_response=fake_llm_response)

    envs = [
        Envelope(id=1, ts="2026-04-17T09:00:00Z", parent_id=None,
                 source="telegram_group", telegram_chat_id=100, telegram_msg_id=1,
                 received_by_bot=None, from_kind="user", from_agent=None,
                 to_agent="manager", body="我今天几点开会?"),
        Envelope(id=10, ts="2026-04-17T09:00:05Z", parent_id=1,
                 source="internal", telegram_chat_id=100, telegram_msg_id=None,
                 received_by_bot=None, from_kind="agent", from_agent="manager",
                 to_agent="user", body="下午两点。"),
    ]

    engine = ReviewEngine(llm=fake_llm, pulse_mode_section="# 模式:定时脉冲\n...")
    result = asyncio.run(engine.run_review(
        agent="manager", envelopes=envs, trigger="pulse",
    ))
    assert result is not None
    assert result.score_helpfulness == 80
    assert result.envelope_count == 2
    assert result.envelope_id_from == 1
    assert result.envelope_id_to == 10
    assert result.score_overall == 77
    recs = json.loads(result.recommendations_json)
    assert len(recs) == 1
    assert recs[0]["target"] == "prompt"


def test_review_engine_rejects_malformed_json() -> None:
    from project0.agents.supervisor import ReviewEngine
    from project0.envelope import Envelope

    fake_llm = _FakeLLM(next_response="not even json")
    envs = [
        Envelope(id=1, ts="2026-04-17T09:00:00Z", parent_id=None,
                 source="telegram_group", telegram_chat_id=100, telegram_msg_id=1,
                 received_by_bot=None, from_kind="user", from_agent=None,
                 to_agent="manager", body="hi"),
    ]
    engine = ReviewEngine(llm=fake_llm, pulse_mode_section="# 模式:定时脉冲\n...")
    assert asyncio.run(engine.run_review(
        agent="manager", envelopes=envs, trigger="pulse",
    )) is None


def test_review_engine_rejects_out_of_range_scores() -> None:
    from project0.agents.supervisor import ReviewEngine
    from project0.envelope import Envelope

    bad = json.dumps({
        "agent": "manager",
        "envelope_id_from": 1, "envelope_id_to": 1, "envelope_count": 1,
        "score_helpfulness": 101, "score_correctness": 50,
        "score_tone": 50, "score_efficiency": 50,
        "critique_text": "x", "recommendations": [],
    })
    fake_llm = _FakeLLM(next_response=bad)
    envs = [
        Envelope(id=1, ts="2026-04-17T09:00:00Z", parent_id=None,
                 source="telegram_group", telegram_chat_id=100, telegram_msg_id=1,
                 received_by_bot=None, from_kind="user", from_agent=None,
                 to_agent="manager", body="hi"),
    ]
    engine = ReviewEngine(llm=fake_llm, pulse_mode_section="# 模式:定时脉冲\n...")
    assert asyncio.run(engine.run_review(
        agent="manager", envelopes=envs, trigger="pulse",
    )) is None


def test_review_engine_caps_recommendations_at_three() -> None:
    from project0.agents.supervisor import ReviewEngine
    from project0.envelope import Envelope

    too_many = json.dumps({
        "agent": "manager",
        "envelope_id_from": 1, "envelope_id_to": 1, "envelope_count": 1,
        "score_helpfulness": 50, "score_correctness": 50,
        "score_tone": 50, "score_efficiency": 50,
        "critique_text": "x",
        "recommendations": [
            {"target": "prompt", "summary": "a", "detail": "a"},
            {"target": "prompt", "summary": "b", "detail": "b"},
            {"target": "prompt", "summary": "c", "detail": "c"},
            {"target": "prompt", "summary": "d", "detail": "d"},
        ],
    })
    fake_llm = _FakeLLM(next_response=too_many)
    envs = [
        Envelope(id=1, ts="2026-04-17T09:00:00Z", parent_id=None,
                 source="telegram_group", telegram_chat_id=100, telegram_msg_id=1,
                 received_by_bot=None, from_kind="user", from_agent=None,
                 to_agent="manager", body="hi"),
    ]
    engine = ReviewEngine(llm=fake_llm, pulse_mode_section="# 模式:定时脉冲\n...")
    assert asyncio.run(engine.run_review(
        agent="manager", envelopes=envs, trigger="pulse",
    )) is None
