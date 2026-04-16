"""POST /maas/start, /stop, /restart drive the supervisor."""

from fastapi.testclient import TestClient


def test_start_transitions_to_running(client: TestClient) -> None:
    r = client.post("/maas/start", follow_redirects=False)
    assert r.status_code in (302, 303)
    r2 = client.get("/")
    assert "running" in r2.text.lower()


def test_stop_after_start_transitions_to_stopped(client: TestClient) -> None:
    client.post("/maas/start")
    r = client.post("/maas/stop", follow_redirects=False)
    assert r.status_code in (302, 303)
    r2 = client.get("/")
    assert "stopped" in r2.text.lower()
