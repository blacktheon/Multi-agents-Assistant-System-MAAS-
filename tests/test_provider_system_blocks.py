from __future__ import annotations

from project0.llm.provider import SystemBlocks, _render_system_param


def test_str_input_single_cached_block() -> None:
    out = _render_system_param("hello persona")
    assert out == [
        {"type": "text", "text": "hello persona", "cache_control": {"type": "ephemeral"}}
    ]


def test_str_input_with_1h_ttl() -> None:
    out = _render_system_param("hello persona", cache_ttl="1h")
    assert out == [
        {
            "type": "text",
            "text": "hello persona",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]


def test_systemblocks_stable_only_single_marker() -> None:
    sb = SystemBlocks(stable="persona + profile", facts=None)
    out = _render_system_param(sb)
    assert out == [
        {"type": "text", "text": "persona + profile", "cache_control": {"type": "ephemeral"}}
    ]


def test_systemblocks_stable_and_facts_two_markers() -> None:
    sb = SystemBlocks(stable="persona + profile", facts="FACT: 生日 3-14")
    out = _render_system_param(sb)
    assert out == [
        {"type": "text", "text": "persona + profile", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "FACT: 生日 3-14", "cache_control": {"type": "ephemeral"}},
    ]


def test_empty_facts_string_is_omitted() -> None:
    sb = SystemBlocks(stable="persona", facts="")
    out = _render_system_param(sb)
    assert len(out) == 1
