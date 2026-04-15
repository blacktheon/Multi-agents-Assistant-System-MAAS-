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
