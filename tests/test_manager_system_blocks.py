"""Task 11: Manager uses SystemBlocks with two cache breakpoints.

Mirrors test_secretary_system_blocks: seed a store with a user_fact,
build Manager, call _assemble_system_blocks, assert the stable segment
contains persona+profile but NOT facts, and the facts segment contains
the seeded fact text.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from project0.agents.manager import (
    Manager,
    ManagerConfig,
    ManagerPersona,
)
from project0.llm.provider import FakeProvider, SystemBlocks
from project0.store import (
    AgentMemory,
    LLMUsageStore,
    MessagesStore,
    Store,
    UserFactsReader,
    UserFactsWriter,
    UserProfile,
)


def _persona() -> ManagerPersona:
    return ManagerPersona(
        core="经理 core persona block",
        dm_mode="dm mode section",
        group_addressed_mode="group addressed mode section",
        pulse_mode="pulse mode section",
        tool_use_guide="tool use guide section",
    )


def _config() -> ManagerConfig:
    return ManagerConfig(
        model="claude-sonnet-4-6",
        max_tokens_reply=800,
        max_tool_iterations=6,
        transcript_window=20,
    )


def _make_manager(
    *,
    user_profile: UserProfile | None = None,
) -> tuple[Manager, Store]:
    store = Store(":memory:")
    store.init_schema()
    usage = LLMUsageStore(store.conn)
    llm = FakeProvider(responses=["ok"], usage_store=usage)
    mgr = Manager(
        llm=llm,
        calendar=None,
        memory=AgentMemory(store.conn, "manager"),
        messages_store=MessagesStore(store.conn),
        persona=_persona(),
        config=_config(),
        user_tz=ZoneInfo("Asia/Shanghai"),
        clock=lambda: datetime(2026, 4, 16, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        user_profile=user_profile,
        user_facts_reader=UserFactsReader("manager", store.conn),
        user_facts_writer=None,
    )
    return mgr, store


@pytest.fixture()
def mgr_with_facts() -> dict:
    mgr, store = _make_manager()
    writer = UserFactsWriter("secretary", store.conn)
    writer.add("用户喜欢吃寿司", topic="饮食")
    return {"manager": mgr, "store": store}


@pytest.fixture()
def mgr_without_facts() -> dict:
    mgr, store = _make_manager()
    return {"manager": mgr, "store": store}


@pytest.fixture()
def mgr_with_profile() -> dict:
    profile = UserProfile(
        address_as="老公",
        birthday="1995-03-14",
        fixed_preferences=["不吃香菜"],
    )
    mgr, store = _make_manager(user_profile=profile)
    writer = UserFactsWriter("secretary", store.conn)
    writer.add("用户喜欢吃寿司", topic="饮食")
    return {"manager": mgr, "store": store}


def test_blocks_are_system_blocks_instance(mgr_with_facts) -> None:
    mgr = mgr_with_facts["manager"]
    sb = mgr._assemble_system_blocks(mgr._persona.dm_mode)
    assert isinstance(sb, SystemBlocks)


def test_stable_contains_persona_core(mgr_with_facts) -> None:
    mgr = mgr_with_facts["manager"]
    sb = mgr._assemble_system_blocks(mgr._persona.dm_mode)
    assert "经理 core persona block" in sb.stable
    assert "dm mode section" in sb.stable
    assert "tool use guide section" in sb.stable


def test_facts_segment_has_fact_text(mgr_with_facts) -> None:
    mgr = mgr_with_facts["manager"]
    sb = mgr._assemble_system_blocks(mgr._persona.dm_mode)
    assert sb.facts is not None
    assert "寿司" in sb.facts


def test_stable_does_not_contain_facts(mgr_with_facts) -> None:
    mgr = mgr_with_facts["manager"]
    sb = mgr._assemble_system_blocks(mgr._persona.dm_mode)
    assert "寿司" not in sb.stable


def test_facts_empty_when_no_facts(mgr_without_facts) -> None:
    mgr = mgr_without_facts["manager"]
    sb = mgr._assemble_system_blocks(mgr._persona.dm_mode)
    assert sb.facts in (None, "")


def test_stable_contains_profile_block(mgr_with_profile) -> None:
    mgr = mgr_with_profile["manager"]
    sb = mgr._assemble_system_blocks(mgr._persona.dm_mode)
    assert "1995-03-14" in sb.stable
    assert "老公" in sb.stable
    # Profile in stable, facts in facts segment.
    assert "寿司" not in sb.stable
    assert sb.facts is not None and "寿司" in sb.facts
