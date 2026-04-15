from fastapi.testclient import TestClient


def test_healthz_returns_200_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_static_css_served(client: TestClient) -> None:
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]


def test_unknown_route_returns_404(client: TestClient) -> None:
    resp = client.get("/not-a-real-path")
    assert resp.status_code == 404
