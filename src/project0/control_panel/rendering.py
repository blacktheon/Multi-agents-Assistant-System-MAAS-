"""Jinja2 environment + the SVG bar chart macro used by /usage.

The chart is rendered as a single string of inline <svg> markup so the
template can ``{{ svg | safe }}`` it directly. No JS, no CDN, no
external charting library — 30 days × 1 rect each is a trivial amount
of markup to generate server-side.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates

_PACKAGE_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"


def build_templates() -> Jinja2Templates:
    return Jinja2Templates(directory=str(_TEMPLATES_DIR))


_CHART_WIDTH = 800
_CHART_HEIGHT = 150
_PAD_TOP = 20
_PAD_BOTTOM = 10
_PAD_LEFT = 50
_PAD_RIGHT = 10
_WEEKDAY_FILL = "#3366aa"
_WEEKEND_FILL = "#88aadd"


def render_bar_chart_svg(
    rows: list[dict[str, Any]],
    *,
    chart_width: int = _CHART_WIDTH,
    chart_height: int = _CHART_HEIGHT,
) -> str:
    """Render an inline SVG bar chart from rollup rows.

    Each row must have ``day`` (YYYY-MM-DD string) and ``total`` (int).
    Empty list produces an empty chart frame.
    """
    plot_w = chart_width - _PAD_LEFT - _PAD_RIGHT
    plot_h = chart_height - _PAD_TOP - _PAD_BOTTOM

    if not rows:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {chart_width} {chart_height}" '
            f'width="{chart_width}" height="{chart_height}" '
            f'style="max-width:100%;height:auto;">'
            f'<text x="{chart_width//2}" y="{chart_height//2}" '
            f'text-anchor="middle" fill="#888">no data</text>'
            f'</svg>'
        )

    max_total = max(int(r["total"]) for r in rows) or 1
    n = len(rows)
    bar_w = plot_w / n
    gap = max(1.0, bar_w * 0.1)
    draw_w = bar_w - gap

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {chart_width} {chart_height}" '
        f'width="{chart_width}" height="{chart_height}" '
        f'style="max-width:100%;height:auto;font-family:monospace;font-size:10px;">'
    )
    parts.append(
        f'<text x="4" y="14" fill="#444">{_fmt(max_total)}</text>'
    )
    for i, r in enumerate(rows):
        day_str = str(r["day"])
        total = int(r["total"])
        h = (total / max_total) * plot_h
        x = _PAD_LEFT + i * bar_w
        y = _PAD_TOP + (plot_h - h)
        fill = _WEEKEND_FILL if _is_weekend(day_str) else _WEEKDAY_FILL
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{draw_w:.1f}" '
            f'height="{h:.1f}" fill="{fill}">'
            f'<title>{day_str}: {_fmt(total)} tokens</title>'
            f'</rect>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _fmt(n: int) -> str:
    return f"{n:,}"


def _is_weekend(day_str: str) -> bool:
    try:
        y, m, d = map(int, day_str.split("-"))
        return date(y, m, d).weekday() >= 5
    except (ValueError, IndexError):
        return False
