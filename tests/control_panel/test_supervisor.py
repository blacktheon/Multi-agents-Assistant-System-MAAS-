"""Supervisor state machine tests using a fake spawn_fn.

Spec: docs/superpowers/specs/2026-04-16-control-panel-design.md §7.
No test spawns a real MAAS subprocess.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from project0.control_panel.supervisor import MAASSupervisor


class FakeProc:
    """Stand-in for asyncio.subprocess.Process with controllable wait().

    Call ``finish(rc)`` to make the pending wait() return. ``terminate``
    and ``kill`` are recorded for assertions. Default pid is 12345.
    """

    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid
        self.terminate_called = False
        self.kill_called = False
        self._done = asyncio.Event()
        self._rc: int = 0

    async def wait(self) -> int:
        await self._done.wait()
        return self._rc

    def terminate(self) -> None:
        self.terminate_called = True

    def kill(self) -> None:
        self.kill_called = True

    def finish(self, rc: int = 0) -> None:
        self._rc = rc
        self._done.set()


def _make_supervisor(proc_queue: list[FakeProc]) -> MAASSupervisor:
    """Supervisor whose spawn_fn returns each queued FakeProc in order."""
    async def spawn() -> Any:
        if not proc_queue:
            raise RuntimeError("no more fake procs queued")
        return proc_queue.pop(0)

    return MAASSupervisor(spawn_fn=spawn, stop_timeout=0.2)


@pytest.mark.asyncio
async def test_initial_state_is_stopped() -> None:
    sup = _make_supervisor([])
    assert sup.state == "stopped"
    assert sup.pid is None
    assert sup.last_exit_code is None


@pytest.mark.asyncio
async def test_start_transitions_to_running() -> None:
    proc = FakeProc(pid=999)
    sup = _make_supervisor([proc])
    await sup.start()
    assert sup.state == "running"
    assert sup.pid == 999


@pytest.mark.asyncio
async def test_stop_sends_sigterm_and_transitions() -> None:
    proc = FakeProc()
    sup = _make_supervisor([proc])
    await sup.start()

    async def clean_exit() -> None:
        await asyncio.sleep(0.01)
        assert proc.terminate_called
        proc.finish(rc=0)

    await asyncio.gather(sup.stop(), clean_exit())
    assert sup.state == "stopped"
    assert proc.terminate_called
    assert not proc.kill_called


@pytest.mark.asyncio
async def test_stop_timeout_triggers_sigkill() -> None:
    proc = FakeProc()
    sup = _make_supervisor([proc])
    await sup.start()

    async def kill_then_finish() -> None:
        while not proc.kill_called:
            await asyncio.sleep(0.01)
        proc.finish(rc=-9)

    await asyncio.gather(sup.stop(), kill_then_finish())
    assert proc.terminate_called
    assert proc.kill_called
    assert sup.state == "stopped"


@pytest.mark.asyncio
async def test_unexpected_exit_transitions_to_crashed() -> None:
    proc = FakeProc()
    sup = _make_supervisor([proc])
    await sup.start()
    proc.finish(rc=7)
    await asyncio.sleep(0.05)
    assert sup.state == "crashed"
    assert sup.last_exit_code == 7


@pytest.mark.asyncio
async def test_concurrent_start_does_not_double_spawn() -> None:
    proc = FakeProc()
    sup = _make_supervisor([proc])
    await asyncio.gather(sup.start(), sup.start())
    assert sup.state == "running"


@pytest.mark.asyncio
async def test_start_from_stopped_resets_exit_code() -> None:
    proc1 = FakeProc()
    proc2 = FakeProc(pid=222)
    sup = _make_supervisor([proc1, proc2])
    await sup.start()
    proc1.finish(rc=3)
    await asyncio.sleep(0.05)
    assert sup.state == "crashed"
    assert sup.last_exit_code == 3

    await sup.start()
    assert sup.state == "running"
    assert sup.pid == 222


@pytest.mark.asyncio
async def test_stop_when_already_stopped_is_noop() -> None:
    sup = _make_supervisor([])
    await sup.stop()
    assert sup.state == "stopped"


@pytest.mark.asyncio
async def test_restart_stop_then_start() -> None:
    proc1 = FakeProc(pid=111)
    proc2 = FakeProc(pid=222)
    sup = _make_supervisor([proc1, proc2])
    await sup.start()
    assert sup.pid == 111

    async def finish_first() -> None:
        await asyncio.sleep(0.01)
        proc1.finish(rc=0)

    await asyncio.gather(sup.restart(), finish_first())
    assert sup.state == "running"
    assert sup.pid == 222
