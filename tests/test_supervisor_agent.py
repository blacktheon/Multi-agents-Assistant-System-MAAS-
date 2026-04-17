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
            "system": system,
            "messages": [m.content for m in messages],
        })
        return self.next_response

    async def complete_with_tools(
        self, *, system, messages, tools, max_tokens, agent, purpose,
        envelope_id=None,
    ):
        # Record as a call, then return a plain text response (no tool use).
        if self.calls is None:
            self.calls = []
        self.calls.append({
            "agent": agent, "purpose": purpose,
            "system": system,
            "messages": [m.content if hasattr(m, "content") else str(m) for m in messages],
            "had_tools": tools is not None and len(tools) > 0,
        })
        from project0.llm.tools import ToolUseResult
        return ToolUseResult(kind="text", text=self.next_response, tool_calls=[])


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


# --- Supervisor class / handle() --------------------------------------------

def _pulse_env(kind: str) -> Envelope:
    return Envelope(
        id=None, ts="2026-04-17T09:00:00Z", parent_id=None,
        source="pulse", telegram_chat_id=None, telegram_msg_id=None,
        received_by_bot=None, from_kind="system", from_agent=None,
        to_agent="supervisor", body=f"pulse:{kind}",
        routing_reason="pulse", payload={"pulse_name": kind, "kind": kind},
    )


def test_pulse_review_cycle_runs_when_quiet(tmp_path) -> None:
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    _insert_user_envelope_at(
        store, chat_id=100, body="我今天几点开会?", msg_id=1,
        when=datetime.now(UTC) - timedelta(hours=1),
    )
    late = (datetime.now(UTC) - timedelta(hours=1)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    store.messages().insert(Envelope(
        id=None, ts=late, parent_id=None, source="internal",
        telegram_chat_id=100, telegram_msg_id=None, received_by_bot=None,
        from_kind="agent", from_agent="manager", to_agent="user",
        body="下午两点。",
    ))

    persona = SupervisorPersona(
        core="core", dm_mode="dm", pulse_mode="pulse-mode-text",
        tool_use_guide="tools",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )

    good_response = json.dumps({
        "agent": "manager",
        "envelope_id_from": 1, "envelope_id_to": 2, "envelope_count": 2,
        "score_helpfulness": 80, "score_correctness": 80,
        "score_tone": 80, "score_efficiency": 80,
        "critique_text": "good.",
        "recommendations": [],
    })
    fake_llm = _FakeLLM(next_response=good_response)

    sup = Supervisor(
        llm=fake_llm, store=store, persona=persona, config=cfg,
    )
    asyncio.run(sup.handle(_pulse_env("review_cycle")))

    rs = store.supervisor_reviews()
    latest = rs.latest_for_agent("manager")
    assert latest is not None
    assert latest.score_overall == 80
    cursor = store.agent_memory("supervisor").get("cursor:manager")
    assert cursor == 2


def test_pulse_review_cycle_skips_when_busy(tmp_path) -> None:
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _insert_user_envelope_now(store, chat_id=100, body="still talking", msg_id=1)

    persona = SupervisorPersona(
        core="core", dm_mode="dm", pulse_mode="pulse-mode-text",
        tool_use_guide="tools",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )
    fake_llm = _FakeLLM(next_response="unused")

    sup = Supervisor(
        llm=fake_llm, store=store, persona=persona, config=cfg,
    )
    asyncio.run(sup.handle(_pulse_env("review_cycle")))

    assert store.supervisor_reviews().latest_for_agent("manager") is None
    assert store.agent_memory("supervisor").get("idle_gate:pending_since_ts") is not None


def test_pulse_review_retry_noop_when_no_pending(tmp_path) -> None:
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    persona = SupervisorPersona(
        core="core", dm_mode="dm", pulse_mode="pulse-mode-text",
        tool_use_guide="tools",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )
    fake_llm = _FakeLLM(next_response="should not be called")

    sup = Supervisor(
        llm=fake_llm, store=store, persona=persona, config=cfg,
    )
    result = asyncio.run(sup.handle(_pulse_env("review_retry")))

    assert result is None
    assert fake_llm.calls is None


def test_pulse_review_cycle_skips_empty_slice(tmp_path) -> None:
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    persona = SupervisorPersona(
        core="core", dm_mode="dm", pulse_mode="pulse-mode-text",
        tool_use_guide="tools",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )
    fake_llm = _FakeLLM(next_response="unused")

    sup = Supervisor(
        llm=fake_llm, store=store, persona=persona, config=cfg,
    )
    asyncio.run(sup.handle(_pulse_env("review_cycle")))

    for agent in ("manager", "intelligence", "learning"):
        assert store.supervisor_reviews().latest_for_agent(agent) is None


def test_dm_path_returns_reply_using_dm_persona_section(tmp_path) -> None:
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    persona = SupervisorPersona(
        core="CORE",
        dm_mode="DM_MODE_SECTION",
        pulse_mode="PULSE_MODE_SECTION",
        tool_use_guide="TOOLS",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )
    fake_llm = _FakeLLM(next_response="欧尼酱好呀~")

    sup = Supervisor(
        llm=fake_llm, store=store, persona=persona, config=cfg,
    )

    dm_env = Envelope(
        id=None, ts="2026-04-17T09:00:00Z", parent_id=None,
        source="telegram_dm", telegram_chat_id=42, telegram_msg_id=99,
        received_by_bot="supervisor",
        from_kind="user", from_agent=None, to_agent="supervisor",
        body="最近 manager 表现怎么样?",
        routing_reason="direct_dm",
    )
    result = asyncio.run(sup.handle(dm_env))
    assert result is not None
    assert result.reply_text is not None and "欧尼酱" in result.reply_text

    assert fake_llm.calls is not None
    assert len(fake_llm.calls) == 1
    assert result.delegate_to is None

    # Voice guardrail: DM path must include dm_mode, never pulse_mode.
    call_system = fake_llm.calls[0]["system"]
    assert "DM_MODE_SECTION" in call_system
    assert "PULSE_MODE_SECTION" not in call_system
    assert fake_llm.calls[0]["purpose"] == "chat_reply"
    assert fake_llm.calls[0]["had_tools"] is True


def test_mention_in_group_routes_to_chat_path(tmp_path) -> None:
    """Group @mention must use the chat path, not the None fallback."""
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    persona = SupervisorPersona(
        core="CORE",
        dm_mode="DM_MODE_SECTION",
        pulse_mode="PULSE_MODE_SECTION",
        tool_use_guide="TOOLS",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )
    fake_llm = _FakeLLM(next_response="欧尼酱,我在呢~")

    sup = Supervisor(
        llm=fake_llm, store=store, persona=persona, config=cfg,
    )

    mention_env = Envelope(
        id=None, ts="2026-04-18T02:19:00Z", parent_id=None,
        source="telegram_group", telegram_chat_id=-100, telegram_msg_id=42,
        received_by_bot="supervisor",
        from_kind="user", from_agent=None, to_agent="supervisor",
        body="@MAAS_supervisor_bot 她们什么都不愿意给我呜呜呜",
        mentions=["supervisor"],
        routing_reason="mention",
    )
    result = asyncio.run(sup.handle(mention_env))
    assert result is not None
    assert result.reply_text is not None
    assert "欧尼酱" in result.reply_text
    # Same voice as DM — uses dm_mode section.
    assert fake_llm.calls is not None
    call_system = fake_llm.calls[0]["system"]
    assert "DM_MODE_SECTION" in call_system
    assert "PULSE_MODE_SECTION" not in call_system
    assert fake_llm.calls[0]["purpose"] == "chat_reply"
    assert fake_llm.calls[0]["had_tools"] is True


def test_focus_followup_routes_to_chat_path(tmp_path) -> None:
    """After a mention sets focus to supervisor, follow-up messages arrive
    with routing_reason='focus' — must still reach the chat path, not the
    None fallback."""
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    persona = SupervisorPersona(
        core="CORE",
        dm_mode="DM_MODE_SECTION",
        pulse_mode="PULSE_MODE_SECTION",
        tool_use_guide="TOOLS",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )
    fake_llm = _FakeLLM(next_response="欧尼酱我在听呢~")

    sup = Supervisor(
        llm=fake_llm, store=store, persona=persona, config=cfg,
    )

    focus_env = Envelope(
        id=None, ts="2026-04-18T02:35:00Z", parent_id=None,
        source="telegram_group", telegram_chat_id=-100, telegram_msg_id=50,
        received_by_bot="supervisor",
        from_kind="user", from_agent=None, to_agent="supervisor",
        body="最近manager姐姐表现怎么样？",
        routing_reason="focus",
    )
    result = asyncio.run(sup.handle(focus_env))
    assert result is not None
    assert result.reply_text is not None
    assert "欧尼酱" in result.reply_text
    assert fake_llm.calls is not None
    assert fake_llm.calls[0]["purpose"] == "chat_reply"
    assert fake_llm.calls[0]["had_tools"] is True


def test_pulse_path_uses_pulse_mode_not_dm_mode(tmp_path) -> None:
    """Mirror test: pulse-mode review must include pulse_mode but never dm_mode."""
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    # Seed manager-only envelopes with old activity (so idle gate is quiet).
    _insert_user_envelope_at(
        store, chat_id=100, body="hello manager", msg_id=1,
        when=datetime.now(UTC) - timedelta(hours=1),
    )

    persona = SupervisorPersona(
        core="CORE",
        dm_mode="DM_MODE_SECTION",
        pulse_mode="PULSE_MODE_SECTION",
        tool_use_guide="TOOLS",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )
    good_response = json.dumps({
        "agent": "manager",
        "envelope_id_from": 1, "envelope_id_to": 1, "envelope_count": 1,
        "score_helpfulness": 80, "score_correctness": 80,
        "score_tone": 80, "score_efficiency": 80,
        "critique_text": "x.",
        "recommendations": [],
    })
    fake_llm = _FakeLLM(next_response=good_response)

    sup = Supervisor(
        llm=fake_llm, store=store, persona=persona, config=cfg,
    )
    asyncio.run(sup.handle(_pulse_env("review_cycle")))

    assert fake_llm.calls is not None
    assert len(fake_llm.calls) >= 1
    call_system = fake_llm.calls[0]["system"]
    assert "PULSE_MODE_SECTION" in call_system
    assert "DM_MODE_SECTION" not in call_system
    assert fake_llm.calls[0]["purpose"] == "review"


# --- tool-use chat path tests -----------------------------------------------


def test_run_review_now_tool_bypasses_idle_gate(tmp_path) -> None:
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store
    from project0.llm.tools import ToolCall, ToolUseResult

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    # Seed RECENT user activity — would normally block the idle gate.
    _insert_user_envelope_now(store, chat_id=100, body="hi mgr", msg_id=1)
    # Manager replies in the same chat:
    now_iso = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    store.messages().insert(Envelope(
        id=None, ts=now_iso, parent_id=None, source="internal",
        telegram_chat_id=100, telegram_msg_id=None, received_by_bot=None,
        from_kind="agent", from_agent="manager", to_agent="user",
        body="hi user",
    ))

    persona = SupervisorPersona(
        core="CORE", dm_mode="DM", pulse_mode="PULSE", tool_use_guide="TOOLS",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )

    good_review = json.dumps({
        "agent": "manager",
        "envelope_id_from": 1, "envelope_id_to": 2, "envelope_count": 2,
        "score_helpfulness": 80, "score_correctness": 80,
        "score_tone": 80, "score_efficiency": 80,
        "critique_text": "Manager 回应及时。",
        "recommendations": [],
    })

    # Scripted LLM: first call returns a tool_use for run_review_now(manager);
    # second call returns the engine's review JSON (for ReviewEngine.run_review);
    # third call returns a final text reply summarizing it.
    class _ScriptedLLM:
        def __init__(self):
            self.complete_calls = 0
            self.complete_with_tools_calls = 0

        async def complete(self, *, system, messages, max_tokens, agent, purpose,
                           envelope_id=None, thinking_budget_tokens=None):
            # Called by ReviewEngine
            self.complete_calls += 1
            return good_review

        async def complete_with_tools(self, *, system, messages, tools,
                                      max_tokens, agent, purpose, envelope_id=None):
            self.complete_with_tools_calls += 1
            if self.complete_with_tools_calls == 1:
                # Model wants to run the tool.
                return ToolUseResult(
                    kind="tool_use",
                    text=None,
                    tool_calls=[ToolCall(id="c1", name="run_review_now",
                                         input={"agent": "manager"})],
                )
            # Second round: produce final text reply.
            return ToolUseResult(
                kind="text",
                text="Manager 分 80 分,很稳哦~",
                tool_calls=[],
            )

    llm = _ScriptedLLM()
    sup = Supervisor(llm=llm, store=store, persona=persona, config=cfg)

    dm_env = Envelope(
        id=None, ts="2026-04-18T02:55:00Z", parent_id=None,
        source="telegram_dm", telegram_chat_id=42, telegram_msg_id=99,
        received_by_bot="supervisor",
        from_kind="user", from_agent=None, to_agent="supervisor",
        body="帮我跑一次 Manager 的评价",
        routing_reason="direct_dm",
    )
    result = asyncio.run(sup.handle(dm_env))

    assert result is not None
    assert result.reply_text is not None
    assert "80" in result.reply_text or "稳" in result.reply_text
    # Review actually persisted, despite busy chat (gate was bypassed).
    rs = store.supervisor_reviews()
    assert rs.latest_for_agent("manager") is not None


def test_list_past_reviews_tool_returns_stored_rows(tmp_path) -> None:
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store, SupervisorReviewRow
    from project0.llm.tools import ToolCall, ToolUseResult

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    # Seed a past review so list_past_reviews has something to return.
    store.supervisor_reviews().insert(SupervisorReviewRow(
        id=0, ts="2026-04-17T10:00:00Z", agent="manager",
        envelope_id_from=1, envelope_id_to=5, envelope_count=5,
        score_overall=77,
        score_helpfulness=80, score_correctness=75,
        score_tone=85, score_efficiency=70,
        critique_text="前一次表现不错。",
        recommendations_json="[]",
        trigger="pulse",
    ))

    persona = SupervisorPersona(
        core="CORE", dm_mode="DM", pulse_mode="PULSE", tool_use_guide="TOOLS",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )

    captured_tool_content = {}

    class _CaptureLLM:
        def __init__(self):
            self.n = 0

        async def complete(self, **kw):
            return "unused"

        async def complete_with_tools(self, *, system, messages, tools,
                                      max_tokens, agent, purpose, envelope_id=None):
            self.n += 1
            if self.n == 1:
                return ToolUseResult(
                    kind="tool_use",
                    text=None,
                    tool_calls=[ToolCall(id="c1", name="list_past_reviews",
                                         input={"agent": "manager", "limit": 3})],
                )
            # Capture the ToolResultMsg content before returning final text.
            for m in messages:
                if hasattr(m, "tool_use_id") and getattr(m, "tool_use_id", None) == "c1":
                    captured_tool_content["body"] = m.content
            return ToolUseResult(
                kind="text",
                text="manager 之前一次是 77 分哦",
                tool_calls=[],
            )

    llm = _CaptureLLM()
    sup = Supervisor(llm=llm, store=store, persona=persona, config=cfg)

    dm_env = Envelope(
        id=None, ts="2026-04-18T02:55:00Z", parent_id=None,
        source="telegram_dm", telegram_chat_id=42, telegram_msg_id=99,
        received_by_bot="supervisor",
        from_kind="user", from_agent=None, to_agent="supervisor",
        body="Manager 上次什么分?",
        routing_reason="direct_dm",
    )
    asyncio.run(sup.handle(dm_env))

    assert "body" in captured_tool_content
    body = captured_tool_content["body"]
    assert "77" in body
    assert "manager" in body


def test_run_review_all_skips_empty_and_rejects_secretary(tmp_path) -> None:
    """run_review_all must never invoke envelopes_for_review('secretary')
    and must skip agents whose slice is empty."""
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    # Only manager has envelopes.
    _insert_user_envelope_at(
        store, chat_id=100, body="hi", msg_id=1,
        when=datetime.now(UTC) - timedelta(hours=2),
    )

    persona = SupervisorPersona(
        core="CORE", dm_mode="DM", pulse_mode="PULSE", tool_use_guide="TOOLS",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )

    from project0.llm.tools import ToolCall, ToolUseResult

    captured_tool_content = {}

    good_review = json.dumps({
        "agent": "manager",
        "envelope_id_from": 1, "envelope_id_to": 1, "envelope_count": 1,
        "score_helpfulness": 70, "score_correctness": 70,
        "score_tone": 70, "score_efficiency": 70,
        "critique_text": ".",
        "recommendations": [],
    })

    class _ScriptedLLM:
        def __init__(self):
            self.n = 0

        async def complete(self, **kw):
            return good_review

        async def complete_with_tools(self, *, system, messages, tools,
                                      max_tokens, agent, purpose, envelope_id=None):
            self.n += 1
            if self.n == 1:
                return ToolUseResult(
                    kind="tool_use",
                    text=None,
                    tool_calls=[ToolCall(id="c1", name="run_review_all",
                                         input={})],
                )
            for m in messages:
                if hasattr(m, "tool_use_id") and getattr(m, "tool_use_id", None) == "c1":
                    captured_tool_content["body"] = m.content
            return ToolUseResult(kind="text", text="done", tool_calls=[])

    llm = _ScriptedLLM()
    sup = Supervisor(llm=llm, store=store, persona=persona, config=cfg)

    dm_env = Envelope(
        id=None, ts="2026-04-18T02:55:00Z", parent_id=None,
        source="telegram_dm", telegram_chat_id=42, telegram_msg_id=99,
        received_by_bot="supervisor",
        from_kind="user", from_agent=None, to_agent="supervisor",
        body="全部评一次",
        routing_reason="direct_dm",
    )
    asyncio.run(sup.handle(dm_env))

    body = captured_tool_content["body"]
    # Manager got reviewed:
    assert "manager" in body
    # Intelligence and Learning were skipped (no envelopes):
    assert '"no_new_envelopes"' in body
    # Secretary never appears in the list:
    assert "secretary" not in body


def test_run_review_now_rejects_secretary(tmp_path) -> None:
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store
    from project0.llm.tools import ToolCall, ToolUseResult

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    persona = SupervisorPersona(
        core="CORE", dm_mode="DM", pulse_mode="PULSE", tool_use_guide="TOOLS",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )

    captured = {}

    class _ScriptedLLM:
        def __init__(self):
            self.n = 0

        async def complete(self, **kw):
            return "unused"

        async def complete_with_tools(self, *, system, messages, tools,
                                      max_tokens, agent, purpose, envelope_id=None):
            self.n += 1
            if self.n == 1:
                return ToolUseResult(
                    kind="tool_use",
                    text=None,
                    tool_calls=[ToolCall(id="c1", name="run_review_now",
                                         input={"agent": "secretary"})],
                )
            for m in messages:
                if hasattr(m, "tool_use_id") and getattr(m, "tool_use_id", None) == "c1":
                    captured["content"] = m.content
                    captured["is_error"] = getattr(m, "is_error", None)
            return ToolUseResult(kind="text", text="sorry", tool_calls=[])

    llm = _ScriptedLLM()
    sup = Supervisor(llm=llm, store=store, persona=persona, config=cfg)

    dm_env = Envelope(
        id=None, ts="2026-04-18T02:55:00Z", parent_id=None,
        source="telegram_dm", telegram_chat_id=42, telegram_msg_id=99,
        received_by_bot="supervisor",
        from_kind="user", from_agent=None, to_agent="supervisor",
        body="评一下 secretary 吧",
        routing_reason="direct_dm",
    )
    asyncio.run(sup.handle(dm_env))

    assert captured.get("is_error") is True
    assert "Secretary" in captured["content"]


def test_run_review_now_reports_parse_failure(tmp_path) -> None:
    """If the review LLM returns malformed JSON, run_review_now must
    surface a 'review_failed' message, not a misleading 'no_new_envelopes'."""
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store
    from project0.llm.tools import ToolCall, ToolUseResult

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()

    # Seed manager envelopes so the slice is NOT empty.
    _insert_user_envelope_at(
        store, chat_id=100, body="hello", msg_id=1,
        when=datetime.now(UTC) - timedelta(hours=2),
    )

    persona = SupervisorPersona(
        core="CORE", dm_mode="DM", pulse_mode="PULSE", tool_use_guide="TOOLS",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )

    captured = {}

    class _ScriptedLLM:
        def __init__(self):
            self.n = 0

        async def complete(self, **kw):
            # ReviewEngine will call this and get back garbage.
            return "this is not json at all, it's just prose"

        async def complete_with_tools(self, *, system, messages, tools,
                                      max_tokens, agent, purpose, envelope_id=None):
            self.n += 1
            if self.n == 1:
                return ToolUseResult(
                    kind="tool_use",
                    text=None,
                    tool_calls=[ToolCall(id="c1", name="run_review_now",
                                         input={"agent": "manager"})],
                )
            for m in messages:
                if hasattr(m, "tool_use_id") and getattr(m, "tool_use_id", None) == "c1":
                    captured["content"] = m.content
                    captured["is_error"] = getattr(m, "is_error", None)
            return ToolUseResult(kind="text", text="sorry, failed", tool_calls=[])

    llm = _ScriptedLLM()
    sup = Supervisor(llm=llm, store=store, persona=persona, config=cfg)

    dm_env = Envelope(
        id=None, ts="2026-04-18T03:20:00Z", parent_id=None,
        source="telegram_dm", telegram_chat_id=42, telegram_msg_id=99,
        received_by_bot="supervisor",
        from_kind="user", from_agent=None, to_agent="supervisor",
        body="帮我跑一次 manager 的评价",
        routing_reason="direct_dm",
    )
    asyncio.run(sup.handle(dm_env))

    body = captured["content"]
    assert "review_failed" in body
    assert captured["is_error"] is True


def test_review_parse_tolerates_trailing_prose(tmp_path) -> None:
    """A valid review JSON followed by trailing prose should still parse."""
    from project0.agents.supervisor import ReviewEngine

    # Pretend the model produced valid JSON + a trailing sentence.
    raw = json.dumps({
        "agent": "manager",
        "envelope_id_from": 1,
        "envelope_id_to": 2,
        "envelope_count": 2,
        "score_helpfulness": 80,
        "score_correctness": 80,
        "score_tone": 80,
        "score_efficiency": 80,
        "critique_text": "good.",
        "recommendations": [],
    }, ensure_ascii=False) + "\n\nHope this is useful for you!"

    obj = ReviewEngine._parse_and_validate(raw, agent="manager")
    assert obj is not None
    assert obj["score_helpfulness"] == 80


# --- registry wiring --------------------------------------------------------


# --- placeholder notification tests ------------------------------------------


def test_run_review_now_sends_placeholder(tmp_path) -> None:
    """run_review_now must send a 'please wait' placeholder to the chat
    before starting the review LLM call."""
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store
    from project0.llm.tools import ToolCall, ToolUseResult

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    _insert_user_envelope_at(
        store, chat_id=42, body="hi", msg_id=1,
        when=datetime.now(UTC) - timedelta(hours=2),
    )

    persona = SupervisorPersona(
        core="CORE", dm_mode="DM", pulse_mode="PULSE", tool_use_guide="TOOLS",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )

    class _CaptureSender:
        def __init__(self):
            self.sent = []
        async def send(self, *, agent, chat_id, text):
            self.sent.append({"agent": agent, "chat_id": chat_id, "text": text})

    captured_sender = _CaptureSender()
    good = json.dumps({
        "agent": "manager",
        "envelope_id_from": 1, "envelope_id_to": 1, "envelope_count": 1,
        "score_helpfulness": 70, "score_correctness": 70,
        "score_tone": 70, "score_efficiency": 70,
        "critique_text": "ok", "recommendations": [],
    })

    class _ScriptedLLM:
        def __init__(self):
            self.n = 0
        async def complete(self, **kw):
            return good
        async def complete_with_tools(self, *, system, messages, tools,
                                      max_tokens, agent, purpose, envelope_id=None):
            self.n += 1
            if self.n == 1:
                return ToolUseResult(
                    kind="tool_use", text=None,
                    tool_calls=[ToolCall(id="c1", name="run_review_now",
                                         input={"agent": "manager"})],
                )
            return ToolUseResult(kind="text", text="done", tool_calls=[])

    sup = Supervisor(llm=_ScriptedLLM(), store=store, persona=persona, config=cfg)
    sup.set_sender(captured_sender)

    env = Envelope(
        id=None, ts="2026-04-18T03:55:00Z", parent_id=None,
        source="telegram_group", telegram_chat_id=42, telegram_msg_id=99,
        received_by_bot="supervisor",
        from_kind="user", from_agent=None, to_agent="supervisor",
        body="跑一次 manager 的评价",
        routing_reason="mention",
    )
    asyncio.run(sup.handle(env))

    # At least one placeholder sent from the supervisor bot to chat 42.
    assert len(captured_sender.sent) >= 1
    first = captured_sender.sent[0]
    assert first["agent"] == "supervisor"
    assert first["chat_id"] == 42
    assert "稍等" in first["text"] or "等一下" in first["text"]


def test_notify_without_sender_is_silent(tmp_path) -> None:
    """_notify must no-op when sender hasn't been injected (unit tests,
    tests that skip main.py wiring)."""
    from project0.agents.supervisor import (
        Supervisor, SupervisorConfig, SupervisorPersona,
    )
    from project0.store import Store

    store = Store(str(tmp_path / "store.db"))
    store.init_schema()
    persona = SupervisorPersona(
        core="CORE", dm_mode="DM", pulse_mode="PULSE", tool_use_guide="TOOLS",
    )
    cfg = SupervisorConfig(
        model="fake", max_tokens_reply=1024, max_tool_iterations=6,
        transcript_window=10,
        quiet_threshold_seconds=300, max_wait_seconds=3600, per_tick_limit=200,
    )
    sup = Supervisor(llm=_FakeLLM(next_response="x"), store=store,
                     persona=persona, config=cfg)
    # No set_sender call. _notify must swallow silently.
    asyncio.run(sup._notify(chat_id=42, text="anything"))
    # No exception; nothing further to assert.


def test_register_supervisor_installs_into_correct_registries() -> None:
    from project0.agents.registry import (
        AGENT_REGISTRY, AGENT_SPECS, LISTENER_REGISTRY, PULSE_REGISTRY,
        register_supervisor,
    )

    assert "supervisor" in AGENT_SPECS
    assert AGENT_SPECS["supervisor"].token_env_key == "TELEGRAM_BOT_TOKEN_SUPERVISOR"

    async def _fake_handle(env):
        return None

    register_supervisor(_fake_handle)
    assert "supervisor" in AGENT_REGISTRY
    assert "supervisor" in PULSE_REGISTRY
    assert "supervisor" not in LISTENER_REGISTRY
