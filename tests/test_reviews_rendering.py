"""Tests for SVG helpers used by the /reviews page."""
from __future__ import annotations


def test_render_sparkline_svg_basic() -> None:
    from project0.control_panel.rendering import render_sparkline_svg
    svg = render_sparkline_svg([60, 65, 70, 72, 80], width=100, height=30)
    assert svg.startswith("<svg")
    assert "polyline" in svg
    assert 'viewBox="0 0 100 30"' in svg


def test_render_sparkline_svg_empty() -> None:
    from project0.control_panel.rendering import render_sparkline_svg
    svg = render_sparkline_svg([], width=100, height=30)
    assert svg.startswith("<svg")


def test_render_score_timeseries_svg_three_lines() -> None:
    from project0.control_panel.rendering import render_score_timeseries_svg
    series = {
        "manager":      [("2026-04-17T10:00:00Z", 70), ("2026-04-17T13:00:00Z", 75)],
        "intelligence": [("2026-04-17T10:00:00Z", 60), ("2026-04-17T13:00:00Z", 65)],
        "learning":     [("2026-04-17T10:00:00Z", 80), ("2026-04-17T13:00:00Z", 82)],
    }
    svg = render_score_timeseries_svg(series)
    assert svg.startswith("<svg")
    assert svg.count("<polyline") == 3


def test_render_score_timeseries_svg_empty() -> None:
    from project0.control_panel.rendering import render_score_timeseries_svg
    svg = render_score_timeseries_svg({})
    assert svg.startswith("<svg")
    assert "no data" in svg
