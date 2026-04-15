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
    def from_toml_section(cls, section: dict[str, Any]) -> WebConfig:
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
