import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_thumbs_up_writes_event_to_log(
    client: TestClient, tmp_feedback_dir: Path
) -> None:
    resp = client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n1", "score": 1},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    files = list(tmp_feedback_dir.glob("*.jsonl"))
    assert len(files) == 1
    line = files[0].read_text().strip()
    evt = json.loads(line)
    assert evt["item_id"] == "n1"
    assert evt["score"] == 1
    assert evt["type"] == "thumbs"
    assert evt["report_date"] == "2026-04-15"


def test_thumbs_down_writes_event(
    client: TestClient, tmp_feedback_dir: Path
) -> None:
    client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n2", "score": -1},
    )
    line = list(tmp_feedback_dir.glob("*.jsonl"))[0].read_text().strip()
    assert json.loads(line)["score"] == -1


def test_thumbs_zero_writes_clear_event(
    client: TestClient, tmp_feedback_dir: Path
) -> None:
    resp = client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n1", "score": 0},
    )
    assert resp.status_code == 200
    line = list(tmp_feedback_dir.glob("*.jsonl"))[0].read_text().strip()
    assert json.loads(line)["score"] == 0


def test_thumbs_invalid_score_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n1", "score": 5},
    )
    assert resp.status_code == 422


def test_thumbs_missing_field_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "score": 1},
    )
    assert resp.status_code == 422


def test_thumbs_bad_date_format_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/feedback/thumbs",
        json={"report_date": "not-a-date", "item_id": "n1", "score": 1},
    )
    assert resp.status_code == 422


def test_thumbs_unknown_report_date_still_accepted(
    client: TestClient, tmp_feedback_dir: Path
) -> None:
    # No report file exists; endpoint is write-only, doesn't validate.
    resp = client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2099-01-01", "item_id": "zz", "score": 1},
    )
    assert resp.status_code == 200
    assert list(tmp_feedback_dir.glob("*.jsonl"))


def test_thumbs_event_has_server_timestamp(
    client: TestClient, tmp_feedback_dir: Path
) -> None:
    client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n1", "score": 1},
    )
    line = list(tmp_feedback_dir.glob("*.jsonl"))[0].read_text().strip()
    evt = json.loads(line)
    assert "ts" in evt
    assert "T" in evt["ts"]
    assert "+" in evt["ts"] or "Z" in evt["ts"]


def test_subsequent_thumbs_updates_derived_state(
    client: TestClient,
    tmp_reports_dir: Path,
    tmp_feedback_dir: Path,
    sample_report: dict,
    write_report_fn,
) -> None:
    write_report_fn(tmp_reports_dir, sample_report)
    client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n1", "score": 1},
    )
    client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n1", "score": -1},
    )
    body = client.get("/reports/2026-04-15").text
    assert 'data-item-id="n1"' in body
    assert "thumb-down active" in body
