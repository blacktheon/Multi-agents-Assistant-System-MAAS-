from pathlib import Path

from fastapi.testclient import TestClient


def test_toml_list_shows_toml_files_from_prompts(
    client: TestClient, project_root: Path,
) -> None:
    (project_root / "prompts" / "secretary.toml").write_text("x = 1\n", encoding="utf-8")
    (project_root / "prompts" / "secretary_free.toml").write_text("x = 2\n", encoding="utf-8")
    r = client.get("/toml")
    assert r.status_code == 200
    assert "secretary" in r.text
    assert "secretary_free" in r.text


def test_toml_edit_renders_file(client: TestClient, project_root: Path) -> None:
    (project_root / "prompts" / "manager.toml").write_text(
        "transcript_window = 10\n", encoding="utf-8"
    )
    r = client.get("/toml/manager")
    assert r.status_code == 200
    assert "transcript_window" in r.text


def test_toml_edit_unknown_name_404(client: TestClient) -> None:
    r = client.get("/toml/unknown_agent")
    assert r.status_code == 404


def test_toml_edit_traversal_404(client: TestClient) -> None:
    r = client.get("/toml/..%2Fevil")
    assert r.status_code == 404


def test_toml_edit_invalid_name_404(client: TestClient) -> None:
    r = client.get("/toml/bad-name")
    assert r.status_code == 404


def test_toml_post_overwrites(client: TestClient, project_root: Path) -> None:
    (project_root / "prompts" / "manager.toml").write_text(
        "x = 0\n", encoding="utf-8",
    )
    new = "transcript_window = 5\n"
    r = client.post("/toml/manager", data={"content": new}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert (project_root / "prompts" / "manager.toml").read_text(encoding="utf-8") == new


def test_toml_post_unknown_name_404(client: TestClient) -> None:
    r = client.post("/toml/unknown_agent", data={"content": "x"})
    assert r.status_code == 404
