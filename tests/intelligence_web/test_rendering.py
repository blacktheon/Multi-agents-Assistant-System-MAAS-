from datetime import date
from zoneinfo import ZoneInfo

from project0.intelligence_web.rendering import (
    build_report_context,
    format_time,
    groupby_month,
    sort_by_importance,
)

TZ = ZoneInfo("Asia/Shanghai")


def _report(items: list[dict]) -> dict:
    return {
        "date": "2026-04-15",
        "generated_at": "2026-04-15T08:03:22+08:00",
        "user_tz": "Asia/Shanghai",
        "watchlist_snapshot": ["openai"],
        "news_items": items,
        "suggested_accounts": [],
        "stats": {
            "tweets_fetched": 100,
            "handles_attempted": 1,
            "handles_succeeded": 1,
            "items_generated": len(items),
            "errors": [],
        },
    }


def test_sort_by_importance_orders_high_medium_low() -> None:
    items = [
        {"id": "a", "importance": "low"},
        {"id": "b", "importance": "high"},
        {"id": "c", "importance": "medium"},
        {"id": "d", "importance": "high"},
    ]
    out = sort_by_importance(items)
    assert [i["id"] for i in out] == ["b", "d", "c", "a"]


def test_sort_by_importance_is_stable_within_bucket() -> None:
    items = [
        {"id": "x", "importance": "medium"},
        {"id": "y", "importance": "medium"},
        {"id": "z", "importance": "medium"},
    ]
    assert [i["id"] for i in sort_by_importance(items)] == ["x", "y", "z"]


def test_format_time_includes_hours_ago_relative() -> None:
    out = format_time("2026-04-15T08:03:22+08:00", user_tz=TZ, now=None)
    assert "08:03" in out


def test_groupby_month_groups_descending_dates() -> None:
    dates = [date(2026, 4, 15), date(2026, 4, 1), date(2026, 3, 20)]
    grouped = groupby_month(dates)
    assert [month for month, _ in grouped] == ["2026-04", "2026-03"]
    assert grouped[0][1] == [date(2026, 4, 15), date(2026, 4, 1)]
    assert grouped[1][1] == [date(2026, 3, 20)]


def test_build_context_sets_current_and_feedback_fields() -> None:
    report = _report([{"id": "n1", "importance": "high"}])
    ctx = build_report_context(
        report_dict=report,
        feedback_state={"n1": 1},
        all_dates=[date(2026, 4, 15)],
        current=date(2026, 4, 15),
        public_base_url="http://test.local",
    )
    assert ctx["current_date"] == "2026-04-15"
    assert ctx["feedback"] == {"n1": 1}
    assert ctx["public_base_url"] == "http://test.local"
    assert ctx["prev_href"] is None
    assert ctx["next_href"] is None


def test_build_context_prev_next_hrefs_middle_of_list() -> None:
    # all_dates sorted descending (newest first): 17, 16, 15
    # current = 16 → prev (older) = 15, next (newer) = 17
    ctx = build_report_context(
        report_dict=_report([]),
        feedback_state={},
        all_dates=[date(2026, 4, 17), date(2026, 4, 16), date(2026, 4, 15)],
        current=date(2026, 4, 16),
        public_base_url="http://test.local",
    )
    assert ctx["prev_href"] == "/reports/2026-04-15"
    assert ctx["next_href"] == "/reports/2026-04-17"


def test_build_context_prev_none_for_oldest_date() -> None:
    ctx = build_report_context(
        report_dict=_report([]),
        feedback_state={},
        all_dates=[date(2026, 4, 17), date(2026, 4, 16), date(2026, 4, 15)],
        current=date(2026, 4, 15),
        public_base_url="http://test.local",
    )
    assert ctx["prev_href"] is None
    assert ctx["next_href"] == "/reports/2026-04-16"


def test_build_context_next_none_for_newest_date() -> None:
    ctx = build_report_context(
        report_dict=_report([]),
        feedback_state={},
        all_dates=[date(2026, 4, 17), date(2026, 4, 16)],
        current=date(2026, 4, 17),
        public_base_url="http://test.local",
    )
    assert ctx["next_href"] is None
    assert ctx["prev_href"] == "/reports/2026-04-16"


def test_build_context_all_dates_are_iso_strings() -> None:
    ctx = build_report_context(
        report_dict=_report([]),
        feedback_state={},
        all_dates=[date(2026, 4, 17), date(2026, 4, 16)],
        current=date(2026, 4, 17),
        public_base_url="http://test.local",
    )
    assert ctx["all_dates"] == ["2026-04-17", "2026-04-16"]
