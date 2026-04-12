"""Parse @mentions out of Telegram message text.

The orchestrator uses this to decide whether a group message is targeted
at a specific agent. Unknown @handles are ignored (no error) — this keeps
the routing logic simple: unknown mentions mean "not a routing hint."
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_MENTION_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]*)")


def parse_mentions(text: str, known_agents: Iterable[str]) -> list[str]:
    known = {a.lower() for a in known_agents}
    out: list[str] = []
    for match in _MENTION_RE.finditer(text):
        handle = match.group(1).lower()
        if handle.endswith("_bot"):
            handle = handle[: -len("_bot")]
        if handle in known:
            out.append(handle)
    return out
