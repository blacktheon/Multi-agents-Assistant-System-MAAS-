from pathlib import Path

from fastapi.testclient import TestClient


def test_personas_list(client: TestClient) -> None:
    r = client.get("/personas")
    assert r.status_code == 200
    for name in ("manager", "secretary", "intelligence"):
        assert name in r.text


def test_personas_edit_renders(client: TestClient, project_root: Path) -> None:
    (project_root / "prompts" / "secretary.md").write_text("# Secretary\n", encoding="utf-8")
    r = client.get("/personas/secretary")
    assert r.status_code == 200
    assert "# Secretary" in r.text


def test_personas_edit_unknown_404(client: TestClient) -> None:
    r = client.get("/personas/random")
    assert r.status_code == 404


def test_personas_post_overwrites(client: TestClient, project_root: Path) -> None:
    r = client.post(
        "/personas/manager",
        data={"content": "# New\n"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert (project_root / "prompts" / "manager.md").read_text(encoding="utf-8") == "# New\n"
