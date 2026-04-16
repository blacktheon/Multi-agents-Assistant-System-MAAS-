from pathlib import Path

from fastapi.testclient import TestClient


def test_env_get_renders_verbatim_including_secret(
    client: TestClient, project_root: Path
) -> None:
    r = client.get("/env")
    assert r.status_code == 200
    assert "ANTHROPIC_API_KEY=sk-fake" in r.text


def test_env_get_missing_file_empty_textarea(
    client: TestClient, project_root: Path
) -> None:
    (project_root / ".env").unlink()
    r = client.get("/env")
    assert r.status_code == 200
    assert "<textarea" in r.text


def test_env_post_overwrites(client: TestClient, project_root: Path) -> None:
    new = "ANTHROPIC_API_KEY=sk-new\nANTHROPIC_CACHE_TTL=1h\n"
    r = client.post("/env", data={"content": new}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert (project_root / ".env").read_text(encoding="utf-8") == new
