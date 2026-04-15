from pathlib import Path

from fastapi.testclient import TestClient


def test_history_lists_all_dates_descending(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    for d in ("2026-04-10", "2026-04-12", "2026-04-15", "2026-03-30"):
        write_report_fn(tmp_reports_dir, {**sample_report, "date": d})
    body = client.get("/history").text
    for d in ("2026-04-10", "2026-04-12", "2026-04-15", "2026-03-30"):
        assert d in body
    idx_15 = body.index("2026-04-15")
    idx_12 = body.index("2026-04-12")
    idx_10 = body.index("2026-04-10")
    idx_330 = body.index("2026-03-30")
    assert idx_15 < idx_12 < idx_10 < idx_330


def test_history_with_no_reports_shows_empty_message(client: TestClient) -> None:
    resp = client.get("/history")
    assert resp.status_code == 200
    assert "No reports yet" in resp.text or "no reports" in resp.text.lower()


def test_history_dates_link_to_report_pages(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    write_report_fn(tmp_reports_dir, {**sample_report, "date": "2026-04-15"})
    body = client.get("/history").text
    assert 'href="/reports/2026-04-15"' in body


def test_history_groups_by_month(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    for d in ("2026-04-15", "2026-04-10", "2026-03-30"):
        write_report_fn(tmp_reports_dir, {**sample_report, "date": d})
    body = client.get("/history").text
    assert "2026-04" in body
    assert "2026-03" in body
