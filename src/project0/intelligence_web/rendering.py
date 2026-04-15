"""Rendering adapter for the Intelligence webapp (6e).

Produces a plain dict ready for Jinja2 to iterate over. Templates stay dumb:
all sorting, grouping, formatting, and feedback-state merging happens here
so it can be unit-tested without touching HTML."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

_IMPORTANCE_ORDER = {"high": 0, "medium": 1, "low": 2}


def sort_by_importance(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable sort: high → medium → low. Anything else sorts after low."""
    return sorted(
        list(items),
        key=lambda it: _IMPORTANCE_ORDER.get(it.get("importance", "low"), 99),
    )


def format_time(
    iso_string: str,
    *,
    user_tz: ZoneInfo,
    now: datetime | None = None,
) -> str:
    """Return `"HH:MM (X hours ago)"` for a given ISO-8601 timestamp.

    `now` is injectable so tests are deterministic; production passes None
    and we use `datetime.now(tz=user_tz)`."""
    parsed = datetime.fromisoformat(iso_string)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=user_tz)
    local = parsed.astimezone(user_tz)
    hhmm = local.strftime("%H:%M")
    now = now or datetime.now(tz=user_tz)
    delta = now - local
    secs = int(delta.total_seconds())
    if secs < 60:
        rel = "just now"
    elif secs < 3600:
        rel = f"{secs // 60}m ago"
    elif secs < 86400:
        rel = f"{secs // 3600}h ago"
    else:
        rel = f"{secs // 86400}d ago"
    return f"{hhmm} ({rel})"


def groupby_month(dates: list[date]) -> list[tuple[str, list[date]]]:
    """Group a descending-sorted list of dates by YYYY-MM. Preserves order."""
    groups: list[tuple[str, list[date]]] = []
    current_key: str | None = None
    for d in dates:
        key = d.strftime("%Y-%m")
        if key != current_key:
            groups.append((key, []))
            current_key = key
        groups[-1][1].append(d)
    return groups


def build_report_context(
    *,
    report_dict: dict[str, Any],
    feedback_state: dict[str, int],
    all_dates: list[date],
    current: date,
    public_base_url: str,
) -> dict[str, Any]:
    """Build the Jinja2 context dict for a single report page render.

    `all_dates` must be sorted descending (newest first). "prev" means an
    older date (next item in the list); "next" means a newer date (previous
    item in the list)."""
    idx = all_dates.index(current)
    older = all_dates[idx + 1] if idx + 1 < len(all_dates) else None
    newer = all_dates[idx - 1] if idx - 1 >= 0 else None
    return {
        "report": report_dict,
        "feedback": feedback_state,
        "current_date": current.isoformat(),
        "prev_href": f"/reports/{older.isoformat()}" if older else None,
        "next_href": f"/reports/{newer.isoformat()}" if newer else None,
        "all_dates": [d.isoformat() for d in all_dates],
        "public_base_url": public_base_url,
    }
