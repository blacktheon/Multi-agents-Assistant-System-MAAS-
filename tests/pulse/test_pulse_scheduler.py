import asyncio

import pytest

from project0.envelope import Envelope
from project0.pulse import PulseEntry, build_pulse_envelope, run_pulse_loop


def test_build_pulse_envelope_shape():
    entry = PulseEntry(
        name="check_calendar",
        every_seconds=300,
        chat_id=42,
        payload={"window_minutes": 60},
    )
    env = build_pulse_envelope(entry, target_agent="manager")
    assert env.source == "pulse"
    assert env.routing_reason == "pulse"
    assert env.from_kind == "system"
    assert env.to_agent == "manager"
    assert env.telegram_chat_id == 42
    assert env.telegram_msg_id is None
    assert env.body == "check_calendar"
    assert env.payload == {
        "pulse_name": "check_calendar",
        "window_minutes": 60,
    }


def test_build_pulse_envelope_unbound_chat():
    entry = PulseEntry(name="p", every_seconds=60, chat_id=None, payload={})
    env = build_pulse_envelope(entry, target_agent="manager")
    assert env.telegram_chat_id is None


class _FakeOrch:
    def __init__(self):
        self.calls: list[Envelope] = []
        self.raise_next = False

    async def handle_pulse(self, env: Envelope) -> None:
        self.calls.append(env)
        if self.raise_next:
            self.raise_next = False
            raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_run_pulse_loop_fires_and_survives_errors(monkeypatch):
    entry = PulseEntry(name="p", every_seconds=60, chat_id=None, payload={})
    orch = _FakeOrch()

    # Patch asyncio.sleep to return immediately, but cap iterations.
    tick_count = {"n": 0}
    real_sleep = asyncio.sleep

    async def fast_sleep(_):
        tick_count["n"] += 1
        if tick_count["n"] >= 3:
            # Signal cancellation on the 3rd tick by raising CancelledError.
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr("project0.pulse.asyncio.sleep", fast_sleep)

    orch.raise_next = True  # first handle_pulse should raise; loop must survive.

    task = asyncio.create_task(
        run_pulse_loop(entry=entry, target_agent="manager", orchestrator=orch)
    )
    with pytest.raises(asyncio.CancelledError):
        await task

    # First tick fires only after the first sleep; then the second tick fires.
    # Tick 1 raises inside handle_pulse → swallowed → loop continues.
    # Tick 2 succeeds → handle_pulse called again.
    # Tick 3 → sleep raises CancelledError → loop exits.
    assert len(orch.calls) == 2
    assert all(e.source == "pulse" for e in orch.calls)
