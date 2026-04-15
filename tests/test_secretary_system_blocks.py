"""Task 10: Secretary uses SystemBlocks with two cache breakpoints.

Verifies _assemble_system_blocks splits persona+mode+profile (stable) from
user_facts (facts) so that Secretary fact writes only bust Segment 2.
"""
from __future__ import annotations

import pytest

from project0.agents.secretary import (
    Secretary,
    SecretaryConfig,
    SecretaryPersona,
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


def _make_secretary(
    *,
    user_profile: UserProfile | None = None,
    user_facts_reader: UserFactsReader | None = None,
    user_facts_writer: UserFactsWriter | None = None,
) -> Secretary:
    store = Store(":memory:")
    store.init_schema()
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
        user_profile=user_profile,
        user_facts_reader=(
            user_facts_reader
            if user_facts_reader is not None
            else UserFactsReader("secretary", store.conn)
        ),
        user_facts_writer=(
            user_facts_writer
            if user_facts_writer is not None
            else UserFactsWriter("secretary", store.conn)
        ),
    ), store


@pytest.fixture()
def store_with_facts() -> dict:
    sec, store = _make_secretary()
    # Seed a user fact via the writer.
    writer = UserFactsWriter("secretary", store.conn)
    writer.add("用户喜欢吃寿司", topic="饮食")
    return {"secretary": sec, "store": store}


@pytest.fixture()
def store_without_facts() -> dict:
    sec, store = _make_secretary()
    return {"secretary": sec, "store": store}


@pytest.fixture()
def store_with_profile() -> dict:
    profile = UserProfile(
        address_as="老公",
        birthday="1995-03-14",
        fixed_preferences=["不吃香菜"],
    )
    sec, store = _make_secretary(user_profile=profile)
    return {"secretary": sec, "store": store}


def _assemble_listener(sec: Secretary) -> SystemBlocks:
    """Call the internal helper that builds the SystemBlocks for listener mode."""
    return sec._assemble_system_blocks(mode="listener")


def test_listener_blocks_have_two_segments_when_facts_present(store_with_facts) -> None:
    sec = store_with_facts["secretary"]
    sb = _assemble_listener(sec)
    assert isinstance(sb, SystemBlocks)
    assert "persona" in sb.stable.lower() or "秘书" in sb.stable
    assert sb.facts is not None
    assert "寿司" in sb.facts


def test_listener_blocks_facts_empty_when_no_facts(store_without_facts) -> None:
    sec = store_without_facts["secretary"]
    sb = _assemble_listener(sec)
    assert sb.facts in (None, "")


def test_stable_block_contains_profile(store_with_profile) -> None:
    sec = store_with_profile["secretary"]
    sb = _assemble_listener(sec)
    assert "1995-03-14" in sb.stable  # birthday rendered in stable segment


def test_stable_block_does_not_contain_facts(store_with_facts) -> None:
    sec = store_with_facts["secretary"]
    sb = _assemble_listener(sec)
    assert "寿司" not in sb.stable
