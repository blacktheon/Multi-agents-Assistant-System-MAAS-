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


# --- review-page helpers -----------------------------------------------------

_SPARK_STROKE = "#3366aa"
_TIMESERIES_COLORS = {
    "manager":      "#cc4455",  # red
    "intelligence": "#3366aa",  # blue
    "learning":     "#449944",  # green
}
_TS_CHART_WIDTH = 820
_TS_CHART_HEIGHT = 220
_TS_PAD_TOP = 20
_TS_PAD_BOTTOM = 30
_TS_PAD_LEFT = 50
_TS_PAD_RIGHT = 110


def render_sparkline_svg(
    points: list[int], *, width: int = 120, height: int = 32,
) -> str:
    """Tiny sparkline: one polyline, no axes, no legend. Y-range is clamped
    to 0-100 regardless of input so sparklines across cards are comparable."""
    if not points:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" style="display:block;">'
            f'<text x="{width//2}" y="{height//2}" text-anchor="middle" '
            f'fill="#888" font-size="10">no data</text></svg>'
        )
    n = len(points)
    if n == 1:
        x = width / 2
        y = height - (points[0] / 100.0) * (height - 2) - 1
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" style="display:block;">'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" fill="{_SPARK_STROKE}"/>'
            f'</svg>'
        )
    step = width / (n - 1)
    pts: list[str] = []
    for i, v in enumerate(points):
        clamped = max(0, min(100, int(v)))
        x = i * step
        y = height - (clamped / 100.0) * (height - 2) - 1
        pts.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(pts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" style="display:block;">'
        f'<polyline fill="none" stroke="{_SPARK_STROKE}" stroke-width="1.5" '
        f'points="{poly}"/>'
        f'</svg>'
    )


def render_score_timeseries_svg(
    series: dict[str, list[tuple[str, int]]],
    *,
    width: int = _TS_CHART_WIDTH,
    height: int = _TS_CHART_HEIGHT,
) -> str:
    """Multi-line time-series chart of overall scores. Y-axis fixed 0-100.
    X-axis is the union of timestamps across all series, sorted; each agent's
    points align to that shared axis. Minimal axis/tick work — just a 0/50/100
    y-grid and a horizontal baseline."""
    plot_w = width - _TS_PAD_LEFT - _TS_PAD_RIGHT
    plot_h = height - _TS_PAD_TOP - _TS_PAD_BOTTOM

    live = {k: v for k, v in series.items() if v}
    if not live:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" '
            f'style="max-width:100%;height:auto;">'
            f'<text x="{width//2}" y="{height//2}" text-anchor="middle" '
            f'fill="#888">no data — come back after a few reviews</text>'
            f'</svg>'
        )

    all_ts = sorted({ts for pts in live.values() for ts, _ in pts})
    n = len(all_ts)
    ts_to_x = {
        ts: _TS_PAD_LEFT + (i * plot_w / max(1, n - 1))
        for i, ts in enumerate(all_ts)
    }

    def _y(score: int) -> float:
        clamped = max(0, min(100, int(score)))
        return _TS_PAD_TOP + (1 - clamped / 100.0) * plot_h

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" '
        f'style="max-width:100%;height:auto;font-family:monospace;font-size:10px;">'
    ]
    for y_val in (0, 50, 100):
        gy = _y(y_val)
        parts.append(
            f'<line x1="{_TS_PAD_LEFT}" y1="{gy:.1f}" '
            f'x2="{_TS_PAD_LEFT + plot_w}" y2="{gy:.1f}" '
            f'stroke="#ddd" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{_TS_PAD_LEFT - 6}" y="{gy + 3:.1f}" '
            f'text-anchor="end" fill="#666">{y_val}</text>'
        )
    legend_y = _TS_PAD_TOP
    for agent, pts in live.items():
        color = _TIMESERIES_COLORS.get(agent, "#888")
        coords = [f"{ts_to_x[ts]:.1f},{_y(s):.1f}" for ts, s in pts]
        if len(coords) >= 2:
            parts.append(
                f'<polyline fill="none" stroke="{color}" stroke-width="2" '
                f'points="{" ".join(coords)}"/>'
            )
        else:
            cx_s, cy_s = coords[0].split(",")
            parts.append(
                f'<circle cx="{cx_s}" cy="{cy_s}" r="3" fill="{color}"/>'
            )
        lx = _TS_PAD_LEFT + plot_w + 10
        parts.append(
            f'<rect x="{lx}" y="{legend_y - 6}" width="10" height="10" '
            f'fill="{color}"/>'
            f'<text x="{lx + 14}" y="{legend_y + 3}" fill="#333">{agent}</text>'
        )
        legend_y += 16
    parts.append("</svg>")
    return "".join(parts)
