from __future__ import annotations

from pathlib import Path

import pytest

from project0.store import UserProfile


def _write(tmp: Path, content: str) -> Path:
    p = tmp / "user_profile.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def test_missing_file_yields_empty_profile(tmp_path: Path) -> None:
    p = tmp_path / "does_not_exist.yaml"
    profile = UserProfile.load(p)
    assert profile.as_prompt_block() == ""


def test_loads_all_fields(tmp_path: Path) -> None:
    p = _write(tmp_path, """
address_as: "主人"
birthday: "1995-03-14"
fixed_preferences:
  - "说话简洁"
  - "不喜欢凌晨打扰"
out_of_band_notes: |
  我在做 MAAS 项目。
""")
    profile = UserProfile.load(p)
    block = profile.as_prompt_block()
    assert "主人" in block
    assert "1995-03-14" in block
    assert "说话简洁" in block
    assert "MAAS" in block


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "address_as: [unclosed")
    with pytest.raises(RuntimeError) as e:
        UserProfile.load(p)
    assert "user_profile.yaml" in str(e.value) or str(p) in str(e.value)


def test_unknown_top_level_keys_ignored(tmp_path: Path) -> None:
    p = _write(tmp_path, """
address_as: "主人"
some_future_field: "ignored"
""")
    profile = UserProfile.load(p)
    block = profile.as_prompt_block()
    assert "主人" in block
    assert "ignored" not in block


def test_invalid_date_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "birthday: \"not a date\"")
    with pytest.raises(RuntimeError) as e:
        UserProfile.load(p)
    assert "birthday" in str(e.value)


def test_non_list_fixed_preferences_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "fixed_preferences: \"just a string\"")
    with pytest.raises(RuntimeError) as e:
        UserProfile.load(p)
    assert "fixed_preferences" in str(e.value)
