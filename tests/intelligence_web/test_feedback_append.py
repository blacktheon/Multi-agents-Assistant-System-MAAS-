import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.intelligence_web.feedback import FeedbackEvent, append_thumbs


TZ = ZoneInfo("Asia/Shanghai")


def _make_event(item_id: str = "n1", score: int = 1) -> FeedbackEvent:
    return FeedbackEvent.thumbs(
        report_date="2026-04-15",
        item_id=item_id,
        score=score,  # type: ignore[arg-type]
        tz=TZ,
    )


def test_writes_one_line_per_event(tmp_path: Path) -> None:
    fb_dir = tmp_path / "feedback"
    for i in range(3):
        append_thumbs(_make_event(item_id=f"n{i}"), fb_dir)
    files = list(fb_dir.glob("*.jsonl"))
    assert len(files) == 1
    lines = [line for line in files[0].read_text().splitlines() if line]
    assert len(lines) == 3


def test_uses_monthly_filename(tmp_path: Path) -> None:
    fb_dir = tmp_path / "feedback"
    evt = FeedbackEvent(
        ts=datetime(2026, 4, 15, 12, 0, tzinfo=TZ),
        type="thumbs",
        report_date="2026-04-15",
        item_id="n1",
        score=1,
    )
    append_thumbs(evt, fb_dir)
    assert (fb_dir / "2026-04.jsonl").exists()


def test_creates_feedback_dir_if_missing(tmp_path: Path) -> None:
    fb_dir = tmp_path / "does" / "not" / "exist"
    assert not fb_dir.exists()
    append_thumbs(_make_event(), fb_dir)
    assert fb_dir.exists()


def test_appends_to_existing_file(tmp_path: Path) -> None:
    fb_dir = tmp_path / "feedback"
    append_thumbs(_make_event("n1"), fb_dir)
    append_thumbs(_make_event("n2"), fb_dir)
    append_thumbs(_make_event("n3"), fb_dir)
    files = list(fb_dir.glob("*.jsonl"))
    assert len(files) == 1
    lines = [line for line in files[0].read_text().splitlines() if line]
    assert len(lines) == 3


def test_line_is_valid_json(tmp_path: Path) -> None:
    fb_dir = tmp_path / "feedback"
    append_thumbs(_make_event(item_id="n7", score=-1), fb_dir)
    files = list(fb_dir.glob("*.jsonl"))
    content = files[0].read_text().strip()
    parsed = json.loads(content)
    assert parsed["type"] == "thumbs"
    assert parsed["report_date"] == "2026-04-15"
    assert parsed["item_id"] == "n7"
    assert parsed["score"] == -1
    assert "ts" in parsed


def test_unicode_preserved(tmp_path: Path) -> None:
    fb_dir = tmp_path / "feedback"
    append_thumbs(_make_event(item_id="条目一"), fb_dir)
    files = list(fb_dir.glob("*.jsonl"))
    content = files[0].read_text(encoding="utf-8").strip()
    assert "条目一" in content


def test_event_has_server_side_timestamp() -> None:
    evt = FeedbackEvent.thumbs(
        report_date="2026-04-15", item_id="n1", score=1, tz=TZ
    )
    assert evt.ts.tzinfo is not None
    assert evt.ts.year == 2026 or evt.ts.year >= 2026  # sanity
