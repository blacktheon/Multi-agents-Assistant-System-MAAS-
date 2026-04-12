"""Store trust boundary tests.

The single most important thing these tests verify is memory isolation:
an AgentMemory scoped to one agent CANNOT read rows written by another.
"""

from __future__ import annotations

from project0.store import Store


def test_store_init_schema_is_idempotent(store: Store) -> None:
    store.init_schema()  # second call must not raise
    store.init_schema()


def test_agent_memory_set_and_get(store: Store) -> None:
    mem = store.agent_memory("manager")
    mem.set("last_seen", {"ts": "2026-04-13T12:00:00Z"})
    assert mem.get("last_seen") == {"ts": "2026-04-13T12:00:00Z"}


def test_agent_memory_get_missing_returns_none(store: Store) -> None:
    mem = store.agent_memory("manager")
    assert mem.get("nothing") is None


def test_agent_memory_delete(store: Store) -> None:
    mem = store.agent_memory("manager")
    mem.set("x", 1)
    mem.delete("x")
    assert mem.get("x") is None


def test_agent_memory_isolation_between_agents(store: Store) -> None:
    """Manager's AgentMemory must not see Intelligence's rows, and vice versa."""
    manager_mem = store.agent_memory("manager")
    intel_mem = store.agent_memory("intelligence")

    manager_mem.set("secret", "manager-only")
    intel_mem.set("secret", "intelligence-only")

    assert manager_mem.get("secret") == "manager-only"
    assert intel_mem.get("secret") == "intelligence-only"


def test_agent_memory_has_no_cross_agent_api(store: Store) -> None:
    """Regression guard: AgentMemory must not expose any method that accepts
    an agent name parameter, because that would let a caller pivot scope."""
    mem = store.agent_memory("manager")
    public_methods = [m for m in dir(mem) if not m.startswith("_")]
    # Whitelisted public surface. Anything else on AgentMemory is a red flag.
    assert set(public_methods) == {"get", "set", "delete"}


# --- Blackboard tests ---


def test_blackboard_append_returns_id(store: Store) -> None:
    bb = store.blackboard()
    row_id = bb.append("manager", "task_summary", {"task": "demo"})
    assert isinstance(row_id, int)
    assert row_id >= 1


def test_blackboard_recent_returns_appended(store: Store) -> None:
    bb = store.blackboard()
    bb.append("manager", "task_summary", {"n": 1})
    bb.append("intelligence", "handoff_note", {"n": 2})
    rows = bb.recent(limit=10)
    assert len(rows) == 2
    # Most recent first.
    assert rows[0]["payload"]["n"] == 2
    assert rows[0]["author_agent"] == "intelligence"
    assert rows[1]["author_agent"] == "manager"


def test_blackboard_recent_filters_by_kind(store: Store) -> None:
    bb = store.blackboard()
    bb.append("manager", "task_summary", {"n": 1})
    bb.append("manager", "handoff_note", {"n": 2})
    rows = bb.recent(kind="task_summary")
    assert len(rows) == 1
    assert rows[0]["kind"] == "task_summary"
