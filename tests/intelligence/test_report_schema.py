"""Report schema + storage tests. Covers parse_json_strict (with code-fence
tolerance), every hard rule from §5.3 of the spec, atomic write safety,
round-trip read, and list_report_dates filename filtering."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from project0.intelligence.report import (
    atomic_write_json,
    list_report_dates,
    parse_json_strict,
    read_report,
    validate_report_dict,
)


def _valid_report() -> dict[str, Any]:
    return {
        "date": "2026-04-15",
        "generated_at": "2026-04-15T08:00:00+08:00",
        "user_tz": "Asia/Shanghai",
        "watchlist_snapshot": ["sama"],
        "news_items": [
            {
                "id": "n1",
                "headline": "h1",
                "summary": "s1",
                "importance": "high",
                "importance_reason": "r1",
                "topics": ["ai"],
                "source_tweets": [
                    {
                        "handle": "sama",
                        "url": "https://x.com/sama/status/1",
                        "text": "hi",
                        "posted_at": "2026-04-15T03:00:00Z",
                    }
                ],
            }
        ],
        "suggested_accounts": [
            {"handle": "researcher1", "reason": "cited by sama", "seen_in_items": ["n1"]}
        ],
        "stats": {
            "tweets_fetched": 10,
            "handles_attempted": 5,
            "handles_succeeded": 5,
            "items_generated": 1,
            "errors": [],
        },
    }


# --- parse_json_strict ------------------------------------------------------

def test_parse_json_strict_plain_json():
    assert parse_json_strict('{"a": 1}') == {"a": 1}


def test_parse_json_strict_with_markdown_fence():
    text = "```json\n{\"a\": 1}\n```"
    assert parse_json_strict(text) == {"a": 1}


def test_parse_json_strict_with_leading_whitespace():
    assert parse_json_strict("\n   {\"a\": 1}   \n") == {"a": 1}


def test_parse_json_strict_rejects_nonjson():
    with pytest.raises(ValueError, match="JSON"):
        parse_json_strict("not json at all")


def test_parse_json_strict_rejects_nondict_top_level():
    with pytest.raises(ValueError, match="top-level"):
        parse_json_strict("[1, 2, 3]")


# --- validate_report_dict ---------------------------------------------------

def test_valid_report_passes_validation():
    validate_report_dict(_valid_report())  # no exception


def test_missing_top_level_key_raises():
    r = _valid_report()
    del r["news_items"]
    with pytest.raises(ValueError, match="news_items"):
        validate_report_dict(r)


def test_bad_date_format_raises():
    r = _valid_report()
    r["date"] = "Apr 15, 2026"
    with pytest.raises(ValueError, match="date"):
        validate_report_dict(r)


def test_invalid_importance_raises():
    r = _valid_report()
    r["news_items"][0]["importance"] = "critical"
    with pytest.raises(ValueError, match="importance"):
        validate_report_dict(r)


def test_empty_source_tweets_raises():
    r = _valid_report()
    r["news_items"][0]["source_tweets"] = []
    with pytest.raises(ValueError, match="source_tweets"):
        validate_report_dict(r)


def test_duplicate_news_item_id_raises():
    r = _valid_report()
    r["news_items"].append({**r["news_items"][0], "id": "n1"})
    with pytest.raises(ValueError, match="duplicate"):
        validate_report_dict(r)


def test_dangling_seen_in_items_raises():
    r = _valid_report()
    r["suggested_accounts"][0]["seen_in_items"] = ["n99"]
    with pytest.raises(ValueError, match="seen_in_items"):
        validate_report_dict(r)


def test_handles_succeeded_gt_attempted_raises():
    r = _valid_report()
    r["stats"]["handles_succeeded"] = 10
    r["stats"]["handles_attempted"] = 5
    with pytest.raises(ValueError, match="handles_succeeded"):
        validate_report_dict(r)


# --- atomic_write_json + read_report ---------------------------------------

def test_atomic_write_and_read_round_trip(tmp_path: Path):
    p = tmp_path / "2026-04-15.json"
    atomic_write_json(p, _valid_report())
    assert p.exists()
    loaded = read_report(p)
    assert loaded["date"] == "2026-04-15"
    # tmp file should not exist.
    assert not (tmp_path / "2026-04-15.json.tmp").exists()


def test_atomic_write_overwrites_existing(tmp_path: Path):
    p = tmp_path / "2026-04-15.json"
    atomic_write_json(p, {"old": "data"})
    atomic_write_json(p, _valid_report())
    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded["date"] == "2026-04-15"
    assert "old" not in loaded


def test_read_report_validates(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"not": "a report"}), encoding="utf-8")
    with pytest.raises(ValueError):
        read_report(p)


# --- list_report_dates -----------------------------------------------------

def test_list_report_dates_sorted_descending(tmp_path: Path):
    for d in ["2026-04-13", "2026-04-15", "2026-04-14"]:
        (tmp_path / f"{d}.json").write_text("{}", encoding="utf-8")
    dates = list_report_dates(tmp_path)
    assert dates == [date(2026, 4, 15), date(2026, 4, 14), date(2026, 4, 13)]


def test_list_report_dates_ignores_non_matching_files(tmp_path: Path):
    (tmp_path / "2026-04-15.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "backup.json.bak").write_text("x", encoding="utf-8")
    (tmp_path / "2026-04-15.json.tmp").write_text("x", encoding="utf-8")
    dates = list_report_dates(tmp_path)
    assert dates == [date(2026, 4, 15)]


def test_list_report_dates_empty_when_dir_missing(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    assert list_report_dates(missing) == []
