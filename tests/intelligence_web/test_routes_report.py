import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_root_returns_latest_report(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    # Write two reports on different dates
    newer = {**sample_report, "date": "2026-04-15"}
    older = {**sample_report, "date": "2026-04-10"}
    write_report_fn(tmp_reports_dir, newer)
    write_report_fn(tmp_reports_dir, older)

    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "2026-04-15" in body
    assert "OpenAI 发布 o5-mini" in body


def test_root_with_no_reports_returns_empty_html(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No reports yet" in resp.text


def test_get_report_by_date(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    write_report_fn(tmp_reports_dir, sample_report)
    resp = client.get("/reports/2026-04-15")
    assert resp.status_code == 200
    assert "OpenAI 发布 o5-mini" in resp.text
    assert "DeepMind 记忆机制论文" in resp.text
    assert "Anthropic 招聘" in resp.text


def test_get_nonexistent_date_returns_404(client: TestClient) -> None:
    resp = client.get("/reports/2099-01-01")
    assert resp.status_code == 404


def test_get_bad_date_format_returns_400(client: TestClient) -> None:
    resp = client.get("/reports/not-a-date")
    assert resp.status_code == 400


def test_items_rendered_in_importance_order(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    write_report_fn(tmp_reports_dir, sample_report)
    body = client.get("/reports/2026-04-15").text
    high_idx = body.index("OpenAI 发布 o5-mini")
    med_idx = body.index("DeepMind 记忆机制论文")
    low_idx = body.index("Anthropic 招聘")
    assert high_idx < med_idx < low_idx


def test_source_tweet_links_rendered(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    write_report_fn(tmp_reports_dir, sample_report)
    body = client.get("/reports/2026-04-15").text
    assert 'href="https://x.com/sama/status/1"' in body
    assert 'href="https://x.com/googledeepmind/status/2"' in body


def test_suggested_accounts_rendered_with_x_links(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    write_report_fn(tmp_reports_dir, sample_report)
    body = client.get("/reports/2026-04-15").text
    assert 'href="https://x.com/noamgpt"' in body
    assert "被 @sama 引用" in body


def test_prev_next_hrefs_rendered_when_applicable(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    write_report_fn(tmp_reports_dir, {**sample_report, "date": "2026-04-14"})
    write_report_fn(tmp_reports_dir, {**sample_report, "date": "2026-04-15"})
    write_report_fn(tmp_reports_dir, {**sample_report, "date": "2026-04-16"})
    body = client.get("/reports/2026-04-15").text
    assert 'href="/reports/2026-04-14"' in body   # older (prev)
    assert 'href="/reports/2026-04-16"' in body   # newer (next)


def test_date_dropdown_contains_all_dates(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    for d in ("2026-04-12", "2026-04-13", "2026-04-14", "2026-04-15"):
        write_report_fn(tmp_reports_dir, {**sample_report, "date": d})
    body = client.get("/reports/2026-04-14").text
    for d in ("2026-04-12", "2026-04-13", "2026-04-14", "2026-04-15"):
        assert f'value="{d}"' in body


def test_feedback_state_reflected_in_rendered_buttons(
    client: TestClient,
    tmp_reports_dir: Path,
    tmp_feedback_dir: Path,
    sample_report: dict,
    write_report_fn,
) -> None:
    write_report_fn(tmp_reports_dir, sample_report)
    tmp_feedback_dir.mkdir()
    (tmp_feedback_dir / "2026-04.jsonl").write_text(
        '{"ts":"2026-04-15T10:00:00+08:00","type":"thumbs","report_date":"2026-04-15","item_id":"n1","score":1}\n',
        encoding="utf-8",
    )
    body = client.get("/reports/2026-04-15").text
    assert 'data-item-id="n1"' in body
    assert "active" in body
