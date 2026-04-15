from pathlib import Path
from zoneinfo import ZoneInfo

from project0.intelligence_web.feedback import (
    FeedbackEvent,
    append_thumbs,
    load_thumbs_state_for,
)

TZ = ZoneInfo("Asia/Shanghai")


def _append(fb_dir: Path, report_date: str, item_id: str, score: int) -> None:
    append_thumbs(
        FeedbackEvent.thumbs(
            report_date=report_date,
            item_id=item_id,
            score=score,  # type: ignore[arg-type]
            tz=TZ,
        ),
        fb_dir,
    )


def test_empty_dir_returns_empty_state(tmp_path: Path) -> None:
    assert load_thumbs_state_for("2026-04-15", tmp_path / "nope") == {}


def test_single_thumbs_up(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    _append(fb, "2026-04-15", "n1", 1)
    assert load_thumbs_state_for("2026-04-15", fb) == {"n1": 1}


def test_latest_write_wins(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    _append(fb, "2026-04-15", "n1", 1)
    _append(fb, "2026-04-15", "n1", -1)
    assert load_thumbs_state_for("2026-04-15", fb) == {"n1": -1}


def test_score_zero_clears_entry(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    _append(fb, "2026-04-15", "n1", 1)
    _append(fb, "2026-04-15", "n1", 0)
    assert load_thumbs_state_for("2026-04-15", fb) == {}


def test_filters_other_report_dates(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    _append(fb, "2026-04-14", "n1", 1)
    _append(fb, "2026-04-15", "n2", -1)
    assert load_thumbs_state_for("2026-04-15", fb) == {"n2": -1}


def test_reads_current_and_previous_month(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    fb.mkdir()
    # Hand-seed a March file containing an event for a March report, plus
    # an April file containing another event for the same March report.
    (fb / "2026-03.jsonl").write_text(
        '{"ts":"2026-03-31T23:00:00+08:00","type":"thumbs","report_date":"2026-03-31","item_id":"n1","score":1}\n',
        encoding="utf-8",
    )
    (fb / "2026-04.jsonl").write_text(
        '{"ts":"2026-04-01T09:00:00+08:00","type":"thumbs","report_date":"2026-03-31","item_id":"n2","score":-1}\n',
        encoding="utf-8",
    )
    state = load_thumbs_state_for("2026-03-31", fb)
    assert state == {"n1": 1, "n2": -1}


def test_skips_corrupt_lines(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    fb.mkdir()
    (fb / "2026-04.jsonl").write_text(
        '{"ts":"2026-04-15T10:00:00+08:00","type":"thumbs","report_date":"2026-04-15","item_id":"n1","score":1}\n'
        '{broken json here\n'
        '{"ts":"2026-04-15T11:00:00+08:00","type":"thumbs","report_date":"2026-04-15","item_id":"n2","score":-1}\n',
        encoding="utf-8",
    )
    state = load_thumbs_state_for("2026-04-15", fb)
    assert state == {"n1": 1, "n2": -1}


def test_skips_unknown_event_type(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    fb.mkdir()
    (fb / "2026-04.jsonl").write_text(
        '{"ts":"2026-04-15T10:00:00+08:00","type":"mute_topic","report_date":"2026-04-15","topic":"crypto"}\n'
        '{"ts":"2026-04-15T11:00:00+08:00","type":"thumbs","report_date":"2026-04-15","item_id":"n1","score":1}\n',
        encoding="utf-8",
    )
    assert load_thumbs_state_for("2026-04-15", fb) == {"n1": 1}


def test_event_stamped_far_from_report_date_still_found(tmp_path: Path) -> None:
    """Regression: an event for a future/past report written today lands in
    the click-time month file, not the report-date month file. Cross-file
    scan must still find it. (Caught by the 6e smoke test.)"""
    fb = tmp_path / "feedback"
    fb.mkdir()
    (fb / "2026-04.jsonl").write_text(
        '{"ts":"2026-04-15T10:00:00+08:00","type":"thumbs","report_date":"2099-12-31","item_id":"n1","score":1}\n',
        encoding="utf-8",
    )
    assert load_thumbs_state_for("2099-12-31", fb) == {"n1": 1}


def test_skips_events_with_invalid_score(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    fb.mkdir()
    (fb / "2026-04.jsonl").write_text(
        '{"ts":"2026-04-15T10:00:00+08:00","type":"thumbs","report_date":"2026-04-15","item_id":"n1","score":5}\n'
        '{"ts":"2026-04-15T11:00:00+08:00","type":"thumbs","report_date":"2026-04-15","item_id":"n2","score":1}\n',
        encoding="utf-8",
    )
    assert load_thumbs_state_for("2026-04-15", fb) == {"n2": 1}
