from pathlib import Path

from fastapi.testclient import TestClient


def test_get_renders_existing_profile(client: TestClient, project_root: Path) -> None:
    r = client.get("/profile")
    assert r.status_code == 200
    assert "address_as: 主人" in r.text


def test_get_when_missing_file_returns_empty_textarea(
    client: TestClient, project_root: Path
) -> None:
    (project_root / "data" / "user_profile.yaml").unlink()
    r = client.get("/profile")
    assert r.status_code == 200
    assert "<textarea" in r.text


def test_post_overwrites_file(client: TestClient, project_root: Path) -> None:
    new_content = "address_as: 陛下\nbirthday: '2000-01-01'\n"
    r = client.post("/profile", data={"content": new_content}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert (project_root / "data" / "user_profile.yaml").read_text(encoding="utf-8") == new_content


def test_post_survives_chinese_content(client: TestClient, project_root: Path) -> None:
    new_content = "out_of_band_notes: |\n  我喜欢吃寿司\n"
    client.post("/profile", data={"content": new_content})
    assert "我喜欢吃寿司" in (project_root / "data" / "user_profile.yaml").read_text(encoding="utf-8")
