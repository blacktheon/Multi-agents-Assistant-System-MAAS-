"""Shared fixtures: a minimal project root + supervisor + FastAPI TestClient.

Every route test uses this conftest so the app construction stays in one
place. Tests never spawn a real MAAS.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from project0.control_panel.app import create_app
from project0.control_panel.supervisor import MAASSupervisor
from project0.store import Store


class FakeProc:
    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid
        self.terminate_called = False
        self.kill_called = False
        self._done = asyncio.Event()
        self._rc = 0

    async def wait(self) -> int:
        await self._done.wait()
        return self._rc

    def terminate(self) -> None:
        self.terminate_called = True
        self._done.set()

    def kill(self) -> None:
        self.kill_called = True
        self._done.set()


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """A minimal project root with data/ and prompts/ directories."""
    (tmp_path / "data").mkdir()
    (tmp_path / "prompts").mkdir()
    for name in ("manager", "secretary", "intelligence"):
        (tmp_path / "prompts" / f"{name}.toml").write_text(
            f"# placeholder for {name}\n", encoding="utf-8"
        )
        (tmp_path / "prompts" / f"{name}.md").write_text(
            f"# Persona {name}\n", encoding="utf-8"
        )
    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=sk-fake\nANTHROPIC_CACHE_TTL=ephemeral\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "user_profile.yaml").write_text(
        "address_as: 主人\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def store(project_root: Path) -> Store:
    s = Store(project_root / "data" / "store.db")
    s.init_schema()
    return s


@pytest.fixture
def supervisor() -> MAASSupervisor:
    """Supervisor whose spawn_fn always returns a fresh FakeProc."""
    async def spawn() -> Any:
        return FakeProc()
    return MAASSupervisor(spawn_fn=spawn, stop_timeout=0.2)


@pytest.fixture
def client(project_root: Path, store: Store, supervisor: MAASSupervisor) -> TestClient:
    app = create_app(
        supervisor=supervisor,
        store=store,
        project_root=project_root,
    )
    return TestClient(app)
