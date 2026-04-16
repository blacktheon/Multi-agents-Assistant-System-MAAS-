"""Atomic file writes via tmp + rename. Required so a panel crash mid-save
cannot leave .env or user_profile.yaml in a half-written state."""

from pathlib import Path

from project0.control_panel.writes import atomic_write_text


def test_writes_new_file(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


def test_leaves_no_tmp_file_on_success(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    atomic_write_text(target, "hi")
    leftover = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftover == []


def test_handles_chinese_content(tmp_path: Path) -> None:
    target = tmp_path / "profile.yaml"
    atomic_write_text(target, "address_as: 主人\n备注: 测试\n")
    assert target.read_text(encoding="utf-8") == "address_as: 主人\n备注: 测试\n"


def test_handles_missing_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "does_not_exist" / "x.txt"
    try:
        atomic_write_text(target, "x")
    except FileNotFoundError:
        return
    raise AssertionError("expected FileNotFoundError for missing parent dir")
