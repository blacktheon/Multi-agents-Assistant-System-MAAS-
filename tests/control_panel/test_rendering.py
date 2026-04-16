"""SVG bar chart renderer for the /usage daily chart.

One <rect> per input row, height proportional to the value. Weekends
rendered with a lighter fill so weekly rhythm is visible. Native SVG
<title> for hover tooltip — zero JS.
"""

from project0.control_panel.rendering import render_bar_chart_svg


def test_empty_rows_returns_empty_svg() -> None:
    svg = render_bar_chart_svg([])
    assert "<svg" in svg
    assert "</svg>" in svg
    assert svg.count("<rect") == 0


def test_one_rect_per_row() -> None:
    rows = [
        {"day": "2026-04-01", "total": 100},
        {"day": "2026-04-02", "total": 200},
        {"day": "2026-04-03", "total": 150},
    ]
    svg = render_bar_chart_svg(rows)
    assert svg.count("<rect") == 3


def test_title_tooltip_contains_day_and_total() -> None:
    rows = [{"day": "2026-04-01", "total": 12345}]
    svg = render_bar_chart_svg(rows)
    assert "<title>2026-04-01: 12,345 tokens</title>" in svg


def test_tallest_bar_has_max_height() -> None:
    rows = [
        {"day": "2026-04-01", "total": 100},
        {"day": "2026-04-02", "total": 1000},
        {"day": "2026-04-03", "total": 500},
    ]
    svg = render_bar_chart_svg(rows, chart_height=120)
    import re
    rect_heights = [
        float(m.group(1))
        for m in re.finditer(r'<rect[^>]*\sheight="(\d+(?:\.\d+)?)"', svg)
    ]
    assert len(rect_heights) == 3
    assert max(rect_heights) == rect_heights[1]


def test_max_label_shown() -> None:
    rows = [
        {"day": "2026-04-01", "total": 100},
        {"day": "2026-04-02", "total": 1000},
    ]
    svg = render_bar_chart_svg(rows)
    assert "1,000" in svg


def test_weekend_fill_differs_from_weekday() -> None:
    rows = [
        {"day": "2026-04-04", "total": 100},  # Saturday
        {"day": "2026-04-06", "total": 100},  # Monday
    ]
    svg = render_bar_chart_svg(rows)
    import re
    fills = re.findall(r'<rect[^>]*\sfill="([^"]+)"', svg)
    assert len(fills) == 2
    assert fills[0] != fills[1]
