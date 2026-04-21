"""Dynamic prompts/ listing + name validation for TOML and persona edits.

Lists are discovered by scanning the filesystem; name-level validation
prevents traversal (``..``, ``/``, non-[a-z0-9_] chars).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from project0.control_panel.paths import (
    list_persona_files,
    list_toml_files,
    persona_path,
    toml_path,
)


def test_list_persona_files_picks_up_md_files(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "secretary.md").write_text("a")
    (tmp_path / "prompts" / "manager.md").write_text("b")
    (tmp_path / "prompts" / "secretary.toml").write_text("x")  # should be skipped
    files = list_persona_files(project_root=tmp_path)
    assert files == ["manager", "secretary"]


def test_list_toml_files_picks_up_toml_files(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "secretary.toml").write_text("a")
    (tmp_path / "prompts" / "manager.toml").write_text("b")
    (tmp_path / "prompts" / "secretary.md").write_text("x")  # should be skipped
    files = list_toml_files(project_root=tmp_path)
    assert files == ["manager", "secretary"]


def test_list_persona_files_skips_hidden_and_subdirs(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "secretary.md").write_text("a")
    (tmp_path / "prompts" / ".hidden.md").write_text("b")
    (tmp_path / "prompts" / "sub").mkdir()
    (tmp_path / "prompts" / "sub" / "nested.md").write_text("c")
    files = list_persona_files(project_root=tmp_path)
    assert files == ["secretary"]


def test_list_persona_files_returns_empty_when_dir_missing(tmp_path: Path) -> None:
    # No prompts/ dir at all.
    assert list_persona_files(project_root=tmp_path) == []
    assert list_toml_files(project_root=tmp_path) == []


def test_persona_path_resolves_any_valid_name(tmp_path: Path) -> None:
    # No filesystem required at this layer — it's just name validation.
    p = persona_path("anything_valid_123", project_root=tmp_path)
    assert p == tmp_path / "prompts" / "anything_valid_123.md"


def test_toml_path_resolves_any_valid_name(tmp_path: Path) -> None:
    p = toml_path("secretary_free", project_root=tmp_path)
    assert p == tmp_path / "prompts" / "secretary_free.toml"


def test_persona_path_rejects_traversal(tmp_path: Path) -> None:
    for bad in ("..", "../secrets", "a/b", "a.b", "A", "", "a-b"):
        with pytest.raises(ValueError):
            persona_path(bad, project_root=tmp_path)


def test_toml_path_rejects_traversal(tmp_path: Path) -> None:
    for bad in ("..", "../secrets", "a/b", "a.b", "A", "", "a-b"):
        with pytest.raises(ValueError):
            toml_path(bad, project_root=tmp_path)
