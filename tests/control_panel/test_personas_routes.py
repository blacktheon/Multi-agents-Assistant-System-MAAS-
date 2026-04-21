from pathlib import Path

from fastapi.testclient import TestClient


def test_personas_list_shows_md_files_from_prompts(
    client: TestClient, project_root: Path,
) -> None:
    (project_root / "prompts" / "secretary.md").write_text("# S\n", encoding="utf-8")
    (project_root / "prompts" / "secretary_free.md").write_text("# SF\n", encoding="utf-8")
    r = client.get("/personas")
    assert r.status_code == 200
    assert "secretary" in r.text
    assert "secretary_free" in r.text


def test_personas_list_picks_up_new_md_file(
    client: TestClient, project_root: Path,
) -> None:
    # Dropping a new md into prompts/ is enough to see it listed.
    (project_root / "prompts" / "brand_new.md").write_text("# new\n", encoding="utf-8")
    r = client.get("/personas")
    assert r.status_code == 200
    assert "brand_new" in r.text


def test_personas_edit_renders(client: TestClient, project_root: Path) -> None:
    (project_root / "prompts" / "secretary.md").write_text("# Secretary\n", encoding="utf-8")
    r = client.get("/personas/secretary")
    assert r.status_code == 200
    assert "# Secretary" in r.text


def test_personas_edit_missing_file_404(client: TestClient, project_root: Path) -> None:
    # Name validation passes but file doesn't exist → 404.
    r = client.get("/personas/no_such_persona")
    assert r.status_code == 404


def test_personas_edit_invalid_name_404(client: TestClient) -> None:
    # Name fails validation pattern (contains dash).
    r = client.get("/personas/bad-name")
    assert r.status_code == 404


def test_personas_post_overwrites_existing(
    client: TestClient, project_root: Path,
) -> None:
    (project_root / "prompts" / "manager.md").write_text("# old\n", encoding="utf-8")
    r = client.post(
        "/personas/manager",
        data={"content": "# New\n"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert (project_root / "prompts" / "manager.md").read_text(encoding="utf-8") == "# New\n"


def test_personas_post_nonexistent_404(
    client: TestClient, project_root: Path,
) -> None:
    # Don't allow creating new files via POST.
    r = client.post(
        "/personas/not_a_thing",
        data={"content": "# x\n"},
        follow_redirects=False,
    )
    assert r.status_code == 404
