from __future__ import annotations

import pytest

from project0.store import Store, UserFactsReader, UserFactsWriter


@pytest.fixture
def store() -> Store:
    s = Store(":memory:")
    s.init_schema()
    return s


# --- Trust boundary ---

def test_writer_rejects_manager(store: Store) -> None:
    with pytest.raises(PermissionError) as e:
        UserFactsWriter("manager", store.conn)
    assert "manager" in str(e.value)


def test_writer_rejects_intelligence(store: Store) -> None:
    with pytest.raises(PermissionError):
        UserFactsWriter("intelligence", store.conn)


def test_writer_accepts_secretary(store: Store) -> None:
    w = UserFactsWriter("secretary", store.conn)
    assert w is not None


# --- CRUD ---

def test_add_and_read(store: Store) -> None:
    w = UserFactsWriter("secretary", store.conn)
    row_id = w.add("生日是3月14日", topic="personal")
    assert row_id > 0
    r = UserFactsReader("manager", store.conn)
    facts = r.active()
    assert len(facts) == 1
    assert facts[0].fact_text == "生日是3月14日"
    assert facts[0].topic == "personal"
    assert facts[0].author_agent == "secretary"
    assert facts[0].is_active is True


def test_deactivate(store: Store) -> None:
    w = UserFactsWriter("secretary", store.conn)
    fid = w.add("likes 寿司", topic="food")
    w.deactivate(fid)
    r = UserFactsReader("manager", store.conn)
    assert r.active() == []
    assert len(r.all_including_inactive()) == 1


def test_caller_cannot_spoof_author(store: Store) -> None:
    w = UserFactsWriter("secretary", store.conn)
    fid = w.add("test", topic=None)
    row = store.conn.execute(
        "SELECT author_agent FROM user_facts WHERE id=?", (fid,)
    ).fetchone()
    assert row[0] == "secretary"


def test_as_prompt_block_empty_when_no_facts(store: Store) -> None:
    r = UserFactsReader("manager", store.conn)
    assert r.as_prompt_block() == ""


def test_as_prompt_block_renders_active_only(store: Store) -> None:
    w = UserFactsWriter("secretary", store.conn)
    w.add("fact A", topic="x")
    fid_b = w.add("fact B", topic=None)
    w.deactivate(fid_b)
    r = UserFactsReader("manager", store.conn)
    block = r.as_prompt_block()
    assert "fact A" in block
    assert "fact B" not in block


def test_as_prompt_block_respects_token_cap(store: Store) -> None:
    w = UserFactsWriter("secretary", store.conn)
    # Insert 100 facts, each ~30 chars ~= ~30 tokens in Chinese
    for i in range(100):
        w.add(f"fact number {i} " + "测试" * 5, topic="bulk")
    r = UserFactsReader("manager", store.conn)
    # Rough cap: assume ~4 chars per token → 600 tok ≈ 2400 chars rendered
    block = r.as_prompt_block(max_tokens=600)
    assert len(block) <= 2400 + 200  # generous slack for framing text
    assert len(block) > 0
