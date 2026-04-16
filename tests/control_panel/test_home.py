"""GET / renders the status header and some content."""

from fastapi.testclient import TestClient


def test_home_200(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200


def test_home_shows_stopped_status_initially(client: TestClient) -> None:
    r = client.get("/")
    assert "stopped" in r.text.lower()


def test_home_has_nav_links(client: TestClient) -> None:
    r = client.get("/")
    for href in ("/profile", "/facts", "/toml", "/personas", "/env", "/usage"):
        assert href in r.text
