"""Tests for disabling bash history expansion in terminal backends."""

import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal.subprocess_terminal import SubprocessTerminal
from openhands.tools.terminal.terminal.terminal_session import TerminalSession
from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal


def test_tmux_terminal_initialization_disables_history_expansion(monkeypatch):
    """Verify TmuxTerminal.initialize sends `set +H;` to disable history expansion.

    We mock libtmux objects so no real tmux server is required.
    """

    sent_commands: list[tuple[str, dict]] = []

    class FakePane:
        def __init__(self) -> None:
            self.stdout = []

        def send_keys(self, text: str, enter: bool = True) -> None:
            sent_commands.append((text, {"enter": enter}))

        def cmd(self, *_args, **_kwargs):  # clear-history / capture-pane
            return SimpleNamespace(stdout=[""])

    class FakeWindow:
        def __init__(self) -> None:
            self._pane = FakePane()

        @property
        def active_pane(self):
            return self._pane

        def kill(self) -> None:  # called for the initial window
            return None

    class FakeSession:
        def __init__(self) -> None:
            self.history_limit = None
            self.active_window = FakeWindow()

        def set_option(self, *_args, **_kwargs):
            return None

        def new_window(self, *_, **__):
            return FakeWindow()

        def kill(self):
            return None

    class FakeServer:
        def new_session(self, *_, **__):
            return FakeSession()

    # Patch libtmux.Server used inside module
    with patch(
        "openhands.tools.terminal.terminal.tmux_terminal.libtmux.Server", FakeServer
    ):
        # Avoid isinstance check in clear_screen during initialize since we stub Pane
        monkeypatch.setattr(TmuxTerminal, "clear_screen", lambda self: None)
        term = TmuxTerminal("/tmp")
        term.initialize()

    # First send_keys should configure shell; ensure it contains `set +H;`
    assert any("set +H;" in args[0] for args in sent_commands), (
        f"Expected an init command containing 'set +H;' but got: {sent_commands}"
    )


def test_subprocess_terminal_disables_history_expansion_e2e():
    """End-to-end check that `!` does not trigger history expansion in PTY backend.

    We run a literal command containing `!` and expect the output to include it
    verbatim rather than an 'event not found' error.
    """

    with tempfile.TemporaryDirectory() as tmp:
        term = SubprocessTerminal(work_dir=tmp)
        session = TerminalSession(terminal=term)
        try:
            session.initialize()
        except RuntimeError as e:
            # Some CI environments might not have a working bash; be defensive
            pytest.skip(f"Subprocess terminal not available: {e}")

        try:
            obs = session.execute(TerminalAction(command="echo A!B"))
            assert "event not found" not in obs.text
            assert "A!B" in obs.text
        finally:
            session.close()
