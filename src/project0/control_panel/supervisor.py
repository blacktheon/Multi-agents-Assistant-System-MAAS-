"""In-memory supervisor for MAAS as a child process.

See docs/superpowers/specs/2026-04-16-control-panel-design.md §7.

Design constraints enforced here:
- Single child at a time (state machine).
- All transitions serialized by an asyncio.Lock.
- spawn_fn is injectable for tests; default is the real uv subprocess.
- Watcher task detects unexpected exits and transitions to 'crashed'.
- In-memory state only; panel restart forgets any orphan MAAS.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

log = logging.getLogger(__name__)

State = Literal["stopped", "starting", "running", "stopping", "crashed"]


class _Proc(Protocol):
    """Subset of asyncio.subprocess.Process we rely on."""
    pid: int
    async def wait(self) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


SpawnFn = Callable[[], Awaitable[_Proc]]


async def _real_spawn() -> _Proc:
    """Default spawn_fn: launch MAAS via `uv run python -m project0.main`.

    stdout and stderr are inherited from the panel process so MAAS logs
    appear in the same terminal that launched the panel.
    """
    proc = await asyncio.create_subprocess_exec(
        "uv", "run", "python", "-m", "project0.main",
    )
    return proc


class MAASSupervisor:
    def __init__(
        self,
        spawn_fn: SpawnFn = _real_spawn,
        stop_timeout: float = 10.0,
    ) -> None:
        self._spawn_fn = spawn_fn
        self._stop_timeout = stop_timeout
        self._state: State = "stopped"
        self._proc: _Proc | None = None
        self._watcher: asyncio.Task[None] | None = None
        self._last_exit_code: int | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> State:
        return self._state

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None and self._state == "running" else None

    @property
    def last_exit_code(self) -> int | None:
        return self._last_exit_code

    async def start(self) -> None:
        async with self._lock:
            if self._state in ("starting", "running", "stopping"):
                return
            self._state = "starting"
            try:
                self._proc = await self._spawn_fn()
            except Exception:
                self._state = "stopped"
                self._proc = None
                raise
            self._state = "running"
            log.info("MAAS spawned, pid=%s", self._proc.pid)
            if self._watcher is not None:
                self._watcher.cancel()
            self._watcher = asyncio.create_task(self._watch(self._proc))

    async def stop(self) -> None:
        async with self._lock:
            if self._state != "running" or self._proc is None:
                return
            self._state = "stopping"
            proc = self._proc
            proc.terminate()
            try:
                rc = await asyncio.wait_for(proc.wait(), timeout=self._stop_timeout)
            except TimeoutError:
                log.warning("MAAS did not exit after SIGTERM; sending SIGKILL")
                proc.kill()
                rc = await proc.wait()
            self._last_exit_code = rc
            self._state = "stopped"
            self._proc = None

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _watch(self, proc: _Proc) -> None:
        try:
            rc = await proc.wait()
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self._state == "stopping":
                return
            if self._proc is not proc:
                return
            self._last_exit_code = rc
            self._state = "crashed"
            self._proc = None
            log.warning("MAAS exited unexpectedly with code=%s", rc)
