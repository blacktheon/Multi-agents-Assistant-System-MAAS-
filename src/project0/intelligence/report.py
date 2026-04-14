"""DailyReport schema, validator, and filesystem helpers.

Reports live as flat JSON files at ``data/intelligence/reports/YYYY-MM-DD.json``.
One file per day. Hand-written validator (no Pydantic — not a project
dependency and not worth adding for one file).

Hard rules enforced by ``validate_report_dict`` match §5.3 of the spec."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
_VALID_IMPORTANCE = {"high", "medium", "low"}

_REQUIRED_TOP_LEVEL = {
    "date",
    "generated_at",
    "user_tz",
    "watchlist_snapshot",
    "news_items",
    "suggested_accounts",
    "stats",
}


def parse_json_strict(text: str) -> dict[str, Any]:
    """Parse JSON from LLM output. Tolerates a surrounding markdown code
    fence and leading/trailing whitespace but nothing else. Rejects
    anything whose top level is not a JSON object."""
    s = text.strip()
    # Strip an optional ```json ... ``` or ``` ... ``` fence.
    if s.startswith("```"):
        # Drop the opening fence line.
        first_nl = s.find("\n")
        if first_nl == -1:
            raise ValueError("JSON code fence has no body")
        s = s[first_nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError(f"JSON top-level must be an object, got {type(parsed).__name__}")
    return parsed


def validate_report_dict(d: dict[str, Any]) -> None:
    """Raise ValueError with a path-qualified message on any hard-rule
    violation. See §5.3 of the spec for the full rule list."""
    missing = _REQUIRED_TOP_LEVEL - set(d.keys())
    if missing:
        raise ValueError(f"report missing keys: {sorted(missing)}")

    if not isinstance(d["date"], str) or not _DATE_RE.match(d["date"]):
        raise ValueError(f"report.date must be YYYY-MM-DD, got {d['date']!r}")

    news_items = d["news_items"]
    if not isinstance(news_items, list):
        raise ValueError("report.news_items must be a list")

    seen_ids: set[str] = set()
    for i, item in enumerate(news_items):
        if not isinstance(item, dict):
            raise ValueError(f"report.news_items[{i}] must be an object")
        for req in ("id", "headline", "summary", "importance", "source_tweets"):
            if req not in item:
                raise ValueError(f"report.news_items[{i}] missing key: {req}")
        if item["importance"] not in _VALID_IMPORTANCE:
            raise ValueError(
                f"report.news_items[{i}].importance must be one of "
                f"{sorted(_VALID_IMPORTANCE)}, got {item['importance']!r}"
            )
        if item["id"] in seen_ids:
            raise ValueError(f"report.news_items: duplicate id {item['id']!r}")
        seen_ids.add(item["id"])
        srcs = item["source_tweets"]
        if not isinstance(srcs, list) or len(srcs) == 0:
            raise ValueError(
                f"report.news_items[{i}].source_tweets must be a non-empty list"
            )

    suggested = d["suggested_accounts"]
    if not isinstance(suggested, list):
        raise ValueError("report.suggested_accounts must be a list")
    for i, acc in enumerate(suggested):
        if not isinstance(acc, dict):
            raise ValueError(f"report.suggested_accounts[{i}] must be an object")
        seen_in = acc.get("seen_in_items") or []
        if not isinstance(seen_in, list):
            raise ValueError(
                f"report.suggested_accounts[{i}].seen_in_items must be a list"
            )
        for ref in seen_in:
            if ref not in seen_ids:
                raise ValueError(
                    f"report.suggested_accounts[{i}].seen_in_items "
                    f"references unknown news_item id {ref!r}"
                )

    stats = d["stats"]
    if not isinstance(stats, dict):
        raise ValueError("report.stats must be an object")
    attempted = int(stats.get("handles_attempted", 0))
    succeeded = int(stats.get("handles_succeeded", 0))
    if succeeded > attempted:
        raise ValueError(
            f"report.stats.handles_succeeded ({succeeded}) > "
            f"handles_attempted ({attempted})"
        )


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` to ``path`` atomically via tmp+fsync+rename. Ensures
    no partial file is left behind if the process crashes mid-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_report(path: Path) -> dict[str, Any]:
    """Load a report file, parse, validate, return the dict."""
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level is not an object")
    validate_report_dict(data)
    return data


def list_report_dates(reports_dir: Path) -> list[date]:
    """Return all YYYY-MM-DD.json filenames in ``reports_dir`` as a sorted
    descending list of ``date`` objects. Non-matching filenames (.tmp,
    .bak, notes.txt) are ignored. A missing directory returns []."""
    if not reports_dir.exists() or not reports_dir.is_dir():
        return []
    dates: list[date] = []
    for entry in reports_dir.iterdir():
        if not entry.is_file():
            continue
        m = _FILENAME_RE.match(entry.name)
        if not m:
            continue
        try:
            dates.append(datetime.strptime(m.group(1), "%Y-%m-%d").date())
        except ValueError:
            continue
    dates.sort(reverse=True)
    return dates
