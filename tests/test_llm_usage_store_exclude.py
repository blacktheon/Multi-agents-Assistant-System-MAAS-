"""LLMUsageStore rollup methods must support excluding specific models
from results. Used by the /usage dashboard to hide local-LLM rows while
still preserving them in the audit trail."""

from __future__ import annotations

from pathlib import Path

from project0.store import LLMUsageStore, Store

LOCAL_MODEL = "qwen2.5-72b-awq-8k"
ANTHROPIC_MODEL = "claude-sonnet-4-6"


def _seed_two_rows(usage: LLMUsageStore) -> None:
    usage.record(
        agent="secretary", model=ANTHROPIC_MODEL,
        input_tokens=100, cache_creation_input_tokens=0,
        cache_read_input_tokens=0, output_tokens=50,
        envelope_id=None, purpose="reply",
    )
    usage.record(
        agent="secretary", model=LOCAL_MODEL,
        input_tokens=200, cache_creation_input_tokens=0,
        cache_read_input_tokens=0, output_tokens=80,
        envelope_id=None, purpose="reply",
    )


def _usage_store(tmp_path: Path) -> LLMUsageStore:
    store = Store(str(tmp_path / "u.db"))
    store.init_schema()
    return LLMUsageStore(store.conn)


def test_daily_rollup_without_exclude_sums_all(tmp_path: Path) -> None:
    usage = _usage_store(tmp_path)
    _seed_two_rows(usage)
    rows = usage.daily_rollup(days=7)
    assert len(rows) == 1
    r = rows[0]
    assert r["in_tok"] == 300  # 100 + 200
    assert r["out_tok"] == 130  # 50 + 80
    assert r["calls"] == 2


def test_daily_rollup_with_exclude_drops_matching_rows(tmp_path: Path) -> None:
    usage = _usage_store(tmp_path)
    _seed_two_rows(usage)
    rows = usage.daily_rollup(days=7, exclude_models={LOCAL_MODEL})
    assert len(rows) == 1
    r = rows[0]
    assert r["in_tok"] == 100
    assert r["out_tok"] == 50
    assert r["calls"] == 1


def test_agent_rollup_without_exclude_sums_all(tmp_path: Path) -> None:
    usage = _usage_store(tmp_path)
    _seed_two_rows(usage)
    rows = usage.agent_rollup(days=7)
    assert len(rows) == 1
    r = rows[0]
    assert r["agent"] == "secretary"
    assert r["calls"] == 2
    assert r["out_total"] == 130


def test_agent_rollup_with_exclude_drops_matching_rows(tmp_path: Path) -> None:
    usage = _usage_store(tmp_path)
    _seed_two_rows(usage)
    rows = usage.agent_rollup(days=7, exclude_models={LOCAL_MODEL})
    assert len(rows) == 1
    r = rows[0]
    assert r["calls"] == 1
    assert r["out_total"] == 50


def test_recent_without_exclude_returns_all(tmp_path: Path) -> None:
    usage = _usage_store(tmp_path)
    _seed_two_rows(usage)
    rows = usage.recent(limit=10)
    assert len(rows) == 2
    assert {r["model"] for r in rows} == {LOCAL_MODEL, ANTHROPIC_MODEL}


def test_recent_with_exclude_drops_matching_rows(tmp_path: Path) -> None:
    usage = _usage_store(tmp_path)
    _seed_two_rows(usage)
    rows = usage.recent(limit=10, exclude_models={LOCAL_MODEL})
    assert len(rows) == 1
    assert rows[0]["model"] == ANTHROPIC_MODEL


def test_exclude_multiple_models(tmp_path: Path) -> None:
    """Exclude takes an iterable; all listed models are dropped."""
    usage = _usage_store(tmp_path)
    usage.record(
        agent="secretary", model="model-a",
        input_tokens=10, cache_creation_input_tokens=0,
        cache_read_input_tokens=0, output_tokens=5,
        envelope_id=None, purpose="reply",
    )
    usage.record(
        agent="secretary", model="model-b",
        input_tokens=20, cache_creation_input_tokens=0,
        cache_read_input_tokens=0, output_tokens=7,
        envelope_id=None, purpose="reply",
    )
    usage.record(
        agent="secretary", model="model-c",
        input_tokens=30, cache_creation_input_tokens=0,
        cache_read_input_tokens=0, output_tokens=11,
        envelope_id=None, purpose="reply",
    )
    rows = usage.recent(limit=10, exclude_models={"model-a", "model-b"})
    assert len(rows) == 1
    assert rows[0]["model"] == "model-c"


def test_exclude_none_is_same_as_absent(tmp_path: Path) -> None:
    usage = _usage_store(tmp_path)
    _seed_two_rows(usage)
    assert usage.daily_rollup(days=7) == usage.daily_rollup(days=7, exclude_models=None)
    assert usage.recent(limit=10) == usage.recent(limit=10, exclude_models=None)


def test_exclude_empty_set_keeps_all(tmp_path: Path) -> None:
    # Edge case: an empty exclude set must not generate a syntactically
    # broken SQL clause (e.g., `model NOT IN ()`).
    usage = _usage_store(tmp_path)
    _seed_two_rows(usage)
    rows = usage.recent(limit=10, exclude_models=set())
    assert len(rows) == 2
