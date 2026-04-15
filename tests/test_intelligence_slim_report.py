"""Task 12: Intelligence Q&A system prompt contains only the headline
index form of the latest report, not the full JSON.

The goal is to shrink the cached stable segment so that a 12-item daily
report adds ~12 short lines to the system prompt instead of several KB
of summaries + source tweets. Per-item deep dives are fetched on demand
via the get_report_item tool (Task 13)."""
from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

from project0.agents.intelligence import (
    Intelligence,
    IntelligenceConfig,
    IntelligencePersona,
)
from project0.intelligence.fake_source import FakeTwitterSource
from project0.llm.provider import FakeProvider
from project0.store import UserFactsReader, UserProfile, Store


def _persona() -> IntelligencePersona:
    return IntelligencePersona(
        core="情报 core persona",
        dm_mode="dm mode",
        group_addressed_mode="group mode",
        delegated_mode="delegated mode",
        tool_use_guide="tool use guide",
    )


def _config() -> IntelligenceConfig:
    return IntelligenceConfig(
        summarizer_model="claude-opus-4-6",
        summarizer_max_tokens=16384,
        summarizer_thinking_budget=None,
        qa_model="claude-sonnet-4-6",
        qa_max_tokens=2048,
        transcript_window=10,
        max_tool_iterations=6,
        timeline_since_hours=24,
        max_tweets_per_handle=50,
    )


def _seed_report(data_dir: Path, n_items: int = 12) -> Path:
    reports_dir = data_dir / "intelligence" / "reports"
    reports_dir.mkdir(parents=True)
    report = {
        "date": "2026-04-16",
        "generated_at": "2026-04-16T08:00:00+08:00",
        "user_tz": "Asia/Shanghai",
        "watchlist_snapshot": [],
        "news_items": [
            {
                "id": f"r{i:02d}",
                "headline": f"headline number {i} short",
                "importance": "high",
                "importance_reason": "r",
                "topics": ["ai"],
                "summary": "a long summary that must NOT appear in the system prompt" * 20,
                "source_tweets": [
                    {
                        "handle": "sama",
                        "url": "https://x.com/sama/status/1",
                        "text": "t",
                        "posted_at": "2026-04-16T03:00:00Z",
                    }
                ],
            }
            for i in range(1, n_items + 1)
        ],
        "suggested_accounts": [],
        "stats": {
            "tweets_fetched": 0,
            "handles_attempted": 0,
            "handles_succeeded": 0,
            "items_generated": n_items,
            "errors": [],
        },
    }
    (reports_dir / "2026-04-16.json").write_text(
        json.dumps(report), encoding="utf-8"
    )
    return reports_dir


def _make_intelligence(
    data_dir: Path,
    *,
    user_profile: UserProfile | None = None,
    with_facts_reader: bool = False,
) -> Intelligence:
    reports_dir = data_dir / "intelligence" / "reports"
    facts_reader: UserFactsReader | None = None
    if with_facts_reader:
        store = Store(":memory:")
        store.init_schema()
        facts_reader = UserFactsReader("intelligence", store.conn)
    return Intelligence(
        llm_summarizer=FakeProvider(responses=[]),
        llm_qa=FakeProvider(tool_responses=[]),
        twitter=FakeTwitterSource(timelines={}),
        messages_store=None,
        persona=_persona(),
        config=_config(),
        watchlist=[],
        reports_dir=reports_dir,
        user_tz=ZoneInfo("Asia/Shanghai"),
        public_base_url="http://test.local:8080",
        user_profile=user_profile,
        user_facts_reader=facts_reader,
    )


def test_qa_system_prompt_is_headline_only(tmp_path: Path) -> None:
    _seed_report(tmp_path)
    intel = _make_intelligence(tmp_path)
    sb = intel._assemble_system_blocks(mode_section="dm mode")
    assert "[r01]" in sb.stable
    assert "headline number 1 short" in sb.stable
    # Critically: the long summary text must NOT be inlined.
    assert "a long summary that must NOT appear" not in sb.stable
    # 12 [r..] lines.
    lines = [ln for ln in sb.stable.splitlines() if ln.startswith("[r")]
    assert len(lines) == 12


def test_qa_system_prompt_includes_persona_and_mode(tmp_path: Path) -> None:
    _seed_report(tmp_path)
    intel = _make_intelligence(tmp_path)
    sb = intel._assemble_system_blocks(mode_section="dm mode")
    assert "情报 core persona" in sb.stable
    assert "dm mode" in sb.stable
    assert "tool use guide" in sb.stable


def test_qa_system_prompt_no_report_when_dir_empty(tmp_path: Path) -> None:
    (tmp_path / "intelligence" / "reports").mkdir(parents=True)
    intel = _make_intelligence(tmp_path)
    sb = intel._assemble_system_blocks(mode_section="dm mode")
    assert "今天的日报索引" not in sb.stable


def test_qa_system_prompt_has_profile_in_stable(tmp_path: Path) -> None:
    _seed_report(tmp_path)
    profile = UserProfile(address_as="老公", birthday="1995-03-14")
    intel = _make_intelligence(tmp_path, user_profile=profile)
    sb = intel._assemble_system_blocks(mode_section="dm mode")
    assert "1995-03-14" in sb.stable
