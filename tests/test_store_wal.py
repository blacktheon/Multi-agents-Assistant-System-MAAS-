"""WAL journaling mode is required so the control panel (separate process)
can read llm_usage and write user_facts concurrently with a running MAAS
process. See docs/superpowers/specs/2026-04-16-control-panel-design.md §7.6."""

from pathlib import Path

from project0.store import Store


def test_journal_mode_is_wal(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_synchronous_is_normal(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    # PRAGMA synchronous returns 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
    val = store.conn.execute("PRAGMA synchronous").fetchone()[0]
    assert int(val) == 1
