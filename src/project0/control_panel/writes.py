"""Atomic text-file writes used by every file-edit route in the control
panel. A panel crash between truncate() and write() on a target file
like .env or user_profile.yaml would corrupt it; writing to a sibling
tmp file and os.replace-ing is the standard POSIX fix."""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically (UTF-8).

    The parent directory must already exist — this function does not
    create it. Raises FileNotFoundError if it does not. On POSIX, the
    os.replace call is atomic; a concurrent reader either sees the old
    file or the new file, never a half-written one.
    """
    parent = path.parent
    if not parent.exists():
        raise FileNotFoundError(f"parent directory does not exist: {parent}")
    tmp = parent / (path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
