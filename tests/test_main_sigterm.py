"""SIGTERM must trigger the same clean shutdown path as SIGINT/KeyboardInterrupt,
so the control panel's Stop button (SIGTERM) shuts MAAS down gracefully.

We can't easily assert full asyncio behavior in-process, so we verify that
`main._install_sigterm_handler` is importable and that invoking the installed
handler function raises KeyboardInterrupt.
"""

import signal

from project0 import main


def test_install_sigterm_handler_exists() -> None:
    assert hasattr(main, "_install_sigterm_handler")
    assert callable(main._install_sigterm_handler)


def test_sigterm_handler_raises_keyboard_interrupt(monkeypatch) -> None:
    captured: list = []

    def fake_signal(sig: int, handler) -> None:
        captured.append((sig, handler))

    monkeypatch.setattr(signal, "signal", fake_signal)
    main._install_sigterm_handler()

    assert len(captured) == 1
    assert captured[0][0] == signal.SIGTERM
    handler = captured[0][1]
    try:
        handler(signal.SIGTERM, None)
    except KeyboardInterrupt:
        return
    raise AssertionError("handler should raise KeyboardInterrupt")
