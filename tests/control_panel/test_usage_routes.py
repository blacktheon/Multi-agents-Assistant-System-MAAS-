from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from project0.store import Store


def _seed(store: Store) -> None:
    base = datetime.now(UTC).replace(hour=10, minute=0, second=0, microsecond=0)
    for i in range(30):
        day = base - timedelta(days=i)
        ts = day.isoformat(timespec="seconds").replace("+00:00", "Z")
        store.conn.execute(
            "INSERT INTO llm_usage "
            "(ts, agent, model, input_tokens, cache_creation_input_tokens, "
            " cache_read_input_tokens, output_tokens, envelope_id, purpose) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                "secretary" if i % 2 == 0 else "manager",
                "claude-sonnet-4-6",
                100 * (i + 1),
                0,
                50 * (i + 1),
                25 * (i + 1),
                None,
                "reply" if i % 2 == 0 else "tool_loop",
            ),
        )


def test_usage_200(client: TestClient, store: Store) -> None:
    _seed(store)
    r = client.get("/usage")
    assert r.status_code == 200


def test_usage_chart_has_expected_rect_count(client: TestClient, store: Store) -> None:
    _seed(store)
    r = client.get("/usage")
    assert r.text.count("<rect") == 30


def test_usage_table_contains_agent_rollup(client: TestClient, store: Store) -> None:
    _seed(store)
    r = client.get("/usage")
    assert "secretary" in r.text
    assert "manager" in r.text


def test_usage_recent_table_rows(client: TestClient, store: Store) -> None:
    _seed(store)
    r = client.get("/usage")
    assert "3,000" in r.text or "3000" in r.text
