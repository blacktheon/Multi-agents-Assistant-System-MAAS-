"""Allowlisted name → path resolution for TOML and persona edits.

Prevents path traversal via ``..`` or absolute paths in URL params.
Only three base names are permitted: manager, secretary, intelligence.
"""

from pathlib import Path

import pytest

from project0.control_panel.paths import (
    ALLOWED_AGENT_NAMES,
    persona_path,
    toml_path,
)


def test_allowed_names_are_fixed() -> None:
    assert ALLOWED_AGENT_NAMES == ("manager", "secretary", "intelligence")


def test_toml_path_resolves_known_name(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    p = toml_path("manager", project_root=tmp_path)
    assert p == tmp_path / "prompts" / "manager.toml"


def test_toml_path_rejects_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        toml_path("supervisor", project_root=tmp_path)


def test_toml_path_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        toml_path("../secrets", project_root=tmp_path)


def test_persona_path_resolves_known_name(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    p = persona_path("secretary", project_root=tmp_path)
    assert p == tmp_path / "prompts" / "secretary.md"


def test_persona_path_rejects_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        persona_path("random", project_root=tmp_path)
