"""Per-agent cache layout invariant (Task 16 / spec §B.3).

1. For each agent, building the system prompt twice with only a messages[]
   difference must produce byte-identical Segment-1 bytes AND byte-identical
   Segment-2 bytes.

2. When facts are present, exactly two cache_control markers must appear in
   the rendered SDK system param. When absent, exactly one.

3. No volatile content (transcript, scene, current-turn body) may appear
   inside either segment.

Fixtures are local to this test file to keep conftest lean.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.agents.intelligence import (
    Intelligence,
    IntelligenceConfig,
    IntelligencePersona,
)
from project0.agents.manager import Manager, ManagerConfig, ManagerPersona
from project0.agents.secretary import Secretary, SecretaryConfig, SecretaryPersona
from project0.intelligence.fake_source import FakeTwitterSource
from project0.llm.provider import FakeProvider, _render_system_param
from project0.store import (
    AgentMemory,
    LLMUsageStore,
    MessagesStore,
    Store,
    UserFactsReader,
    UserFactsWriter,
    UserProfile,
)

VOLATILE_MARKERS = ["@secretary", "明天", "transcript"]


def _profile() -> UserProfile:
    return UserProfile(address_as="老公", birthday="1995-03-14")


def _seed_fact(store: Store) -> None:
    UserFactsWriter("secretary", store.conn).add("用户喜欢吃寿司", topic="饮食")


def _make_store(seed: bool) -> Store:
    store = Store(":memory:")
    store.init_schema()
    if seed:
        _seed_fact(store)
    return store


def _make_secretary(store: Store) -> Secretary:
    usage = LLMUsageStore(store.conn)
    llm = FakeProvider(responses=["ok"], usage_store=usage)
    persona = SecretaryPersona(
        core="秘书 core persona block",
        listener_mode="listener mode section",
        group_addressed_mode="addressed mode section",
        dm_mode="dm mode section",
        reminder_mode="reminder mode section",
    )
    cfg = SecretaryConfig(
        t_min_seconds=0,
        n_min_messages=0,
        l_min_weighted_chars=0,
        transcript_window=20,
        model="claude-sonnet-4-6",
        max_tokens_reply=800,
        max_tokens_listener=600,
        skip_sentinels=["[skip]"],
    )
    return Secretary(
        llm=llm,
        memory=AgentMemory(store.conn, "secretary"),
        messages_store=MessagesStore(store.conn),
        persona=persona,
        config=cfg,
        user_profile=_profile(),
        user_facts_reader=UserFactsReader("secretary", store.conn),
        user_facts_writer=UserFactsWriter("secretary", store.conn),
    )


def _make_manager(store: Store) -> Manager:
    usage = LLMUsageStore(store.conn)
    llm = FakeProvider(responses=["ok"], usage_store=usage)
    persona = ManagerPersona(
        core="经理 core persona block",
        dm_mode="dm mode section",
        group_addressed_mode="group addressed mode section",
        pulse_mode="pulse mode section",
        tool_use_guide="tool use guide section",
    )
    cfg = ManagerConfig(
        model="claude-sonnet-4-6",
        max_tokens_reply=800,
        max_tool_iterations=6,
        transcript_window=10,
    )
    return Manager(
        llm=llm,
        calendar=None,
        memory=AgentMemory(store.conn, "manager"),
        messages_store=MessagesStore(store.conn),
        persona=persona,
        config=cfg,
        user_tz=ZoneInfo("Asia/Shanghai"),
        clock=lambda: datetime(2026, 4, 16, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        user_profile=_profile(),
        user_facts_reader=UserFactsReader("manager", store.conn),
        user_facts_writer=None,
    )


def _seed_report(data_dir: Path) -> None:
    reports_dir = data_dir / "intelligence" / "reports"
    reports_dir.mkdir(parents=True)
    report = {
        "date": "2026-04-16",
        "generated_at": "2026-04-16T08:00:00+08:00",
        "user_tz": "Asia/Shanghai",
        "watchlist_snapshot": [],
        "news_items": [
            {
                "id": "r01",
                "headline": "headline one",
                "importance": "high",
                "importance_reason": "r",
                "topics": ["ai"],
                "summary": "s",
                "source_tweets": [],
            }
        ],
        "suggested_accounts": [],
        "stats": {
            "tweets_fetched": 0,
            "handles_attempted": 0,
            "handles_succeeded": 0,
            "items_generated": 1,
            "errors": [],
        },
    }
    (reports_dir / "2026-04-16.json").write_text(json.dumps(report), encoding="utf-8")


def _make_intelligence(store: Store, data_dir: Path) -> Intelligence:
    _seed_report(data_dir)
    return Intelligence(
        llm_summarizer=FakeProvider(responses=[]),
        llm_qa=FakeProvider(tool_responses=[]),
        twitter=FakeTwitterSource(timelines={}),
        messages_store=None,
        persona=IntelligencePersona(
            core="情报 core persona",
            dm_mode="dm mode",
            group_addressed_mode="group mode",
            delegated_mode="delegated mode",
            tool_use_guide="tool use guide",
        ),
        config=IntelligenceConfig(
            summarizer_model="claude-opus-4-6",
            summarizer_max_tokens=16384,
            summarizer_thinking_budget=None,
            qa_model="claude-sonnet-4-6",
            qa_max_tokens=2048,
            transcript_window=10,
            max_tool_iterations=6,
            timeline_since_hours=24,
            max_tweets_per_handle=50,
        ),
        watchlist=[],
        reports_dir=data_dir / "intelligence" / "reports",
        user_tz=ZoneInfo("Asia/Shanghai"),
        public_base_url="http://test.local:8080",
        user_profile=_profile(),
        user_facts_reader=UserFactsReader("intelligence", store.conn),
    )


@pytest.fixture()
def secretary_with_facts() -> Secretary:
    return _make_secretary(_make_store(seed=True))


@pytest.fixture()
def manager_with_facts() -> Manager:
    return _make_manager(_make_store(seed=True))


@pytest.fixture()
def intelligence_with_facts(tmp_path: Path) -> Intelligence:
    return _make_intelligence(_make_store(seed=True), tmp_path)


@pytest.fixture()
def secretary_empty_facts() -> Secretary:
    return _make_secretary(_make_store(seed=False))


def _assert_no_volatile_markers(text: str) -> None:
    for m in VOLATILE_MARKERS:
        assert m not in text, f"volatile marker {m!r} leaked into cached segment"


def _count_cache_markers(rendered: list[dict]) -> int:
    return sum(1 for b in rendered if "cache_control" in b)


def test_secretary_cache_layout_invariant(secretary_with_facts: Secretary) -> None:
    sec = secretary_with_facts
    sb1 = sec._assemble_system_blocks(mode="addressed")
    sb2 = sec._assemble_system_blocks(mode="addressed")
    assert sb1.stable == sb2.stable
    assert sb1.facts == sb2.facts
    assert sb1.facts  # facts present

    rendered = _render_system_param(sb1)
    assert _count_cache_markers(rendered) == 2

    _assert_no_volatile_markers(sb1.stable)
    assert sb1.facts is not None
    _assert_no_volatile_markers(sb1.facts)


def test_manager_cache_layout_invariant(manager_with_facts: Manager) -> None:
    mgr = manager_with_facts
    sb1 = mgr._assemble_system_blocks(mgr._persona.dm_mode)
    sb2 = mgr._assemble_system_blocks(mgr._persona.dm_mode)
    assert sb1.stable == sb2.stable
    assert sb1.facts == sb2.facts
    assert sb1.facts

    rendered = _render_system_param(sb1)
    assert _count_cache_markers(rendered) == 2

    _assert_no_volatile_markers(sb1.stable)
    assert sb1.facts is not None
    _assert_no_volatile_markers(sb1.facts)


def test_intelligence_cache_layout_invariant(
    intelligence_with_facts: Intelligence,
) -> None:
    intel = intelligence_with_facts
    sb1 = intel._assemble_system_blocks(mode_section="dm mode")
    sb2 = intel._assemble_system_blocks(mode_section="dm mode")
    assert sb1.stable == sb2.stable
    assert sb1.facts == sb2.facts
    assert sb1.facts

    rendered = _render_system_param(sb1)
    assert _count_cache_markers(rendered) == 2

    _assert_no_volatile_markers(sb1.stable)
    assert sb1.facts is not None
    _assert_no_volatile_markers(sb1.facts)


def test_single_segment_when_no_facts(secretary_empty_facts: Secretary) -> None:
    sb = secretary_empty_facts._assemble_system_blocks(mode="listener")
    assert sb.facts in (None, "")
    rendered = _render_system_param(sb)
    assert _count_cache_markers(rendered) == 1
