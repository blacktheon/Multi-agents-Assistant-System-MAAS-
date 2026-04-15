# Intelligence Delivery Surface (6e) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a FastAPI webapp rendering Intelligence daily reports with history browsing, thumbs feedback capture, a new `get_report_link` agent tool, and extended thinking on the Opus summarizer call. Reachable from any device (including phone) via Tailscale.

**Architecture:** New `src/project0/intelligence_web/` package (FastAPI app factory, routes, Jinja2 templates, feedback I/O, rendering adapter, config) runs as an additional `asyncio.Task` inside `main.py`'s existing `TaskGroup` alongside the Telegram bot pollers. One-way dependency — the webapp imports from `intelligence` (read-only access to reports) and appends to a new feedback directory, but nothing in the agent package imports from the webapp. Strict separation through a shared `WebConfig` loaded once from `prompts/intelligence.toml`'s new `[web]` section.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, Jinja2, vanilla JS (no build step), pytest + pytest-asyncio, FastAPI `TestClient`, `uv` for dep management.

**Spec:** `docs/superpowers/specs/2026-04-15-intelligence-delivery-surface-design.md`

---

## Prerequisites

Before starting, confirm:

- You are on the `main` branch with a clean working tree (or in an isolated worktree).
- You can run `uv sync` and `uv run pytest` successfully on the existing 256-test suite.
- You have read the spec file referenced above in full.
- The pre-existing 6d files are intact: `src/project0/intelligence/`, `src/project0/agents/intelligence.py`, `prompts/intelligence.toml`, `prompts/intelligence.md`.

Key anchors you will touch:

| File | Lines | What lives there today |
|---|---|---|
| `src/project0/main.py` | 150 | `reports_dir = Path("data/intelligence/reports")` |
| `src/project0/main.py` | 166–176 | `Intelligence(...)` construction call |
| `src/project0/main.py` | 239–259 | `async with asyncio.TaskGroup() as tg:` + bot pollers + `stop_event` |
| `src/project0/agents/intelligence.py` | 123–132 | `IntelligenceConfig` dataclass |
| `src/project0/agents/intelligence.py` | 154–163 | `load_intelligence_config` body |
| `src/project0/agents/intelligence.py` | 210–232 | `Intelligence.__init__` |
| `src/project0/agents/intelligence.py` | 234–266 | `_build_tool_specs` |
| `src/project0/agents/intelligence.py` | 285–340 | `_dispatch_tool_inner` tool-name if-chain |
| `src/project0/intelligence/generate.py` | 33–94 | `generate_daily_report` signature + `llm.complete` call |
| `src/project0/llm/provider.py` | 41–46 | `ProviderCall` dataclass |
| `src/project0/llm/provider.py` | 48–56 | `LLMProvider.complete` protocol |
| `src/project0/llm/provider.py` | 82–103 | `FakeProvider.complete` |
| `src/project0/llm/provider.py` | 145–178 | `AnthropicProvider.complete` |

---

## Task 0: Dependencies, gitignore, package skeleton, TOML additions

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `src/project0/intelligence_web/__init__.py`
- Create: `src/project0/intelligence_web/templates/` (directory)
- Create: `src/project0/intelligence_web/static/` (directory)
- Create: `tests/intelligence_web/__init__.py`
- Modify: `prompts/intelligence.toml`

- [ ] **Step 1: Add new runtime dependencies**

Run:
```bash
uv add "fastapi>=0.115" "uvicorn[standard]>=0.32" "jinja2>=3.1"
```

Expected: `uv add` updates `pyproject.toml` and `uv.lock`, installs packages in <10 seconds. No build errors.

- [ ] **Step 2: Verify deps installed**

Run:
```bash
uv run python -c "import fastapi, uvicorn, jinja2; print(fastapi.__version__, uvicorn.__version__, jinja2.__version__)"
```

Expected: three version strings printed, no ImportError.

- [ ] **Step 3: Add feedback dir to `.gitignore`**

Edit `.gitignore`. Find the block:
```
# Intelligence agent runtime data (6d)
data/intelligence/reports/
```

Append immediately after it:
```

# Intelligence webapp feedback log (6e)
data/intelligence/feedback/
```

- [ ] **Step 4: Create the empty package skeleton**

Run:
```bash
mkdir -p src/project0/intelligence_web/templates
mkdir -p src/project0/intelligence_web/static
mkdir -p tests/intelligence_web
```

Create `src/project0/intelligence_web/__init__.py` with contents:
```python
"""Intelligence agent delivery surface — FastAPI webapp rendering daily
reports with history browsing and thumbs feedback capture (6e)."""
```

Create `tests/intelligence_web/__init__.py` as an empty file (zero bytes).

- [ ] **Step 5: Extend `prompts/intelligence.toml` with new fields**

In `prompts/intelligence.toml`, locate the `[llm.summarizer]` section. It currently reads something like:
```toml
[llm.summarizer]
model = "claude-opus-4-6"
max_tokens = 16384
```

Replace that section with:
```toml
[llm.summarizer]
model = "claude-opus-4-6"
max_tokens = 32768
thinking_budget_tokens = 16384
```

Then append a new `[web]` section at the end of the file (after the existing `[[watch]]` entries):
```toml

[web]
public_base_url = "http://localhost:8080"
bind_host = "0.0.0.0"
bind_port = 8080
reports_dir = "data/intelligence/reports"
feedback_dir = "data/intelligence/feedback"
user_tz = "Asia/Shanghai"
```

The `public_base_url` is set to `http://localhost:8080` as a dev-friendly default; the user will override to their Tailscale hostname in their own environment.

- [ ] **Step 6: Verify existing test suite still passes**

Run:
```bash
uv run pytest -q
```

Expected: 256 tests pass (or whatever the current count is). Nothing we've touched yet affects runtime code.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .gitignore src/project0/intelligence_web tests/intelligence_web prompts/intelligence.toml
git commit -m "scaffold(6e): deps, package skeleton, intelligence.toml [web] section"
```

---

## Task 1: `WebConfig` dataclass

**Files:**
- Create: `src/project0/intelligence_web/config.py`
- Create: `tests/intelligence_web/test_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/intelligence_web/test_config.py`:
```python
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

from project0.intelligence_web.config import WebConfig


def test_valid_full_config() -> None:
    section = {
        "public_base_url": "http://intel.tailnet.ts.net:8080",
        "bind_host": "127.0.0.1",
        "bind_port": 9000,
        "reports_dir": "/tmp/reports",
        "feedback_dir": "/tmp/feedback",
        "user_tz": "UTC",
    }
    cfg = WebConfig.from_toml_section(section)
    assert cfg.public_base_url == "http://intel.tailnet.ts.net:8080"
    assert cfg.bind_host == "127.0.0.1"
    assert cfg.bind_port == 9000
    assert cfg.reports_dir == Path("/tmp/reports")
    assert cfg.feedback_dir == Path("/tmp/feedback")
    assert cfg.user_tz == ZoneInfo("UTC")


def test_valid_minimal_config_applies_defaults() -> None:
    section = {"public_base_url": "http://localhost:8080"}
    cfg = WebConfig.from_toml_section(section)
    assert cfg.public_base_url == "http://localhost:8080"
    assert cfg.bind_host == "0.0.0.0"
    assert cfg.bind_port == 8080
    assert cfg.reports_dir == Path("data/intelligence/reports")
    assert cfg.feedback_dir == Path("data/intelligence/feedback")
    assert cfg.user_tz == ZoneInfo("Asia/Shanghai")


def test_rejects_base_url_without_scheme() -> None:
    with pytest.raises(RuntimeError, match="public_base_url"):
        WebConfig.from_toml_section({"public_base_url": "intel.ts.net:8080"})


def test_rejects_base_url_with_ftp_scheme() -> None:
    with pytest.raises(RuntimeError, match="public_base_url"):
        WebConfig.from_toml_section({"public_base_url": "ftp://intel.ts.net/"})


def test_rejects_missing_public_base_url() -> None:
    with pytest.raises(KeyError):
        WebConfig.from_toml_section({})


def test_accepts_https_base_url() -> None:
    cfg = WebConfig.from_toml_section(
        {"public_base_url": "https://intel.example.com"}
    )
    assert cfg.public_base_url == "https://intel.example.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/intelligence_web/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'project0.intelligence_web.config'`.

- [ ] **Step 3: Implement `WebConfig`**

Create `src/project0/intelligence_web/config.py`:
```python
"""Web config loaded from prompts/intelligence.toml's [web] section.

Shared between the Intelligence agent (which uses `public_base_url` to build
report-page URLs in `get_report_link`) and the webapp (which uses all fields
for binding and filesystem access). Loaded once at startup in main.py so both
consumers see the same values."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class WebConfig:
    public_base_url: str
    bind_host: str
    bind_port: int
    reports_dir: Path
    feedback_dir: Path
    user_tz: ZoneInfo

    @classmethod
    def from_toml_section(cls, section: dict[str, Any]) -> "WebConfig":
        public_base_url = section["public_base_url"]
        if not public_base_url.startswith(("http://", "https://")):
            raise RuntimeError(
                f"[web].public_base_url must start with http:// or https://, "
                f"got {public_base_url!r}"
            )
        return cls(
            public_base_url=public_base_url,
            bind_host=section.get("bind_host", "0.0.0.0"),
            bind_port=int(section.get("bind_port", 8080)),
            reports_dir=Path(section.get("reports_dir", "data/intelligence/reports")),
            feedback_dir=Path(section.get("feedback_dir", "data/intelligence/feedback")),
            user_tz=ZoneInfo(section.get("user_tz", "Asia/Shanghai")),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/intelligence_web/test_config.py -v
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/project0/intelligence_web/config.py tests/intelligence_web/test_config.py
git commit -m "feat(6e): WebConfig dataclass with TOML loader"
```

---

## Task 2: `FeedbackEvent` dataclass + `append_thumbs`

**Files:**
- Create: `src/project0/intelligence_web/feedback.py`
- Create: `tests/intelligence_web/test_feedback_append.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/intelligence_web/test_feedback_append.py`:
```python
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from project0.intelligence_web.feedback import FeedbackEvent, append_thumbs


TZ = ZoneInfo("Asia/Shanghai")


def _make_event(item_id: str = "n1", score: int = 1) -> FeedbackEvent:
    return FeedbackEvent.thumbs(
        report_date="2026-04-15",
        item_id=item_id,
        score=score,  # type: ignore[arg-type]
        tz=TZ,
    )


def test_writes_one_line_per_event(tmp_path: Path) -> None:
    fb_dir = tmp_path / "feedback"
    for i in range(3):
        append_thumbs(_make_event(item_id=f"n{i}"), fb_dir)
    files = list(fb_dir.glob("*.jsonl"))
    assert len(files) == 1
    lines = [line for line in files[0].read_text().splitlines() if line]
    assert len(lines) == 3


def test_uses_monthly_filename(tmp_path: Path) -> None:
    fb_dir = tmp_path / "feedback"
    # Force a known ts via direct dataclass construction instead of .thumbs()
    evt = FeedbackEvent(
        ts=datetime(2026, 4, 15, 12, 0, tzinfo=TZ),
        type="thumbs",
        report_date="2026-04-15",
        item_id="n1",
        score=1,
    )
    append_thumbs(evt, fb_dir)
    assert (fb_dir / "2026-04.jsonl").exists()


def test_creates_feedback_dir_if_missing(tmp_path: Path) -> None:
    fb_dir = tmp_path / "does" / "not" / "exist"
    assert not fb_dir.exists()
    append_thumbs(_make_event(), fb_dir)
    assert fb_dir.exists()


def test_appends_to_existing_file(tmp_path: Path) -> None:
    fb_dir = tmp_path / "feedback"
    append_thumbs(_make_event("n1"), fb_dir)
    append_thumbs(_make_event("n2"), fb_dir)
    append_thumbs(_make_event("n3"), fb_dir)
    files = list(fb_dir.glob("*.jsonl"))
    assert len(files) == 1
    lines = [line for line in files[0].read_text().splitlines() if line]
    assert len(lines) == 3


def test_line_is_valid_json(tmp_path: Path) -> None:
    fb_dir = tmp_path / "feedback"
    append_thumbs(_make_event(item_id="n7", score=-1), fb_dir)
    files = list(fb_dir.glob("*.jsonl"))
    content = files[0].read_text().strip()
    parsed = json.loads(content)
    assert parsed["type"] == "thumbs"
    assert parsed["report_date"] == "2026-04-15"
    assert parsed["item_id"] == "n7"
    assert parsed["score"] == -1
    assert "ts" in parsed


def test_unicode_preserved(tmp_path: Path) -> None:
    fb_dir = tmp_path / "feedback"
    append_thumbs(_make_event(item_id="条目一"), fb_dir)
    files = list(fb_dir.glob("*.jsonl"))
    content = files[0].read_text(encoding="utf-8").strip()
    assert "条目一" in content


def test_event_has_server_side_timestamp() -> None:
    evt = FeedbackEvent.thumbs(
        report_date="2026-04-15", item_id="n1", score=1, tz=TZ
    )
    assert evt.ts.tzinfo is not None
    assert evt.ts.year == 2026 or evt.ts.year >= 2026  # sanity
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/intelligence_web/test_feedback_append.py -v
```

Expected: `ModuleNotFoundError: No module named 'project0.intelligence_web.feedback'`.

- [ ] **Step 3: Implement `FeedbackEvent` and `append_thumbs`**

Create `src/project0/intelligence_web/feedback.py`:
```python
"""Thumbs feedback event schema and append-only JSONL storage (6e).

Single event type in 6e: `thumbs` with score in {-1, 0, 1}. Events are
append-only, one JSON object per line, in monthly rollover files under
`data/intelligence/feedback/YYYY-MM.jsonl`. Nothing in the Intelligence
agent or generation pipeline reads these events back — 6e captures signal
only; preference learning is a later sub-project."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ThumbsScore = Literal[-1, 0, 1]


@dataclass(frozen=True)
class FeedbackEvent:
    ts: datetime                     # timezone-aware
    type: Literal["thumbs"]
    report_date: str                 # YYYY-MM-DD
    item_id: str
    score: ThumbsScore

    @classmethod
    def thumbs(
        cls,
        *,
        report_date: str,
        item_id: str,
        score: ThumbsScore,
        tz: ZoneInfo,
    ) -> "FeedbackEvent":
        return cls(
            ts=datetime.now(tz=tz),
            type="thumbs",
            report_date=report_date,
            item_id=item_id,
            score=score,
        )

    def to_jsonl_line(self) -> str:
        payload = {
            "ts": self.ts.isoformat(),
            "type": self.type,
            "report_date": self.report_date,
            "item_id": self.item_id,
            "score": self.score,
        }
        return json.dumps(payload, ensure_ascii=False) + "\n"


def append_thumbs(event: FeedbackEvent, feedback_dir: Path) -> None:
    """Append one thumbs event to the monthly JSONL file. Atomic at the
    POSIX level for writes below PIPE_BUF (4KB); a single thumbs line is
    ~120 bytes so this is safe without explicit locking at single-writer
    scale. fsyncs before returning so the client can treat a 200 response
    as a durability signal."""
    feedback_dir.mkdir(parents=True, exist_ok=True)
    month = event.ts.strftime("%Y-%m")
    path = feedback_dir / f"{month}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(event.to_jsonl_line())
        f.flush()
        os.fsync(f.fileno())
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/intelligence_web/test_feedback_append.py -v
```

Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/project0/intelligence_web/feedback.py tests/intelligence_web/test_feedback_append.py
git commit -m "feat(6e): FeedbackEvent + append_thumbs JSONL writer"
```

---

## Task 3: `load_thumbs_state_for` — deriving current state from history

**Files:**
- Modify: `src/project0/intelligence_web/feedback.py`
- Create: `tests/intelligence_web/test_feedback_load_state.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/intelligence_web/test_feedback_load_state.py`:
```python
from pathlib import Path
from zoneinfo import ZoneInfo

from project0.intelligence_web.feedback import (
    FeedbackEvent,
    append_thumbs,
    load_thumbs_state_for,
)

TZ = ZoneInfo("Asia/Shanghai")


def _append(fb_dir: Path, report_date: str, item_id: str, score: int) -> None:
    append_thumbs(
        FeedbackEvent.thumbs(
            report_date=report_date,
            item_id=item_id,
            score=score,  # type: ignore[arg-type]
            tz=TZ,
        ),
        fb_dir,
    )


def test_empty_dir_returns_empty_state(tmp_path: Path) -> None:
    assert load_thumbs_state_for("2026-04-15", tmp_path / "nope") == {}


def test_single_thumbs_up(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    _append(fb, "2026-04-15", "n1", 1)
    assert load_thumbs_state_for("2026-04-15", fb) == {"n1": 1}


def test_latest_write_wins(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    _append(fb, "2026-04-15", "n1", 1)
    _append(fb, "2026-04-15", "n1", -1)
    assert load_thumbs_state_for("2026-04-15", fb) == {"n1": -1}


def test_score_zero_clears_entry(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    _append(fb, "2026-04-15", "n1", 1)
    _append(fb, "2026-04-15", "n1", 0)
    assert load_thumbs_state_for("2026-04-15", fb) == {}


def test_filters_other_report_dates(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    _append(fb, "2026-04-14", "n1", 1)
    _append(fb, "2026-04-15", "n2", -1)
    assert load_thumbs_state_for("2026-04-15", fb) == {"n2": -1}


def test_reads_current_and_previous_month(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    fb.mkdir()
    # Hand-seed a March file containing an event for a March report, plus
    # an April file containing another event for the same March report.
    (fb / "2026-03.jsonl").write_text(
        '{"ts":"2026-03-31T23:00:00+08:00","type":"thumbs","report_date":"2026-03-31","item_id":"n1","score":1}\n',
        encoding="utf-8",
    )
    (fb / "2026-04.jsonl").write_text(
        '{"ts":"2026-04-01T09:00:00+08:00","type":"thumbs","report_date":"2026-03-31","item_id":"n2","score":-1}\n',
        encoding="utf-8",
    )
    state = load_thumbs_state_for("2026-03-31", fb)
    assert state == {"n1": 1, "n2": -1}


def test_skips_corrupt_lines(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    fb.mkdir()
    (fb / "2026-04.jsonl").write_text(
        '{"ts":"2026-04-15T10:00:00+08:00","type":"thumbs","report_date":"2026-04-15","item_id":"n1","score":1}\n'
        '{broken json here\n'
        '{"ts":"2026-04-15T11:00:00+08:00","type":"thumbs","report_date":"2026-04-15","item_id":"n2","score":-1}\n',
        encoding="utf-8",
    )
    state = load_thumbs_state_for("2026-04-15", fb)
    assert state == {"n1": 1, "n2": -1}


def test_skips_unknown_event_type(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    fb.mkdir()
    (fb / "2026-04.jsonl").write_text(
        '{"ts":"2026-04-15T10:00:00+08:00","type":"mute_topic","report_date":"2026-04-15","topic":"crypto"}\n'
        '{"ts":"2026-04-15T11:00:00+08:00","type":"thumbs","report_date":"2026-04-15","item_id":"n1","score":1}\n',
        encoding="utf-8",
    )
    assert load_thumbs_state_for("2026-04-15", fb) == {"n1": 1}


def test_skips_events_with_invalid_score(tmp_path: Path) -> None:
    fb = tmp_path / "feedback"
    fb.mkdir()
    (fb / "2026-04.jsonl").write_text(
        '{"ts":"2026-04-15T10:00:00+08:00","type":"thumbs","report_date":"2026-04-15","item_id":"n1","score":5}\n'
        '{"ts":"2026-04-15T11:00:00+08:00","type":"thumbs","report_date":"2026-04-15","item_id":"n2","score":1}\n',
        encoding="utf-8",
    )
    assert load_thumbs_state_for("2026-04-15", fb) == {"n2": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/intelligence_web/test_feedback_load_state.py -v
```

Expected: `ImportError: cannot import name 'load_thumbs_state_for' from 'project0.intelligence_web.feedback'`.

- [ ] **Step 3: Implement `load_thumbs_state_for`**

Append to `src/project0/intelligence_web/feedback.py` (after `append_thumbs`):
```python
def _relevant_month_files(report_date: str, feedback_dir: Path) -> list[Path]:
    """Return the current-month and previous-month JSONL paths for a given
    report_date, in chronological order (oldest first). Doesn't check
    existence; the caller filters. Handles December→January rollover."""
    year, month, _ = report_date.split("-")
    year_i, month_i = int(year), int(month)
    prev_year, prev_month = (year_i - 1, 12) if month_i == 1 else (year_i, month_i - 1)
    return [
        feedback_dir / f"{prev_year:04d}-{prev_month:02d}.jsonl",
        feedback_dir / f"{year_i:04d}-{month_i:02d}.jsonl",
        # Also include the next month to handle the "view March report in
        # April and click a thumb" edge case. The click event stamps its ts
        # as April but references a March report_date.
        *(
            [feedback_dir / f"{year_i:04d}-{month_i + 1:02d}.jsonl"]
            if month_i < 12
            else [feedback_dir / f"{year_i + 1:04d}-01.jsonl"]
        ),
    ]


def load_thumbs_state_for(
    report_date: str, feedback_dir: Path
) -> dict[str, int]:
    """Return current thumbs state for a given report_date as {item_id: score}.
    Only items with a non-zero current score are included.

    Scans the previous, current, and next month's JSONL files to cover all
    edge cases around month-boundary clicks. Uses latest-write-wins ordering
    (file order within a file equals chronological order because append
    happens at write-time with a monotonic ts)."""
    state: dict[str, int] = {}
    for path in _relevant_month_files(report_date, feedback_dir):
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("skipping corrupt feedback line in %s", path)
                    continue
                if evt.get("type") != "thumbs":
                    continue
                if evt.get("report_date") != report_date:
                    continue
                item_id = evt.get("item_id")
                score = evt.get("score")
                if not isinstance(item_id, str) or score not in (-1, 0, 1):
                    continue
                state[item_id] = score
    return {k: v for k, v in state.items() if v != 0}
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/intelligence_web/test_feedback_load_state.py -v
```

Expected: 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/project0/intelligence_web/feedback.py tests/intelligence_web/test_feedback_load_state.py
git commit -m "feat(6e): load_thumbs_state_for derives current state from history"
```

---

## Task 4: `rendering.py` — context builder and Jinja2 filters

**Files:**
- Create: `src/project0/intelligence_web/rendering.py`
- Create: `tests/intelligence_web/test_rendering.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/intelligence_web/test_rendering.py`:
```python
from datetime import date
from zoneinfo import ZoneInfo

from project0.intelligence_web.rendering import (
    build_report_context,
    format_time,
    groupby_month,
    sort_by_importance,
)

TZ = ZoneInfo("Asia/Shanghai")


def _report(items: list[dict]) -> dict:
    return {
        "date": "2026-04-15",
        "generated_at": "2026-04-15T08:03:22+08:00",
        "user_tz": "Asia/Shanghai",
        "watchlist_snapshot": ["openai"],
        "news_items": items,
        "suggested_accounts": [],
        "stats": {
            "tweets_fetched": 100,
            "handles_attempted": 1,
            "handles_succeeded": 1,
            "items_generated": len(items),
            "errors": [],
        },
    }


def test_sort_by_importance_orders_high_medium_low() -> None:
    items = [
        {"id": "a", "importance": "low"},
        {"id": "b", "importance": "high"},
        {"id": "c", "importance": "medium"},
        {"id": "d", "importance": "high"},
    ]
    out = sort_by_importance(items)
    assert [i["id"] for i in out] == ["b", "d", "c", "a"]


def test_sort_by_importance_is_stable_within_bucket() -> None:
    items = [
        {"id": "x", "importance": "medium"},
        {"id": "y", "importance": "medium"},
        {"id": "z", "importance": "medium"},
    ]
    assert [i["id"] for i in sort_by_importance(items)] == ["x", "y", "z"]


def test_format_time_includes_hours_ago_relative() -> None:
    out = format_time("2026-04-15T08:03:22+08:00", user_tz=TZ, now=None)
    assert "08:03" in out


def test_groupby_month_groups_descending_dates() -> None:
    dates = [date(2026, 4, 15), date(2026, 4, 1), date(2026, 3, 20)]
    grouped = groupby_month(dates)
    assert [month for month, _ in grouped] == ["2026-04", "2026-03"]
    assert grouped[0][1] == [date(2026, 4, 15), date(2026, 4, 1)]
    assert grouped[1][1] == [date(2026, 3, 20)]


def test_build_context_sets_current_and_feedback_fields() -> None:
    report = _report([{"id": "n1", "importance": "high"}])
    ctx = build_report_context(
        report_dict=report,
        feedback_state={"n1": 1},
        all_dates=[date(2026, 4, 15)],
        current=date(2026, 4, 15),
        public_base_url="http://test.local",
    )
    assert ctx["current_date"] == "2026-04-15"
    assert ctx["feedback"] == {"n1": 1}
    assert ctx["public_base_url"] == "http://test.local"
    assert ctx["prev_href"] is None
    assert ctx["next_href"] is None


def test_build_context_prev_next_hrefs_middle_of_list() -> None:
    # all_dates sorted descending (newest first): 17, 16, 15
    # current = 16 → prev (older) = 15, next (newer) = 17
    ctx = build_report_context(
        report_dict=_report([]),
        feedback_state={},
        all_dates=[date(2026, 4, 17), date(2026, 4, 16), date(2026, 4, 15)],
        current=date(2026, 4, 16),
        public_base_url="http://test.local",
    )
    assert ctx["prev_href"] == "/reports/2026-04-15"
    assert ctx["next_href"] == "/reports/2026-04-17"


def test_build_context_prev_none_for_oldest_date() -> None:
    ctx = build_report_context(
        report_dict=_report([]),
        feedback_state={},
        all_dates=[date(2026, 4, 17), date(2026, 4, 16), date(2026, 4, 15)],
        current=date(2026, 4, 15),
        public_base_url="http://test.local",
    )
    assert ctx["prev_href"] is None
    assert ctx["next_href"] == "/reports/2026-04-16"


def test_build_context_next_none_for_newest_date() -> None:
    ctx = build_report_context(
        report_dict=_report([]),
        feedback_state={},
        all_dates=[date(2026, 4, 17), date(2026, 4, 16)],
        current=date(2026, 4, 17),
        public_base_url="http://test.local",
    )
    assert ctx["next_href"] is None
    assert ctx["prev_href"] == "/reports/2026-04-16"


def test_build_context_all_dates_are_iso_strings() -> None:
    ctx = build_report_context(
        report_dict=_report([]),
        feedback_state={},
        all_dates=[date(2026, 4, 17), date(2026, 4, 16)],
        current=date(2026, 4, 17),
        public_base_url="http://test.local",
    )
    assert ctx["all_dates"] == ["2026-04-17", "2026-04-16"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/intelligence_web/test_rendering.py -v
```

Expected: `ModuleNotFoundError: No module named 'project0.intelligence_web.rendering'`.

- [ ] **Step 3: Implement `rendering.py`**

Create `src/project0/intelligence_web/rendering.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/intelligence_web/test_rendering.py -v
```

Expected: 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/project0/intelligence_web/rendering.py tests/intelligence_web/test_rendering.py
git commit -m "feat(6e): rendering adapter (sort, format, group, context)"
```

---

## Task 5: FastAPI app factory + base template + static CSS

**Files:**
- Create: `src/project0/intelligence_web/app.py`
- Create: `src/project0/intelligence_web/routes.py`
- Create: `src/project0/intelligence_web/templates/base.html`
- Create: `src/project0/intelligence_web/templates/empty.html`
- Create: `src/project0/intelligence_web/static/style.css`
- Create: `tests/intelligence_web/conftest.py`
- Create: `tests/intelligence_web/test_app_factory.py`
- Create: `tests/intelligence_web/test_routes_errors.py`

- [ ] **Step 1: Write the shared conftest**

Create `tests/intelligence_web/conftest.py`:
```python
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from project0.intelligence_web.app import create_app
from project0.intelligence_web.config import WebConfig


@pytest.fixture
def tmp_reports_dir(tmp_path: Path) -> Path:
    d = tmp_path / "reports"
    d.mkdir()
    return d


@pytest.fixture
def tmp_feedback_dir(tmp_path: Path) -> Path:
    # Intentionally not created — feedback append should lazily create it.
    return tmp_path / "feedback"


@pytest.fixture
def web_config(tmp_reports_dir: Path, tmp_feedback_dir: Path) -> WebConfig:
    return WebConfig(
        public_base_url="http://test.local:8080",
        bind_host="127.0.0.1",
        bind_port=8080,
        reports_dir=tmp_reports_dir,
        feedback_dir=tmp_feedback_dir,
        user_tz=ZoneInfo("Asia/Shanghai"),
    )


@pytest.fixture
def client(web_config: WebConfig) -> TestClient:
    app = create_app(web_config)
    return TestClient(app)


@pytest.fixture
def sample_report() -> dict:
    return {
        "date": "2026-04-15",
        "generated_at": "2026-04-15T08:03:22+08:00",
        "user_tz": "Asia/Shanghai",
        "watchlist_snapshot": ["openai", "sama"],
        "news_items": [
            {
                "id": "n1",
                "headline": "OpenAI 发布 o5-mini",
                "summary": "推理延迟降低 40%，对 API 用户有直接影响。",
                "importance": "high",
                "importance_reason": "主流模型迭代",
                "topics": ["ai-models"],
                "source_tweets": [
                    {
                        "handle": "sama",
                        "url": "https://x.com/sama/status/1",
                        "text": "o5-mini is here",
                        "posted_at": "2026-04-15T03:00:00Z",
                    }
                ],
            },
            {
                "id": "n2",
                "headline": "DeepMind 记忆机制论文",
                "summary": "新架构在长上下文任务上优于 baseline。",
                "importance": "medium",
                "importance_reason": "研究进展",
                "topics": ["research"],
                "source_tweets": [
                    {
                        "handle": "googledeepmind",
                        "url": "https://x.com/googledeepmind/status/2",
                        "text": "Paper",
                        "posted_at": "2026-04-15T04:00:00Z",
                    }
                ],
            },
            {
                "id": "n3",
                "headline": "Anthropic 招聘",
                "summary": "招聘信息。",
                "importance": "low",
                "importance_reason": "常规",
                "topics": ["hr"],
                "source_tweets": [
                    {
                        "handle": "anthropicai",
                        "url": "https://x.com/anthropicai/status/3",
                        "text": "Hiring",
                        "posted_at": "2026-04-15T05:00:00Z",
                    }
                ],
            },
        ],
        "suggested_accounts": [
            {
                "handle": "noamgpt",
                "reason": "被 @sama 引用，连续多日讨论推理优化",
                "seen_in_items": ["n1"],
            }
        ],
        "stats": {
            "tweets_fetched": 100,
            "handles_attempted": 2,
            "handles_succeeded": 2,
            "items_generated": 3,
            "errors": [],
        },
    }


def write_report(reports_dir: Path, report: dict) -> Path:
    path = reports_dir / f"{report['date']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def write_report_fn():
    return write_report
```

- [ ] **Step 2: Write the failing app factory and errors tests**

Create `tests/intelligence_web/test_app_factory.py`:
```python
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from project0.intelligence_web.app import create_app
from project0.intelligence_web.config import WebConfig


def test_create_app_returns_fastapi_instance(tmp_path: Path) -> None:
    cfg = WebConfig(
        public_base_url="http://test.local",
        bind_host="127.0.0.1",
        bind_port=8080,
        reports_dir=tmp_path / "reports",
        feedback_dir=tmp_path / "feedback",
        user_tz=ZoneInfo("UTC"),
    )
    app = create_app(cfg)
    assert isinstance(app, FastAPI)


def test_create_app_with_nonexistent_dirs_still_constructs(tmp_path: Path) -> None:
    cfg = WebConfig(
        public_base_url="http://test.local",
        bind_host="127.0.0.1",
        bind_port=8080,
        reports_dir=tmp_path / "not-created",
        feedback_dir=tmp_path / "also-not-created",
        user_tz=ZoneInfo("UTC"),
    )
    # Should NOT raise — directories are read lazily per request.
    app = create_app(cfg)
    client = TestClient(app)
    # healthz should work regardless of directory state
    assert client.get("/healthz").status_code == 200
```

Create `tests/intelligence_web/test_routes_errors.py`:
```python
from fastapi.testclient import TestClient


def test_healthz_returns_200_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_static_css_served(client: TestClient) -> None:
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]


def test_unknown_route_returns_404(client: TestClient) -> None:
    resp = client.get("/not-a-real-path")
    assert resp.status_code == 404
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/intelligence_web/test_app_factory.py tests/intelligence_web/test_routes_errors.py -v
```

Expected: `ModuleNotFoundError: No module named 'project0.intelligence_web.app'`.

- [ ] **Step 4: Implement `routes.py` with healthz only (report/history/thumbs come in later tasks)**

Create `src/project0/intelligence_web/routes.py`:
```python
"""FastAPI routes for the Intelligence webapp (6e).

Kept thin — handlers read config via Depends, call into feedback/rendering
modules for real work, and delegate HTML generation to Jinja2 templates."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter()


@router.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"
```

- [ ] **Step 5: Implement `app.py` factory**

Create `src/project0/intelligence_web/app.py`:
```python
"""FastAPI app factory for the Intelligence webapp (6e).

Construction is deferred into a factory (`create_app(config)`) so tests can
build isolated app instances with tmp directories, and so composition-root
(`main.py`) has a single entry point to call after loading `WebConfig`."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from project0.intelligence_web import routes
from project0.intelligence_web.config import WebConfig
from project0.intelligence_web.rendering import (
    format_time,
    groupby_month,
    sort_by_importance,
)

_PACKAGE_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"


def create_app(config: WebConfig) -> FastAPI:
    app = FastAPI(
        title="Intelligence Webapp",
        description="Reading surface for Intelligence daily reports (6e).",
    )

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    # Filters need the user's tz closed over
    templates.env.filters["sort_by_importance"] = sort_by_importance
    templates.env.filters["groupby_month"] = groupby_month
    templates.env.filters["format_time"] = lambda s: format_time(
        s, user_tz=config.user_tz, now=None
    )

    app.state.config = config
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(routes.router)
    return app
```

- [ ] **Step 6: Create minimal `base.html`, `empty.html`, and `style.css` so static serving works**

Create `src/project0/intelligence_web/templates/base.html`:
```html
<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Intelligence{% endblock %}</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header class="site-header">
    <h1 class="site-title"><a href="/">Intelligence</a></h1>
    <nav class="site-nav">
      <a href="/">Latest</a> ·
      <a href="/history">History</a>
    </nav>
  </header>
  <main class="site-main">
    {% block content %}{% endblock %}
  </main>
  <footer class="site-footer">
    <p>顾瑾 · Intelligence agent</p>
  </footer>
  <script src="/static/thumbs.js" defer></script>
</body>
</html>
```

Create `src/project0/intelligence_web/templates/empty.html`:
```html
{% extends "base.html" %}
{% block title %}No reports yet · Intelligence{% endblock %}
{% block content %}
<section class="empty-state">
  <h2>No reports yet</h2>
  <p>Ask 顾瑾 on Telegram to generate your first daily report.</p>
</section>
{% endblock %}
```

Create `src/project0/intelligence_web/static/style.css`:
```css
:root {
  --bg: #0e0e12;
  --fg: #e6e6ea;
  --muted: #888894;
  --accent: #64b4ff;
  --high: #ff7850;
  --medium: #ffc850;
  --low: #666670;
  --card-bg: #16161d;
  --border: #2a2a34;
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg); }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue",
               "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 16px;
  line-height: 1.6;
}

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

.site-header {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 12px;
}
.site-title { margin: 0; font-size: 20px; }
.site-title a { color: var(--fg); }
.site-nav { color: var(--muted); font-size: 14px; }

.site-main {
  max-width: 780px;
  margin: 0 auto;
  padding: 20px 16px 60px;
}

.site-footer {
  text-align: center;
  padding: 20px;
  color: var(--muted);
  font-size: 13px;
  border-top: 1px solid var(--border);
}

.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--muted);
}
.empty-state h2 { color: var(--fg); }

/* Date navigator */
.date-nav {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 20px;
}
.date-nav select {
  flex: 1;
  background: transparent;
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 6px 10px;
  font-size: 15px;
  text-align: center;
}
.nav-btn {
  display: inline-block;
  padding: 6px 14px;
  background: var(--border);
  border-radius: 4px;
  color: var(--fg);
  font-weight: 600;
}
.nav-btn.disabled { opacity: 0.3; pointer-events: none; }

/* Report header */
.report-header { margin-bottom: 24px; }
.report-header h1 { margin: 0 0 4px; font-size: 24px; }
.report-header .meta { color: var(--muted); font-size: 13px; margin: 0; }
.report-header .err { color: var(--high); }

/* News items */
.news-items { display: flex; flex-direction: column; gap: 16px; }
.item {
  padding: 14px 16px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  border-left-width: 4px;
}
.item.importance-high { border-left-color: var(--high); }
.item.importance-medium { border-left-color: var(--medium); }
.item.importance-low { border-left-color: var(--low); }
.item-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.item-header h2 { margin: 0; font-size: 17px; }
.importance-badge {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 700;
  letter-spacing: 0.5px;
}
.item.importance-high .importance-badge {
  background: rgba(255, 120, 80, 0.2);
  color: var(--high);
}
.item.importance-medium .importance-badge {
  background: rgba(255, 200, 80, 0.2);
  color: var(--medium);
}
.item.importance-low .importance-badge {
  background: rgba(150, 150, 160, 0.2);
  color: var(--muted);
}
.item .summary { margin: 6px 0; }
.item .reason { color: var(--muted); font-size: 13px; margin: 4px 0; }
.item .sources {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 13px;
}

/* Thumbs */
.thumbs { margin-top: 10px; display: flex; gap: 8px; }
.thumbs button {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--fg);
  padding: 6px 14px;
  border-radius: 4px;
  font-size: 15px;
  cursor: pointer;
}
.thumbs button.active { background: var(--accent); border-color: var(--accent); }

/* Suggested accounts */
.suggested { margin-top: 32px; }
.suggested h2 { font-size: 16px; color: var(--muted); letter-spacing: 0.5px; }
.suggested-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px;
}
.suggested-card {
  padding: 12px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--fg);
}
.suggested-card:hover { border-color: var(--accent); text-decoration: none; }
.suggested-card .handle { font-weight: 600; margin-bottom: 4px; }
.suggested-card .reason { color: var(--muted); font-size: 13px; }
.suggested-card .cue { color: var(--accent); font-size: 12px; margin-top: 6px; }

/* History page */
.history-list { list-style: none; padding: 0; }
.history-month { margin-bottom: 20px; }
.history-month h3 {
  color: var(--muted);
  font-size: 13px;
  letter-spacing: 0.5px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 4px;
  margin-bottom: 8px;
}
.history-month ul { list-style: none; padding: 0; }
.history-month li { padding: 4px 0; }
```

Also create a placeholder `src/project0/intelligence_web/static/thumbs.js` with contents `// populated in Task 8`:
```javascript
// populated in Task 8
```

- [ ] **Step 7: Run the tests**

Run:
```bash
uv run pytest tests/intelligence_web/test_app_factory.py tests/intelligence_web/test_routes_errors.py -v
```

Expected: 5 tests pass (2 from factory, 3 from errors).

- [ ] **Step 8: Commit**

```bash
git add src/project0/intelligence_web/app.py src/project0/intelligence_web/routes.py \
        src/project0/intelligence_web/templates src/project0/intelligence_web/static \
        tests/intelligence_web/conftest.py tests/intelligence_web/test_app_factory.py \
        tests/intelligence_web/test_routes_errors.py
git commit -m "feat(6e): FastAPI app factory, base template, CSS, healthz"
```

---

## Task 6: `GET /` + `GET /reports/{date}` + `report.html`

**Files:**
- Modify: `src/project0/intelligence_web/routes.py`
- Create: `src/project0/intelligence_web/templates/report.html`
- Create: `src/project0/intelligence_web/templates/not_found.html`
- Create: `tests/intelligence_web/test_routes_report.py`

- [ ] **Step 1: Write the failing route tests**

Create `tests/intelligence_web/test_routes_report.py`:
```python
import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_root_returns_latest_report(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    # Write two reports on different dates
    newer = {**sample_report, "date": "2026-04-15"}
    older = {**sample_report, "date": "2026-04-10"}
    write_report_fn(tmp_reports_dir, newer)
    write_report_fn(tmp_reports_dir, older)

    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "2026-04-15" in body
    # The older report's specific headline mix is the same, but the date heading should be the newer one
    assert "OpenAI 发布 o5-mini" in body


def test_root_with_no_reports_returns_empty_html(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No reports yet" in resp.text


def test_get_report_by_date(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    write_report_fn(tmp_reports_dir, sample_report)
    resp = client.get("/reports/2026-04-15")
    assert resp.status_code == 200
    assert "OpenAI 发布 o5-mini" in resp.text
    assert "DeepMind 记忆机制论文" in resp.text
    assert "Anthropic 招聘" in resp.text


def test_get_nonexistent_date_returns_404(client: TestClient) -> None:
    resp = client.get("/reports/2099-01-01")
    assert resp.status_code == 404


def test_get_bad_date_format_returns_400(client: TestClient) -> None:
    resp = client.get("/reports/not-a-date")
    assert resp.status_code == 400


def test_items_rendered_in_importance_order(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    write_report_fn(tmp_reports_dir, sample_report)
    body = client.get("/reports/2026-04-15").text
    # High headline should appear before medium headline which should
    # appear before low headline
    high_idx = body.index("OpenAI 发布 o5-mini")
    med_idx = body.index("DeepMind 记忆机制论文")
    low_idx = body.index("Anthropic 招聘")
    assert high_idx < med_idx < low_idx


def test_source_tweet_links_rendered(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    write_report_fn(tmp_reports_dir, sample_report)
    body = client.get("/reports/2026-04-15").text
    assert 'href="https://x.com/sama/status/1"' in body
    assert 'href="https://x.com/googledeepmind/status/2"' in body


def test_suggested_accounts_rendered_with_x_links(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    write_report_fn(tmp_reports_dir, sample_report)
    body = client.get("/reports/2026-04-15").text
    assert 'href="https://x.com/noamgpt"' in body
    assert "被 @sama 引用" in body


def test_prev_next_hrefs_rendered_when_applicable(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    write_report_fn(tmp_reports_dir, {**sample_report, "date": "2026-04-14"})
    write_report_fn(tmp_reports_dir, {**sample_report, "date": "2026-04-15"})
    write_report_fn(tmp_reports_dir, {**sample_report, "date": "2026-04-16"})
    body = client.get("/reports/2026-04-15").text
    assert 'href="/reports/2026-04-14"' in body   # older (prev)
    assert 'href="/reports/2026-04-16"' in body   # newer (next)


def test_date_dropdown_contains_all_dates(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    for d in ("2026-04-12", "2026-04-13", "2026-04-14", "2026-04-15"):
        write_report_fn(tmp_reports_dir, {**sample_report, "date": d})
    body = client.get("/reports/2026-04-14").text
    for d in ("2026-04-12", "2026-04-13", "2026-04-14", "2026-04-15"):
        assert f'value="{d}"' in body


def test_feedback_state_reflected_in_rendered_buttons(
    client: TestClient,
    tmp_reports_dir: Path,
    tmp_feedback_dir: Path,
    sample_report: dict,
    write_report_fn,
) -> None:
    write_report_fn(tmp_reports_dir, sample_report)
    # Seed a thumbs-up on n1
    tmp_feedback_dir.mkdir()
    (tmp_feedback_dir / "2026-04.jsonl").write_text(
        '{"ts":"2026-04-15T10:00:00+08:00","type":"thumbs","report_date":"2026-04-15","item_id":"n1","score":1}\n',
        encoding="utf-8",
    )
    body = client.get("/reports/2026-04-15").text
    # The thumb-up button for n1 should carry the "active" class
    # Loose check: the substring 'data-item-id="n1"' should appear near 'active'
    assert 'data-item-id="n1"' in body
    assert "active" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/intelligence_web/test_routes_report.py -v
```

Expected: all tests fail with 404 or template errors.

- [ ] **Step 3: Implement the report routes**

Replace `src/project0/intelligence_web/routes.py` with:
```python
"""FastAPI routes for the Intelligence webapp (6e)."""

from __future__ import annotations

from datetime import date
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from project0.intelligence.report import list_report_dates, read_report
from project0.intelligence_web.config import WebConfig
from project0.intelligence_web.feedback import load_thumbs_state_for
from project0.intelligence_web.rendering import build_report_context

router = APIRouter()


def _cfg(request: Request) -> WebConfig:
    return request.app.state.config  # type: ignore[no-any-return]


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


def _render_report_page(
    request: Request, cfg: WebConfig, target: date
) -> HTMLResponse:
    report_path = cfg.reports_dir / f"{target.isoformat()}.json"
    if not report_path.exists():
        return HTMLResponse(
            _templates(request).get_template("not_found.html").render(
                {"request": request, "missing_date": target.isoformat()}
            ),
            status_code=404,
        )
    report_dict = read_report(report_path)
    all_dates = list_report_dates(cfg.reports_dir)
    feedback_state = load_thumbs_state_for(target.isoformat(), cfg.feedback_dir)
    ctx = build_report_context(
        report_dict=report_dict,
        feedback_state=feedback_state,
        all_dates=all_dates,
        current=target,
        public_base_url=cfg.public_base_url,
    )
    ctx["request"] = request
    return _templates(request).TemplateResponse("report.html", ctx)


@router.get("/", response_class=HTMLResponse)
async def root(request: Request) -> HTMLResponse:
    cfg = _cfg(request)
    dates = list_report_dates(cfg.reports_dir)
    if not dates:
        return _templates(request).TemplateResponse(
            "empty.html", {"request": request}
        )
    return _render_report_page(request, cfg, dates[0])


@router.get("/reports/{date_str}", response_class=HTMLResponse)
async def report_by_date(request: Request, date_str: str) -> HTMLResponse:
    cfg = _cfg(request)
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"bad date: {date_str}")
    return _render_report_page(request, cfg, target)


@router.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"
```

- [ ] **Step 4: Create `report.html` and `not_found.html`**

Create `src/project0/intelligence_web/templates/report.html`:
```html
{% extends "base.html" %}
{% block title %}{{ current_date }} · Intelligence{% endblock %}
{% block content %}

<nav class="date-nav">
  {% if prev_href %}
    <a href="{{ prev_href }}" class="nav-btn">‹</a>
  {% else %}
    <span class="nav-btn disabled">‹</span>
  {% endif %}
  <form class="date-picker" onsubmit="return false;">
    <select onchange="location.href='/reports/'+this.value">
      {% for d in all_dates %}
        <option value="{{ d }}" {% if d == current_date %}selected{% endif %}>{{ d }}</option>
      {% endfor %}
    </select>
  </form>
  {% if next_href %}
    <a href="{{ next_href }}" class="nav-btn">›</a>
  {% else %}
    <span class="nav-btn disabled">›</span>
  {% endif %}
</nav>

<header class="report-header">
  <h1>{{ report.date }}</h1>
  <p class="meta">
    Generated {{ report.generated_at | format_time }} ·
    {{ report.stats.items_generated }} items ·
    {{ report.stats.tweets_fetched }} tweets
    {% if report.stats.errors %}
      · <span class="err">{{ report.stats.errors | length }} fetch errors</span>
    {% endif %}
  </p>
</header>

<section class="news-items">
  {% for item in report.news_items | sort_by_importance %}
    <article class="item importance-{{ item.importance }}">
      <div class="item-header">
        <span class="importance-badge">{{ item.importance | upper }}</span>
        <h2>{{ item.headline }}</h2>
      </div>
      <p class="summary">{{ item.summary }}</p>
      {% if item.importance_reason %}
        <p class="reason">{{ item.importance_reason }}</p>
      {% endif %}
      {% if item.source_tweets %}
        <ul class="sources">
          {% for src in item.source_tweets %}
            <li><a href="{{ src.url }}" target="_blank" rel="noopener">@{{ src.handle }}</a></li>
          {% endfor %}
        </ul>
      {% endif %}
      <div class="thumbs"
           data-item-id="{{ item.id }}"
           data-report-date="{{ current_date }}">
        <button type="button"
                class="thumb-up{% if feedback.get(item.id) == 1 %} active{% endif %}">
          👍
        </button>
        <button type="button"
                class="thumb-down{% if feedback.get(item.id) == -1 %} active{% endif %}">
          👎
        </button>
      </div>
    </article>
  {% endfor %}
</section>

{% if report.suggested_accounts %}
<section class="suggested">
  <h2>Suggested accounts</h2>
  <div class="suggested-grid">
    {% for acc in report.suggested_accounts %}
      <a class="suggested-card"
         href="https://x.com/{{ acc.handle }}"
         target="_blank" rel="noopener">
        <div class="handle">@{{ acc.handle }}</div>
        <div class="reason">{{ acc.reason }}</div>
        <div class="cue">↗ open on X</div>
      </a>
    {% endfor %}
  </div>
</section>
{% endif %}

{% endblock %}
```

Create `src/project0/intelligence_web/templates/not_found.html`:
```html
{% extends "base.html" %}
{% block title %}No report · Intelligence{% endblock %}
{% block content %}
<section class="empty-state">
  <h2>No report for {{ missing_date }}</h2>
  <p>Ask 顾瑾 on Telegram to generate one, or check <a href="/history">the history page</a>.</p>
</section>
{% endblock %}
```

- [ ] **Step 5: Run report route tests**

Run:
```bash
uv run pytest tests/intelligence_web/test_routes_report.py -v
```

Expected: 11 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/project0/intelligence_web/routes.py \
        src/project0/intelligence_web/templates/report.html \
        src/project0/intelligence_web/templates/not_found.html \
        tests/intelligence_web/test_routes_report.py
git commit -m "feat(6e): GET / and GET /reports/{date} with report.html"
```

---

## Task 7: `GET /history` + `history.html`

**Files:**
- Modify: `src/project0/intelligence_web/routes.py`
- Create: `src/project0/intelligence_web/templates/history.html`
- Create: `tests/intelligence_web/test_routes_history.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/intelligence_web/test_routes_history.py`:
```python
from pathlib import Path
from fastapi.testclient import TestClient


def test_history_lists_all_dates_descending(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    for d in ("2026-04-10", "2026-04-12", "2026-04-15", "2026-03-30"):
        write_report_fn(tmp_reports_dir, {**sample_report, "date": d})
    body = client.get("/history").text
    # All four dates present
    for d in ("2026-04-10", "2026-04-12", "2026-04-15", "2026-03-30"):
        assert d in body
    # Descending order
    idx_15 = body.index("2026-04-15")
    idx_12 = body.index("2026-04-12")
    idx_10 = body.index("2026-04-10")
    idx_330 = body.index("2026-03-30")
    assert idx_15 < idx_12 < idx_10 < idx_330


def test_history_with_no_reports_shows_empty_message(client: TestClient) -> None:
    resp = client.get("/history")
    assert resp.status_code == 200
    assert "No reports yet" in resp.text or "no reports" in resp.text.lower()


def test_history_dates_link_to_report_pages(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    write_report_fn(tmp_reports_dir, {**sample_report, "date": "2026-04-15"})
    body = client.get("/history").text
    assert 'href="/reports/2026-04-15"' in body


def test_history_groups_by_month(
    client: TestClient, tmp_reports_dir: Path, sample_report: dict, write_report_fn
) -> None:
    for d in ("2026-04-15", "2026-04-10", "2026-03-30"):
        write_report_fn(tmp_reports_dir, {**sample_report, "date": d})
    body = client.get("/history").text
    assert "2026-04" in body
    assert "2026-03" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/intelligence_web/test_routes_history.py -v
```

Expected: all fail with 404.

- [ ] **Step 3: Add the history route**

Append to `src/project0/intelligence_web/routes.py`, after the `report_by_date` handler and before `healthz`:
```python
@router.get("/history", response_class=HTMLResponse)
async def history(request: Request) -> HTMLResponse:
    cfg = _cfg(request)
    dates = list_report_dates(cfg.reports_dir)
    return _templates(request).TemplateResponse(
        "history.html",
        {"request": request, "dates": dates},
    )
```

- [ ] **Step 4: Create `history.html`**

Create `src/project0/intelligence_web/templates/history.html`:
```html
{% extends "base.html" %}
{% block title %}History · Intelligence{% endblock %}
{% block content %}

<h2>All reports</h2>

{% if not dates %}
  <p class="empty-state">No reports yet.</p>
{% else %}
  {% for month_label, month_dates in dates | groupby_month %}
    <section class="history-month">
      <h3>{{ month_label }}</h3>
      <ul>
        {% for d in month_dates %}
          <li><a href="/reports/{{ d.isoformat() }}">{{ d.isoformat() }}</a></li>
        {% endfor %}
      </ul>
    </section>
  {% endfor %}
{% endif %}

{% endblock %}
```

- [ ] **Step 5: Run tests**

Run:
```bash
uv run pytest tests/intelligence_web/test_routes_history.py -v
```

Expected: 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/project0/intelligence_web/routes.py \
        src/project0/intelligence_web/templates/history.html \
        tests/intelligence_web/test_routes_history.py
git commit -m "feat(6e): GET /history with month-grouped listing"
```

---

## Task 8: `POST /api/feedback/thumbs` + `thumbs.js`

**Files:**
- Modify: `src/project0/intelligence_web/routes.py`
- Modify: `src/project0/intelligence_web/static/thumbs.js`
- Create: `tests/intelligence_web/test_routes_thumbs.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/intelligence_web/test_routes_thumbs.py`:
```python
import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_thumbs_up_writes_event_to_log(
    client: TestClient, tmp_feedback_dir: Path
) -> None:
    resp = client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n1", "score": 1},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    files = list(tmp_feedback_dir.glob("*.jsonl"))
    assert len(files) == 1
    line = files[0].read_text().strip()
    evt = json.loads(line)
    assert evt["item_id"] == "n1"
    assert evt["score"] == 1
    assert evt["type"] == "thumbs"
    assert evt["report_date"] == "2026-04-15"


def test_thumbs_down_writes_event(
    client: TestClient, tmp_feedback_dir: Path
) -> None:
    client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n2", "score": -1},
    )
    line = list(tmp_feedback_dir.glob("*.jsonl"))[0].read_text().strip()
    assert json.loads(line)["score"] == -1


def test_thumbs_zero_writes_clear_event(
    client: TestClient, tmp_feedback_dir: Path
) -> None:
    resp = client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n1", "score": 0},
    )
    assert resp.status_code == 200
    line = list(tmp_feedback_dir.glob("*.jsonl"))[0].read_text().strip()
    assert json.loads(line)["score"] == 0


def test_thumbs_invalid_score_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n1", "score": 5},
    )
    assert resp.status_code == 422


def test_thumbs_missing_field_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "score": 1},
    )
    assert resp.status_code == 422


def test_thumbs_bad_date_format_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/feedback/thumbs",
        json={"report_date": "not-a-date", "item_id": "n1", "score": 1},
    )
    assert resp.status_code == 422


def test_thumbs_unknown_report_date_still_accepted(
    client: TestClient, tmp_feedback_dir: Path
) -> None:
    # No report file exists; endpoint is write-only, doesn't validate.
    resp = client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2099-01-01", "item_id": "zz", "score": 1},
    )
    assert resp.status_code == 200
    assert list(tmp_feedback_dir.glob("*.jsonl"))


def test_thumbs_event_has_server_timestamp(
    client: TestClient, tmp_feedback_dir: Path
) -> None:
    client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n1", "score": 1},
    )
    line = list(tmp_feedback_dir.glob("*.jsonl"))[0].read_text().strip()
    evt = json.loads(line)
    assert "ts" in evt
    assert "T" in evt["ts"]   # ISO format
    assert "+" in evt["ts"] or "Z" in evt["ts"]   # tz-aware


def test_subsequent_thumbs_updates_derived_state(
    client: TestClient,
    tmp_reports_dir: Path,
    tmp_feedback_dir: Path,
    sample_report: dict,
    write_report_fn,
) -> None:
    write_report_fn(tmp_reports_dir, sample_report)
    # Thumbs up then down on n1
    client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n1", "score": 1},
    )
    client.post(
        "/api/feedback/thumbs",
        json={"report_date": "2026-04-15", "item_id": "n1", "score": -1},
    )
    body = client.get("/reports/2026-04-15").text
    # Find the thumbs block for n1 and check the down button is active
    assert 'data-item-id="n1"' in body
    # The thumb-down should be marked active; thumb-up should not
    # Loose substring check: 'thumb-down active' should appear
    assert "thumb-down active" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/intelligence_web/test_routes_thumbs.py -v
```

Expected: all fail (405 or 404 on POST to unknown route).

- [ ] **Step 3: Add the thumbs route**

At the top of `src/project0/intelligence_web/routes.py`, add to the imports:
```python
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from project0.intelligence_web.feedback import FeedbackEvent, append_thumbs
```

Add the payload model and handler. Insert after the `history` handler and before `healthz`:
```python
class ThumbsPayload(BaseModel):
    report_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    item_id: str = Field(min_length=1, max_length=64)
    score: int = Field(ge=-1, le=1)


@router.post("/api/feedback/thumbs")
async def post_thumbs(payload: ThumbsPayload, request: Request) -> JSONResponse:
    cfg = _cfg(request)
    event = FeedbackEvent.thumbs(
        report_date=payload.report_date,
        item_id=payload.item_id,
        score=payload.score,  # type: ignore[arg-type]
        tz=cfg.user_tz,
    )
    append_thumbs(event, cfg.feedback_dir)
    return JSONResponse({"ok": True})
```

- [ ] **Step 4: Populate `thumbs.js`**

Replace contents of `src/project0/intelligence_web/static/thumbs.js`:
```javascript
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".thumb-up, .thumb-down");
  if (!btn) return;
  const container = btn.closest(".thumbs");
  if (!container) return;

  const wasActive = btn.classList.contains("active");
  const baseScore = btn.classList.contains("thumb-up") ? 1 : -1;
  const score = wasActive ? 0 : baseScore;

  let res;
  try {
    res = await fetch("/api/feedback/thumbs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        report_date: container.dataset.reportDate,
        item_id: container.dataset.itemId,
        score: score,
      }),
    });
  } catch (err) {
    console.error("thumbs fetch failed", err);
    return;
  }

  if (!res.ok) {
    console.error("thumbs POST rejected:", res.status);
    return;
  }

  container.querySelectorAll("button").forEach(b => b.classList.remove("active"));
  if (score !== 0) btn.classList.add("active");
});
```

- [ ] **Step 5: Run thumbs tests**

Run:
```bash
uv run pytest tests/intelligence_web/test_routes_thumbs.py -v
```

Expected: 9 tests pass.

- [ ] **Step 6: Run full webapp test suite to catch any regressions**

Run:
```bash
uv run pytest tests/intelligence_web/ -v
```

Expected: all webapp tests pass (~40 tests at this point).

- [ ] **Step 7: Commit**

```bash
git add src/project0/intelligence_web/routes.py \
        src/project0/intelligence_web/static/thumbs.js \
        tests/intelligence_web/test_routes_thumbs.py
git commit -m "feat(6e): POST /api/feedback/thumbs + vanilla-JS client"
```

---

## Task 9: `LLMProvider.complete` — extended thinking parameter

**Files:**
- Modify: `src/project0/llm/provider.py`
- Create: `tests/llm/test_thinking_budget.py`

- [ ] **Step 1: Write the failing tests**

Check if `tests/llm/` exists; if not, create it:
```bash
mkdir -p tests/llm
touch tests/llm/__init__.py
```

Create `tests/llm/test_thinking_budget.py`:
```python
"""Tests for the `thinking_budget_tokens` kwarg added to LLMProvider.complete (6e)."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from project0.llm.provider import (
    AnthropicProvider,
    FakeProvider,
    LLMProviderError,
    Msg,
)


async def test_fake_provider_records_thinking_budget_in_call() -> None:
    fake = FakeProvider(responses=["ok"])
    await fake.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=100,
        thinking_budget_tokens=4096,
    )
    assert fake.calls[0].thinking_budget_tokens == 4096


async def test_fake_provider_defaults_thinking_budget_to_none() -> None:
    fake = FakeProvider(responses=["ok"])
    await fake.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=100,
    )
    assert fake.calls[0].thinking_budget_tokens is None


async def test_anthropic_provider_passes_thinking_to_sdk() -> None:
    provider = AnthropicProvider(api_key="k", model="claude-opus-4-6")
    # Replace the client with a mock
    mock_resp = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "hello"
    mock_resp.content = [text_block]
    mock_create = AsyncMock(return_value=mock_resp)
    provider._client = MagicMock()  # type: ignore[attr-defined]
    provider._client.messages = MagicMock()
    provider._client.messages.create = mock_create

    await provider.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=32768,
        thinking_budget_tokens=16384,
    )

    kwargs = mock_create.call_args.kwargs
    assert kwargs.get("thinking") == {"type": "enabled", "budget_tokens": 16384}


async def test_anthropic_provider_omits_thinking_when_none() -> None:
    provider = AnthropicProvider(api_key="k", model="claude-opus-4-6")
    mock_resp = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "hello"
    mock_resp.content = [text_block]
    mock_create = AsyncMock(return_value=mock_resp)
    provider._client = MagicMock()  # type: ignore[attr-defined]
    provider._client.messages = MagicMock()
    provider._client.messages.create = mock_create

    await provider.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=100,
    )

    kwargs = mock_create.call_args.kwargs
    assert "thinking" not in kwargs


async def test_anthropic_provider_skips_thinking_blocks_in_response() -> None:
    provider = AnthropicProvider(api_key="k", model="claude-opus-4-6")
    mock_resp = MagicMock()
    thinking_block = MagicMock()
    thinking_block.type = "thinking"
    thinking_block.text = "internal reasoning"
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "final answer"
    mock_resp.content = [thinking_block, text_block]
    mock_create = AsyncMock(return_value=mock_resp)
    provider._client = MagicMock()  # type: ignore[attr-defined]
    provider._client.messages = MagicMock()
    provider._client.messages.create = mock_create

    result = await provider.complete(
        system="sys",
        messages=[Msg(role="user", content="hi")],
        max_tokens=32768,
        thinking_budget_tokens=16384,
    )
    assert result == "final answer"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/llm/test_thinking_budget.py -v
```

Expected: fail with `TypeError: complete() got an unexpected keyword argument 'thinking_budget_tokens'` or `AttributeError` on `calls[0].thinking_budget_tokens`.

- [ ] **Step 3: Extend `ProviderCall` dataclass**

In `src/project0/llm/provider.py`, find the `ProviderCall` dataclass at around line 41:
```python
@dataclass
class ProviderCall:
    system: str
    messages: list[Msg]
    max_tokens: int
```

Replace it with:
```python
@dataclass
class ProviderCall:
    system: str
    messages: list[Msg]
    max_tokens: int
    thinking_budget_tokens: int | None = None
```

- [ ] **Step 4: Extend `LLMProvider.complete` protocol**

Find the `LLMProvider` Protocol class at around line 48. Replace:
```python
class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
    ) -> str:
        ...
```

With:
```python
class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
        thinking_budget_tokens: int | None = None,
    ) -> str:
        ...
```

- [ ] **Step 5: Extend `FakeProvider.complete`**

Find `FakeProvider.complete` at around line 82. Replace:
```python
    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
    ) -> str:
        self.calls.append(
            ProviderCall(system=system, messages=list(messages), max_tokens=max_tokens)
        )
```

With:
```python
    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
        thinking_budget_tokens: int | None = None,
    ) -> str:
        self.calls.append(
            ProviderCall(
                system=system,
                messages=list(messages),
                max_tokens=max_tokens,
                thinking_budget_tokens=thinking_budget_tokens,
            )
        )
```

- [ ] **Step 6: Extend `AnthropicProvider.complete`**

Find `AnthropicProvider.complete` at around line 145. Replace:
```python
    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
    ) -> str:
        sdk_messages: list[MessageParam] = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        system_block: list[TextBlockParam] = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system_block,
                messages=sdk_messages,
            )
        except Exception as e:
            log.exception("anthropic call failed")
            raise LLMProviderError(f"anthropic {type(e).__name__}") from e

        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", None)
                if text:
                    return str(text)
        raise LLMProviderError("anthropic response contained no text block")
```

With:
```python
    async def complete(
        self,
        *,
        system: str,
        messages: list[Msg],
        max_tokens: int = 800,
        thinking_budget_tokens: int | None = None,
    ) -> str:
        sdk_messages: list[MessageParam] = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        system_block: list[TextBlockParam] = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        extra: dict[str, Any] = {}
        if thinking_budget_tokens is not None:
            extra["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget_tokens,
            }
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system_block,
                messages=sdk_messages,
                **extra,
            )
        except Exception as e:
            log.exception("anthropic call failed")
            raise LLMProviderError(f"anthropic {type(e).__name__}") from e

        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", None)
                if text:
                    return str(text)
        raise LLMProviderError("anthropic response contained no text block")
```

Verify `Any` is already imported at the top of the file. If not, add to the existing `from typing import ...` line.

- [ ] **Step 7: Run the new tests**

Run:
```bash
uv run pytest tests/llm/test_thinking_budget.py -v
```

Expected: 5 tests pass.

- [ ] **Step 8: Run the full existing suite to catch regressions**

Run:
```bash
uv run pytest -q
```

Expected: all existing tests still pass. The added kwarg defaults to `None` so no existing caller changes behavior.

- [ ] **Step 9: Commit**

```bash
git add src/project0/llm/provider.py tests/llm/test_thinking_budget.py tests/llm/__init__.py
git commit -m "feat(6e): extend LLMProvider.complete with thinking_budget_tokens"
```

---

## Task 10: Plumb thinking budget through `generate.py` and `IntelligenceConfig`

**Files:**
- Modify: `src/project0/intelligence/generate.py`
- Modify: `src/project0/agents/intelligence.py`
- Modify: `tests/intelligence/test_generate_pipeline.py`
- Modify: `tests/agents/test_intelligence_config_load.py`

- [ ] **Step 1: Inspect the current `generate_daily_report` signature**

Run:
```bash
uv run python -c "import inspect; from project0.intelligence.generate import generate_daily_report; print(inspect.signature(generate_daily_report))"
```

Note the current parameters. The plan assumes `summarizer_max_tokens` is already there (from 6d).

- [ ] **Step 2: Write the failing test for generate plumbing**

Read the existing `tests/intelligence/test_generate_pipeline.py` to find an existing successful test (e.g., one that drives `generate_daily_report` with a `FakeProvider`). Copy its structure into a new test at the end of the file:

```python
async def test_generate_passes_thinking_budget_to_llm(
    tmp_path: Path,
    # reuse whatever other fixtures the existing tests use
) -> None:
    """Verify thinking_budget_tokens flows through to the LLM provider."""
    from project0.intelligence.generate import generate_daily_report
    from project0.intelligence.source import Tweet
    from project0.intelligence.fake_source import FakeTwitterSource
    from project0.intelligence.watchlist import WatchEntry
    from project0.llm.provider import FakeProvider
    from datetime import date, datetime, timezone
    from zoneinfo import ZoneInfo
    import json

    tweets = {
        "openai": [
            Tweet(
                handle="openai",
                tweet_id="1",
                url="https://x.com/openai/status/1",
                text="o5 is here",
                posted_at=datetime(2026, 4, 15, 3, 0, tzinfo=timezone.utc),
                reply_count=1,
                like_count=10,
                retweet_count=1,
            )
        ]
    }
    source = FakeTwitterSource(timelines=tweets)
    minimal_report = {
        "date": "2026-04-15",
        "news_items": [
            {
                "id": "n1",
                "headline": "o5",
                "summary": "summary",
                "importance": "high",
                "importance_reason": "r",
                "topics": ["ai"],
                "source_tweets": [
                    {
                        "handle": "openai",
                        "url": "https://x.com/openai/status/1",
                        "text": "o5 is here",
                        "posted_at": "2026-04-15T03:00:00+00:00",
                    }
                ],
            }
        ],
        "suggested_accounts": [],
    }
    llm = FakeProvider(responses=[json.dumps(minimal_report, ensure_ascii=False)])

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()

    await generate_daily_report(
        target_date=date(2026, 4, 15),
        source=source,
        llm=llm,
        summarizer_model="claude-opus-4-6",
        summarizer_max_tokens=32768,
        summarizer_thinking_budget=16384,
        watchlist=[WatchEntry(handle="openai", tags=(), notes="")],
        reports_dir=reports_dir,
        user_tz=ZoneInfo("Asia/Shanghai"),
        timeline_since_hours=24,
        max_tweets_per_handle=10,
    )

    assert len(llm.calls) == 1
    assert llm.calls[0].thinking_budget_tokens == 16384
```

Adjust the test to match the existing style — if the existing tests already construct fixtures for `source`, `llm`, `watchlist`, use the same pattern. The critical assertions are `llm.calls[0].thinking_budget_tokens == 16384` and the call to `generate_daily_report` must pass `summarizer_thinking_budget=16384`.

**Note:** Check the existing `generate_daily_report` signature. If it currently takes `summarizer_model` as a keyword or not, match the existing call sites. Adjust accordingly.

- [ ] **Step 3: Run test to verify it fails**

Run:
```bash
uv run pytest tests/intelligence/test_generate_pipeline.py::test_generate_passes_thinking_budget_to_llm -v
```

Expected: `TypeError: generate_daily_report() got an unexpected keyword argument 'summarizer_thinking_budget'`.

- [ ] **Step 4: Update `generate_daily_report` signature**

In `src/project0/intelligence/generate.py`, find the `async def generate_daily_report(` signature (line ~33). Add a new parameter `summarizer_thinking_budget: int | None = None` in the keyword-only section (after `summarizer_max_tokens`).

Find the `llm.complete(...)` call (line ~88–95). Replace:
```python
    result_text = await llm.complete(
        system=SUMMARIZER_SYSTEM_PROMPT,
        messages=[Msg(role="user", content=user_prompt)],
        max_tokens=summarizer_max_tokens,
    )
```

With:
```python
    result_text = await llm.complete(
        system=SUMMARIZER_SYSTEM_PROMPT,
        messages=[Msg(role="user", content=user_prompt)],
        max_tokens=summarizer_max_tokens,
        thinking_budget_tokens=summarizer_thinking_budget,
    )
```

(If the existing call passes `model=summarizer_model`, preserve that — add `thinking_budget_tokens` alongside. The exact positional/keyword mix depends on what already exists; match it.)

- [ ] **Step 5: Run the generate test**

Run:
```bash
uv run pytest tests/intelligence/test_generate_pipeline.py::test_generate_passes_thinking_budget_to_llm -v
```

Expected: PASS.

- [ ] **Step 6: Extend `IntelligenceConfig` to load `thinking_budget_tokens`**

In `src/project0/agents/intelligence.py`, find the `IntelligenceConfig` dataclass at line 122:
```python
@dataclass(frozen=True)
class IntelligenceConfig:
    summarizer_model: str
    summarizer_max_tokens: int
    qa_model: str
    qa_max_tokens: int
    transcript_window: int
    max_tool_iterations: int
    timeline_since_hours: int
    max_tweets_per_handle: int
```

Replace with:
```python
@dataclass(frozen=True)
class IntelligenceConfig:
    summarizer_model: str
    summarizer_max_tokens: int
    summarizer_thinking_budget: int | None
    qa_model: str
    qa_max_tokens: int
    transcript_window: int
    max_tool_iterations: int
    timeline_since_hours: int
    max_tweets_per_handle: int
```

Find `load_intelligence_config` (line 134). In the `_require` section, add a new read immediately after `summarizer_max_tokens`:

Change the `IntelligenceConfig(...)` constructor call (line ~154) to include:
```python
        summarizer_thinking_budget=(
            int(data["llm"]["summarizer"]["thinking_budget_tokens"])
            if "thinking_budget_tokens" in data.get("llm", {}).get("summarizer", {})
            else None
        ),
```

Insert this line between the `summarizer_max_tokens=` line and the `qa_model=` line. The `.get()` chain keeps backwards compatibility for tests that load minimal TOML without the new field.

- [ ] **Step 7: Use the config in the dispatch**

Find the `generate_daily_report` call inside `_dispatch_tool_inner` (line ~299). Add a line passing the new config value:

```python
            report = await generate_daily_report(
                target_date=target_date,
                source=self._twitter,
                llm=self._llm_summarizer,
                summarizer_max_tokens=self._config.summarizer_max_tokens,
                summarizer_thinking_budget=self._config.summarizer_thinking_budget,
                watchlist=self._watchlist,
                reports_dir=self._reports_dir,
                user_tz=self._user_tz,
                timeline_since_hours=self._config.timeline_since_hours,
                max_tweets_per_handle=self._config.max_tweets_per_handle,
            )
```

(Preserve whatever other params were passed before — this task only adds `summarizer_thinking_budget`. If the current call passes a `summarizer_model`, keep it.)

- [ ] **Step 8: Extend the config loader test**

Find `tests/agents/test_intelligence_config_load.py` and add a test that asserts the new field loads correctly from the real TOML:

```python
def test_loads_summarizer_thinking_budget_from_toml(tmp_path) -> None:
    from pathlib import Path
    from project0.agents.intelligence import load_intelligence_config

    cfg = load_intelligence_config(Path("prompts/intelligence.toml"))
    assert cfg.summarizer_thinking_budget == 16384
```

- [ ] **Step 9: Run the full test suite**

Run:
```bash
uv run pytest -q
```

Expected: all tests pass. The existing `tests/agents/test_intelligence_config_load.py` cases that build a `IntelligenceConfig` directly or load minimal TOML may need updating; if any fail, add `summarizer_thinking_budget=None` (or a valid int) to those constructions inline.

- [ ] **Step 10: Commit**

```bash
git add src/project0/intelligence/generate.py src/project0/agents/intelligence.py \
        tests/intelligence/test_generate_pipeline.py tests/agents/test_intelligence_config_load.py
git commit -m "feat(6e): plumb thinking_budget_tokens through generate + config"
```

---

## Task 11: `get_report_link` tool spec + dispatch

**Files:**
- Modify: `src/project0/agents/intelligence.py`
- Modify: `tests/agents/test_intelligence_tool_dispatch.py`

- [ ] **Step 1: Write the failing tests**

Open `tests/agents/test_intelligence_tool_dispatch.py`. Find an existing test that drives `_dispatch_tool` or `_dispatch_tool_inner` on the Intelligence class with a successful report read (e.g., for `get_latest_report`). Use its fixture/setup pattern as a template.

Append these tests at the end of the file (adjust fixture/builder names to match what the file already uses):

```python
@pytest.mark.asyncio
async def test_get_report_link_latest_picks_newest(
    tmp_path,
    # whatever fixture builds an Intelligence with tmp reports_dir; adapt
):
    # Arrange: write two report files on different dates
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    for d in ("2026-04-10", "2026-04-15"):
        (reports_dir / f"{d}.json").write_text(
            '{"date":"%s","generated_at":"2026-04-15T00:00:00+08:00",'
            '"user_tz":"Asia/Shanghai","watchlist_snapshot":[],'
            '"news_items":[{"id":"n1","headline":"h","summary":"s",'
            '"importance":"high","importance_reason":"r","topics":[],'
            '"source_tweets":[{"handle":"x","url":"u","text":"t",'
            '"posted_at":"2026-04-15T00:00:00Z"}]}],'
            '"suggested_accounts":[],'
            '"stats":{"tweets_fetched":1,"handles_attempted":1,'
            '"handles_succeeded":1,"items_generated":1,"errors":[]}}'
            % d,
            encoding="utf-8",
        )

    intel = _build_intelligence(reports_dir=reports_dir, public_base_url="http://host:8080")

    call = ToolCall(id="tc1", name="get_report_link", input={"date": "latest"})
    content, is_error = await intel._dispatch_tool_inner(call)
    assert is_error is False
    parsed = json.loads(content)
    assert parsed["url"] == "http://host:8080/reports/2026-04-15"
    assert parsed["date"] == "2026-04-15"


@pytest.mark.asyncio
async def test_get_report_link_specific_date_exists(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "2026-04-15.json").write_text(
        '{"date":"2026-04-15","generated_at":"2026-04-15T00:00:00+08:00",'
        '"user_tz":"Asia/Shanghai","watchlist_snapshot":[],'
        '"news_items":[{"id":"n1","headline":"h","summary":"s",'
        '"importance":"high","importance_reason":"r","topics":[],'
        '"source_tweets":[{"handle":"x","url":"u","text":"t",'
        '"posted_at":"2026-04-15T00:00:00Z"}]}],"suggested_accounts":[],'
        '"stats":{"tweets_fetched":1,"handles_attempted":1,'
        '"handles_succeeded":1,"items_generated":1,"errors":[]}}',
        encoding="utf-8",
    )
    intel = _build_intelligence(reports_dir=reports_dir, public_base_url="http://host:8080")
    call = ToolCall(id="tc1", name="get_report_link", input={"date": "2026-04-15"})
    content, is_error = await intel._dispatch_tool_inner(call)
    assert is_error is False
    parsed = json.loads(content)
    assert parsed["url"] == "http://host:8080/reports/2026-04-15"


@pytest.mark.asyncio
async def test_get_report_link_nonexistent_date_returns_error(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    intel = _build_intelligence(reports_dir=reports_dir, public_base_url="http://host:8080")
    call = ToolCall(id="tc1", name="get_report_link", input={"date": "2020-01-01"})
    content, is_error = await intel._dispatch_tool_inner(call)
    assert is_error is True
    assert "2020-01-01" in content


@pytest.mark.asyncio
async def test_get_report_link_invalid_date_format_returns_error(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    intel = _build_intelligence(reports_dir=reports_dir, public_base_url="http://host:8080")
    call = ToolCall(id="tc1", name="get_report_link", input={"date": "not-a-date"})
    content, is_error = await intel._dispatch_tool_inner(call)
    assert is_error is True
    assert "invalid" in content.lower() or "not-a-date" in content


@pytest.mark.asyncio
async def test_get_report_link_latest_with_no_reports_returns_error(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    intel = _build_intelligence(reports_dir=reports_dir, public_base_url="http://host:8080")
    call = ToolCall(id="tc1", name="get_report_link", input={"date": "latest"})
    content, is_error = await intel._dispatch_tool_inner(call)
    assert is_error is True
    assert "no reports" in content.lower() or "generate" in content.lower()


@pytest.mark.asyncio
async def test_get_report_link_trims_trailing_slash_on_base_url(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "2026-04-15.json").write_text(
        '{"date":"2026-04-15","generated_at":"2026-04-15T00:00:00+08:00",'
        '"user_tz":"Asia/Shanghai","watchlist_snapshot":[],'
        '"news_items":[{"id":"n1","headline":"h","summary":"s",'
        '"importance":"high","importance_reason":"r","topics":[],'
        '"source_tweets":[{"handle":"x","url":"u","text":"t",'
        '"posted_at":"2026-04-15T00:00:00Z"}]}],"suggested_accounts":[],'
        '"stats":{"tweets_fetched":1,"handles_attempted":1,'
        '"handles_succeeded":1,"items_generated":1,"errors":[]}}',
        encoding="utf-8",
    )
    intel = _build_intelligence(reports_dir=reports_dir, public_base_url="http://host:8080/")
    call = ToolCall(id="tc1", name="get_report_link", input={"date": "2026-04-15"})
    content, is_error = await intel._dispatch_tool_inner(call)
    assert is_error is False
    parsed = json.loads(content)
    assert parsed["url"] == "http://host:8080/reports/2026-04-15"
```

**Note:** The helper `_build_intelligence(...)` above is a placeholder — use whatever builder/fixture the existing test file already provides. Most 6d tests will have a fixture that constructs an `Intelligence` with a `FakeProvider` and `FakeTwitterSource`. Extend that fixture to accept a `public_base_url` kwarg (you'll need to update the fixture itself too). If the existing fixture is a function like `_make_intelligence()`, find it near the top of the test file and add `public_base_url="http://test.local"` with the parameter plumbed through.

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/agents/test_intelligence_tool_dispatch.py -v -k get_report_link
```

Expected: `KeyError` or `TypeError` from the unknown tool name or missing `public_base_url` parameter.

- [ ] **Step 3: Add the `_GET_REPORT_LINK_SCHEMA` and tool spec**

In `src/project0/agents/intelligence.py`, find the existing schemas around line 166–199. Add a new schema after `_LIST_REPORTS_SCHEMA`:

```python
_GET_REPORT_LINK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {
            "type": "string",
            "description": (
                "Either a YYYY-MM-DD date string or the literal string "
                "'latest'. If 'latest', the tool picks the newest report "
                "that exists on disk."
            ),
        },
    },
    "required": ["date"],
}
```

Then find `_build_tool_specs` at line 234. Append a 5th entry to the returned list:
```python
            ToolSpec(
                name="get_report_link",
                description=(
                    "Return a stable URL to the webpage rendering of a daily "
                    "report. Use this whenever the user asks to 'send me', "
                    "'share', 'open', or 'give me a link to' a daily report. "
                    "Paste the returned URL verbatim into your reply — do not "
                    "shorten, paraphrase, or wrap it in a code block. Pass "
                    "'latest' to get the most recent report."
                ),
                input_schema=_GET_REPORT_LINK_SCHEMA,
            ),
```

- [ ] **Step 4: Add the dispatch branch**

Find `_dispatch_tool_inner` at line 285. After the `if name == "list_reports":` branch and before the final fall-through, add:

```python
        if name == "get_report_link":
            raw = (inp.get("date") or "").strip()
            if raw == "latest":
                dates = list_report_dates(self._reports_dir)
                if not dates:
                    return (
                        "No reports exist yet. Generate one first.",
                        True,
                    )
                target = dates[0]
            else:
                try:
                    target = date.fromisoformat(raw)
                except ValueError:
                    return (
                        f"Invalid date: {raw!r}. Expected YYYY-MM-DD or 'latest'.",
                        True,
                    )
                if not (self._reports_dir / f"{target.isoformat()}.json").exists():
                    return (f"No report for {target.isoformat()}.", True)
            base = self._public_base_url.rstrip("/")
            url = f"{base}/reports/{target.isoformat()}"
            return (
                json.dumps({"url": url, "date": target.isoformat()}, ensure_ascii=False),
                False,
            )
```

(`date`, `json`, and `list_report_dates` should already be imported at the top of the file from 6d.)

- [ ] **Step 5: (Stub) Add `_public_base_url` attribute access temporarily**

The dispatch references `self._public_base_url` which doesn't exist yet — it will be added in Task 12 when the `__init__` signature changes. For now, to keep tests runnable, we need to either (a) jump ahead and add the init param now, or (b) use a default.

Do option (a): also apply Task 12's init change now so everything compiles and tests pass. The steps are:

1. Modify `Intelligence.__init__` at line 210 to add `public_base_url: str` kwarg after `user_tz: ZoneInfo`.
2. Add `self._public_base_url = public_base_url` in the body of `__init__`.

Specifically:
```python
    def __init__(
        self,
        *,
        llm_summarizer: "LLMProvider",
        llm_qa: "LLMProvider",
        twitter: TwitterSource,
        messages_store: "MessagesStore | None",
        persona: IntelligencePersona,
        config: IntelligenceConfig,
        watchlist: list[WatchEntry],
        reports_dir: Path,
        user_tz: ZoneInfo,
        public_base_url: str,
    ) -> None:
        self._llm_summarizer = llm_summarizer
        self._llm_qa = llm_qa
        self._twitter = twitter
        self._messages = messages_store
        self._persona = persona
        self._config = config
        self._watchlist = watchlist
        self._reports_dir = reports_dir
        self._user_tz = user_tz
        self._public_base_url = public_base_url
        self._tool_specs = self._build_tool_specs()
```

- [ ] **Step 6: Update any test fixtures that build Intelligence**

Any existing fixture/helper (e.g., `_make_intelligence`, `_build_intelligence`, or directly-constructed `Intelligence(...)` calls) in the test suite now needs `public_base_url="http://test.local"` (or any valid URL). Grep for construction sites:

```bash
grep -rn "Intelligence(" tests/ src/project0/main.py
```

Add `public_base_url="http://test.local"` to every call except `main.py` (which we'll handle in Task 13).

- [ ] **Step 7: Run the get_report_link tests**

Run:
```bash
uv run pytest tests/agents/test_intelligence_tool_dispatch.py -v -k get_report_link
```

Expected: 6 tests pass.

- [ ] **Step 8: Run the full suite**

Run:
```bash
uv run pytest -q
```

Expected: all tests pass (or the only failure is `main.py`-side, addressed in Task 13).

- [ ] **Step 9: Commit**

```bash
git add src/project0/agents/intelligence.py tests/agents/test_intelligence_tool_dispatch.py
git commit -m "feat(6e): get_report_link tool + Intelligence public_base_url"
```

---

## Task 12: Persona additions + persona-load regression test

**Files:**
- Modify: `prompts/intelligence.md`
- Modify: `tests/agents/test_intelligence_persona_load.py`

- [ ] **Step 1: Add the persona block**

Open `prompts/intelligence.md`. Find the "Tools and behavior" section (or whatever section currently governs how tools should be used). If such a section doesn't exist by that exact name, find the most appropriate location — typically near the description of the existing four tools.

Append the following Chinese text:
```markdown

## 分享每日情报的网页链接

当用户请求查看、发送、分享、或"把今天的情报给我"时（无论中英文表达），
你必须使用 `get_report_link` 工具获取网页链接，然后把链接**原样**贴到回复里。
不要缩短、改写、或把链接放在代码块里。用户会直接点击链接打开网页。

示例：
- 用户："把今天的日报发给我。"
  你调用 `get_report_link(date="latest")`，得到 `{"url": "...", "date": "2026-04-15"}`。
  回复："（指尖轻点）今日情报整理完毕 → https://intel.tailnet.ts.net/reports/2026-04-15"

- 用户："昨天有什么新的？"
  你先用 `get_report` 查看昨天的内容做出简短回答。如果用户追问细节或
  要求"完整版"、"链接"、"发给我"，再用 `get_report_link` 返回链接。

## 源推文链接

在文字回复里讨论具体新闻条目时，如果条目有 `source_tweets`，可以直接把
其中一条推文 URL 贴出来做引用。不要把所有推文都贴上——挑最权威的那条
（通常是第一方账号或事件原始发布者）。
```

- [ ] **Step 2: Add a persona regression test**

Open `tests/agents/test_intelligence_persona_load.py`. Add:
```python
def test_persona_mentions_get_report_link() -> None:
    from pathlib import Path
    text = Path("prompts/intelligence.md").read_text(encoding="utf-8")
    assert "get_report_link" in text, (
        "persona file missing the get_report_link instruction block"
    )


def test_persona_mentions_source_tweet_citation_rule() -> None:
    from pathlib import Path
    text = Path("prompts/intelligence.md").read_text(encoding="utf-8")
    assert "source_tweets" in text
```

- [ ] **Step 3: Run persona tests**

Run:
```bash
uv run pytest tests/agents/test_intelligence_persona_load.py -v
```

Expected: all tests pass, including the two new ones.

- [ ] **Step 4: Commit**

```bash
git add prompts/intelligence.md tests/agents/test_intelligence_persona_load.py
git commit -m "persona(intelligence): link-sharing and source-citation rules"
```

---

## Task 13: `main.py` — load `WebConfig`, wire `public_base_url`, add `_run_web` task

**Files:**
- Modify: `src/project0/main.py`

- [ ] **Step 1: Add WebConfig loading near intelligence_cfg loading**

Open `src/project0/main.py`. Find line ~140 where `intelligence_cfg` is loaded:
```python
    intelligence_cfg = load_intelligence_config(Path("prompts/intelligence.toml"))
```

Immediately after that line, add:
```python

    # Load webapp config from the same file. Shared between the agent
    # (for building link URLs in get_report_link) and the webapp (for
    # binding and filesystem access).
    import tomllib
    from project0.intelligence_web.config import WebConfig
    _intel_toml_data = tomllib.loads(
        Path("prompts/intelligence.toml").read_text(encoding="utf-8")
    )
    if "web" not in _intel_toml_data:
        raise RuntimeError(
            "prompts/intelligence.toml missing [web] section — required for 6e"
        )
    web_config = WebConfig.from_toml_section(_intel_toml_data["web"])
```

(The extra `tomllib` + `Path` read here is acceptable — the existing `load_intelligence_config` also re-reads the same file separately. A cleaner refactor could centralize it, but is out of scope for 6e.)

- [ ] **Step 2: Pass `public_base_url` to `Intelligence`**

Find the `Intelligence(...)` construction at line 166:
```python
    intelligence = Intelligence(
        llm_summarizer=intelligence_llm_summarizer,
        llm_qa=intelligence_llm_qa,
        twitter=twitter_source,
        messages_store=store.messages(),
        persona=intelligence_persona,
        config=intelligence_cfg,
        watchlist=intelligence_watchlist,
        reports_dir=reports_dir,
        user_tz=settings.user_tz,
    )
```

Replace with:
```python
    intelligence = Intelligence(
        llm_summarizer=intelligence_llm_summarizer,
        llm_qa=intelligence_llm_qa,
        twitter=twitter_source,
        messages_store=store.messages(),
        persona=intelligence_persona,
        config=intelligence_cfg,
        watchlist=intelligence_watchlist,
        reports_dir=reports_dir,
        user_tz=settings.user_tz,
        public_base_url=web_config.public_base_url,
    )
```

- [ ] **Step 3: Add the `_run_web` async helper**

At module scope in `main.py`, above `async def _run(settings: Settings) -> None:` (line 64), add:

```python
async def _run_web(
    *,
    web_config: "WebConfig",  # forward ref; imported inside _run
    stop_event: asyncio.Event,
) -> None:
    """Run the Intelligence webapp as an asyncio task alongside the bot
    pollers. Shares `stop_event` with the rest of _run so Ctrl-C stops
    everything together."""
    from project0.intelligence_web.app import create_app
    import uvicorn

    app = create_app(web_config)
    config = uvicorn.Config(
        app,
        host=web_config.bind_host,
        port=web_config.bind_port,
        log_level="info",
        access_log=True,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    # main.py owns signals; prevent uvicorn from installing its own handlers
    server.install_signal_handlers = lambda: None  # type: ignore[assignment]

    server_task = asyncio.create_task(server.serve())
    try:
        await stop_event.wait()
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=5.0)
        except asyncio.TimeoutError:
            server.force_exit = True
            await server_task
```

- [ ] **Step 4: Wire `_run_web` into the TaskGroup**

Find the `async with asyncio.TaskGroup() as tg:` block at line 239. Find where `stop_event` is created at line 258. Currently it's inside the TaskGroup's body after spawning pollers. That ordering means the web task would have to be spawned after stop_event exists.

Refactor: move the `stop_event = asyncio.Event()` line to **before** the `async with asyncio.TaskGroup()` line, and then add the web task spawn inside the block.

Find:
```python
    async with asyncio.TaskGroup() as tg:
        for name, app in apps.items():
            assert app.updater is not None
            tg.create_task(
                app.updater.start_polling(drop_pending_updates=True)
            )
            log.info("bot %s polling", name)

        for entry in pulse_entries:
            tg.create_task(
                run_pulse_loop(
                    entry=entry,
                    target_agent="manager",
                    orchestrator=orch,
                )
            )
            log.info("pulse task spawned: %s", entry.name)

        # Run forever until cancelled.
        stop_event = asyncio.Event()
        await stop_event.wait()
```

Replace with:
```python
    stop_event = asyncio.Event()
    async with asyncio.TaskGroup() as tg:
        for name, app in apps.items():
            assert app.updater is not None
            tg.create_task(
                app.updater.start_polling(drop_pending_updates=True)
            )
            log.info("bot %s polling", name)

        for entry in pulse_entries:
            tg.create_task(
                run_pulse_loop(
                    entry=entry,
                    target_agent="manager",
                    orchestrator=orch,
                )
            )
            log.info("pulse task spawned: %s", entry.name)

        tg.create_task(_run_web(web_config=web_config, stop_event=stop_event))
        log.info(
            "intelligence webapp task spawned: bound to %s:%d",
            web_config.bind_host, web_config.bind_port,
        )

        # Run forever until cancelled.
        await stop_event.wait()
```

- [ ] **Step 5: Start the bots and webapp locally as a smoke check**

Run:
```bash
uv run python -m project0.main &
MAIN_PID=$!
sleep 3
curl -sf http://127.0.0.1:8080/healthz || echo "HEALTHZ FAILED"
curl -sf http://127.0.0.1:8080/ > /dev/null && echo "ROOT OK" || echo "ROOT FAILED"
kill $MAIN_PID
wait $MAIN_PID 2>/dev/null
```

Expected: "ok" from healthz, "ROOT OK". No traceback errors in the output.

**If the Telegram bots fail to start due to missing `.env` tokens:** that's an orthogonal issue, not an 6e regression. The webapp-side smoke test can instead be run via the dev helper in Task 14. For this task, at minimum verify that `uv run python -c "from project0.intelligence_web.app import create_app; from project0.intelligence_web.config import WebConfig; from zoneinfo import ZoneInfo; from pathlib import Path; app = create_app(WebConfig(public_base_url='http://test.local', bind_host='127.0.0.1', bind_port=8080, reports_dir=Path('data/intelligence/reports'), feedback_dir=Path('data/intelligence/feedback'), user_tz=ZoneInfo('Asia/Shanghai'))); print(type(app).__name__)"` prints `FastAPI`.

- [ ] **Step 6: Run the full test suite**

Run:
```bash
uv run pytest -q
```

Expected: every test passes.

- [ ] **Step 7: Commit**

```bash
git add src/project0/main.py
git commit -m "feat(6e): load WebConfig, wire public_base_url, spawn _run_web task"
```

---

## Task 14: `scripts/dev_web.sh`, `scripts/smoke_web.sh`, README update

**Files:**
- Create: `scripts/dev_web.sh`
- Create: `scripts/smoke_web.sh`
- Modify: `README.md`

- [ ] **Step 1: Create the dev helper**

Create `scripts/dev_web.sh`:
```bash
#!/usr/bin/env bash
# Run the Intelligence webapp in isolation with live reload for template/CSS
# iteration. Reads the real data/intelligence/reports/ directory. Does not
# start the Telegram bots. Bound on a separate port from production (8081).
set -euo pipefail

cd "$(dirname "$0")/.."

exec uv run uvicorn \
    "project0.intelligence_web.app:create_app" \
    --factory \
    --reload \
    --port 8081 \
    --host 127.0.0.1
```

**Note:** `create_app` takes a `WebConfig` argument, not zero args. The `--factory` mode in uvicorn expects a zero-arg callable. Either (a) add a top-level `app` variable in `app.py` that builds one from a default config, or (b) write a tiny helper in `app.py` like `def _dev_factory() -> FastAPI: return create_app(WebConfig(...default dev values...))`.

Pick option (b): append to `src/project0/intelligence_web/app.py`:
```python
def _dev_factory() -> FastAPI:
    """Factory for `uvicorn --factory` dev mode — builds an app with sensible
    dev defaults pointing at the real data directory. Not used in production."""
    from pathlib import Path
    from zoneinfo import ZoneInfo

    cfg = WebConfig(
        public_base_url="http://localhost:8081",
        bind_host="127.0.0.1",
        bind_port=8081,
        reports_dir=Path("data/intelligence/reports"),
        feedback_dir=Path("data/intelligence/feedback"),
        user_tz=ZoneInfo("Asia/Shanghai"),
    )
    return create_app(cfg)
```

Update `dev_web.sh` to reference the new factory:
```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run uvicorn \
    "project0.intelligence_web.app:_dev_factory" \
    --factory \
    --reload \
    --port 8081 \
    --host 127.0.0.1
```

Make it executable:
```bash
chmod +x scripts/dev_web.sh
```

- [ ] **Step 2: Create the smoke test script**

Create `scripts/smoke_web.sh`:
```bash
#!/usr/bin/env bash
# End-to-end sanity check for the Intelligence webapp. Seeds a fake report,
# spins up uvicorn, exercises the main routes, and cleans up. Manual only —
# not run in CI. Does not touch real data beyond the seeded file.
set -euo pipefail

cd "$(dirname "$0")/.."

PORT=18080
REPORT_DATE="2099-12-31"
REPORT_PATH="data/intelligence/reports/${REPORT_DATE}.json"
FEEDBACK_PATH="data/intelligence/feedback/$(date +%Y-%m).jsonl"

mkdir -p "data/intelligence/reports" "data/intelligence/feedback"

cleanup() {
    rm -f "$REPORT_PATH"
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "seeding fake report at $REPORT_PATH"
cat > "$REPORT_PATH" <<'JSON'
{
  "date": "2099-12-31",
  "generated_at": "2099-12-31T08:00:00+08:00",
  "user_tz": "Asia/Shanghai",
  "watchlist_snapshot": ["smoke"],
  "news_items": [
    {
      "id": "n1",
      "headline": "SMOKE TEST HEADLINE",
      "summary": "smoke test summary",
      "importance": "high",
      "importance_reason": "smoke",
      "topics": ["smoke"],
      "source_tweets": [
        {
          "handle": "smoke",
          "url": "https://x.com/smoke/status/1",
          "text": "smoke",
          "posted_at": "2099-12-31T00:00:00Z"
        }
      ]
    }
  ],
  "suggested_accounts": [],
  "stats": {
    "tweets_fetched": 1,
    "handles_attempted": 1,
    "handles_succeeded": 1,
    "items_generated": 1,
    "errors": []
  }
}
JSON

echo "starting uvicorn on port $PORT"
uv run uvicorn \
    "project0.intelligence_web.app:_dev_factory" \
    --factory \
    --port "$PORT" \
    --host 127.0.0.1 \
    > /tmp/smoke_web.log 2>&1 &
SERVER_PID=$!

# Wait for the server to come up
for _ in $(seq 1 20); do
    if curl -sf "http://127.0.0.1:${PORT}/healthz" > /dev/null 2>&1; then
        break
    fi
    sleep 0.3
done

echo "GET /healthz"
curl -sf "http://127.0.0.1:${PORT}/healthz"
echo

echo "GET /reports/${REPORT_DATE}"
curl -sf "http://127.0.0.1:${PORT}/reports/${REPORT_DATE}" | grep -q "SMOKE TEST HEADLINE" \
    && echo "  ✓ headline rendered" \
    || { echo "  ✗ headline missing"; exit 1; }

echo "GET /history"
curl -sf "http://127.0.0.1:${PORT}/history" | grep -q "${REPORT_DATE}" \
    && echo "  ✓ date listed" \
    || { echo "  ✗ date missing from history"; exit 1; }

echo "POST /api/feedback/thumbs"
curl -sf -X POST "http://127.0.0.1:${PORT}/api/feedback/thumbs" \
    -H "Content-Type: application/json" \
    -d "{\"report_date\":\"${REPORT_DATE}\",\"item_id\":\"n1\",\"score\":1}" \
    | grep -q '"ok":true' \
    && echo "  ✓ thumbs accepted" \
    || { echo "  ✗ thumbs rejected"; exit 1; }

echo "GET /reports/${REPORT_DATE} (check feedback reflected)"
curl -sf "http://127.0.0.1:${PORT}/reports/${REPORT_DATE}" | grep -q "thumb-up active" \
    && echo "  ✓ thumbs-up reflected in rendered HTML" \
    || { echo "  ✗ thumbs-up not active in HTML"; exit 1; }

echo
echo "smoke test passed ✓"
```

Make it executable:
```bash
chmod +x scripts/smoke_web.sh
```

- [ ] **Step 3: Run the smoke script to verify it passes**

Run:
```bash
./scripts/smoke_web.sh
```

Expected: all five checkmarks printed, "smoke test passed ✓" at the end. The report file and feedback log entries are cleaned up (though the feedback file may remain since the script doesn't delete it — that's fine for smoke, just manually `rm data/intelligence/feedback/$(date +%Y-%m).jsonl` if it bothers you).

**If the script fails:** investigate `/tmp/smoke_web.log`. Most likely cause is a template rendering bug or a route path mismatch — fix the underlying issue, not the smoke script.

- [ ] **Step 4: Update README.md**

Open `README.md`. Find the section documenting the Intelligence agent (from 6d) — likely a section titled something like "Intelligence agent (6d)" or similar. Append a new subsection after it:

```markdown

### Intelligence webapp (6e)

The Intelligence agent ships with a FastAPI webapp that renders daily reports
in your browser. Run `python -m project0.main` and the webapp starts alongside
the Telegram bots on the port configured in `prompts/intelligence.toml`
(default `8080`).

**Access from your phone via Tailscale.** The webapp binds to `0.0.0.0:8080`
by default. Install Tailscale on both the server machine and your phone, log
in on both with the same account, and open
`http://<machine>.<tailnet>.ts.net:8080/` in your phone's browser. The
connection works over cellular and any WiFi — Tailscale handles the tunneling
and provides the DNS name.

**URLs:**
- `/` — latest report (auto-picks newest on disk)
- `/reports/YYYY-MM-DD` — specific report by date
- `/history` — browsable list of all reports grouped by month
- `/healthz` — liveness probe

**顾瑾 can send you links.** In Telegram, ask: "把今天的日报发给我" or
"send me today's report" — Intelligence returns a URL you can tap.

**Feedback.** Each news item has thumbs up/down buttons. Clicks are recorded
to `data/intelligence/feedback/YYYY-MM.jsonl` as an append-only event log.
6e captures the signal only; reading it back to influence generation or
memory is a later sub-project.

**Configuration** (`[web]` section in `prompts/intelligence.toml`):
- `public_base_url` — the URL 顾瑾 uses when constructing links. Must start
  with `http://` or `https://`. **Update this to your Tailscale hostname.**
- `bind_host` — default `0.0.0.0` (all interfaces)
- `bind_port` — default `8080`
- `reports_dir`, `feedback_dir` — filesystem paths
- `user_tz` — timezone for feedback event timestamps and the report meta line

**Dev workflow** — run just the webapp with live reload on port 8081 for
template/CSS iteration (doesn't start the Telegram bots):
```
./scripts/dev_web.sh
```

**Smoke test** — spins up a temporary server, exercises all routes against
a seeded fake report, cleans up:
```
./scripts/smoke_web.sh
```

**Security note.** There is no auth, TLS, rate limiting, or CSRF protection.
The security model is "Tailscale is the gate". **Do not expose port 8080 to
the public internet.** Verify your firewall (`sudo ufw status` or equivalent)
does not forward port 8080 from the outside.
```

- [ ] **Step 5: Final full suite run**

Run:
```bash
uv run pytest -q
```

Expected: all tests pass. Project total should be ~320 tests (up from 256 at start of 6e).

- [ ] **Step 6: Commit**

```bash
git add scripts/dev_web.sh scripts/smoke_web.sh src/project0/intelligence_web/app.py README.md
git commit -m "docs(6e): dev/smoke scripts + README webapp section"
```

---

## Task 15: Verification and final sanity checks

**Files:** none modified — verification only.

- [ ] **Step 1: Full test suite**

Run:
```bash
uv run pytest -q
```

Expected: all tests pass, count is roughly 256 + 60..80 = 316..336.

- [ ] **Step 2: Type check**

Run:
```bash
uv run mypy src/project0/intelligence_web src/project0/agents/intelligence.py src/project0/intelligence/generate.py src/project0/llm/provider.py src/project0/main.py
```

Expected: no type errors. If `mypy` complains about untyped `uvicorn` or `fastapi`, add `[[tool.mypy.overrides]]` entries to `pyproject.toml` matching the existing pattern used for `google.*`. Example:
```toml
[[tool.mypy.overrides]]
module = ["uvicorn.*", "fastapi.*", "starlette.*", "jinja2.*"]
ignore_missing_imports = true
```

Re-run mypy after any config change.

- [ ] **Step 3: Linting**

Run:
```bash
uv run ruff check src/project0/intelligence_web tests/intelligence_web
uv run ruff format --check src/project0/intelligence_web tests/intelligence_web
```

Expected: no issues. If ruff reports style problems, run `uv run ruff check --fix ...` and `uv run ruff format ...` to auto-fix, then re-run the check.

- [ ] **Step 4: Manual browser verification**

Start the dev server:
```bash
./scripts/dev_web.sh
```

In your browser, open `http://localhost:8081/`. You should see either a rendered report (if you have any real reports on disk) or the "No reports yet" empty state. Visit `/history` and `/healthz`. Click a thumbs button on a news item and confirm the active-state CSS toggles.

Stop the dev server with `Ctrl-C`.

- [ ] **Step 5: Full manual smoke**

Run:
```bash
./scripts/smoke_web.sh
```

Expected: passes cleanly.

- [ ] **Step 6: Commit any mypy/ruff config tweaks**

If Step 2 or Step 3 required changes to `pyproject.toml`:
```bash
git add pyproject.toml
git commit -m "chore(6e): mypy/ruff overrides for webapp deps"
```

If nothing was touched, skip this step.

---

## Post-plan

Once all tasks pass, the branch is ready for:
- Merge to main (via `superpowers:finishing-a-development-branch` if you're using worktrees).
- A follow-up sub-project (new 6f: two-source generation with dedicated intel Twitter account + auto-discovery via search queries).

The spec file (`docs/superpowers/specs/2026-04-15-intelligence-delivery-surface-design.md`) is the reference for any questions during implementation. If you discover a mismatch between the plan and the spec, trust the spec for intent and the plan for exact code; if they contradict on design decisions, stop and surface the discrepancy.
