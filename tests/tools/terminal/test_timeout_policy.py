from unittest.mock import MagicMock

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal.terminal_session import TerminalSession
from openhands.tools.terminal.timeout_policy import (
    RUNTIME_IDLE_TIMEOUT_SECONDS_ENV,
    foreground_timeout_rejection_for,
    get_max_foreground_timeout_seconds,
    get_runtime_idle_timeout_seconds,
)


def test_runtime_idle_timeout_unset_disables_cap(monkeypatch):
    monkeypatch.delenv(RUNTIME_IDLE_TIMEOUT_SECONDS_ENV, raising=False)

    assert get_runtime_idle_timeout_seconds() is None
    assert get_max_foreground_timeout_seconds() is None
    assert (
        foreground_timeout_rejection_for(
            command="sleep 9999",
            is_input=False,
            timeout=9999,
        )
        is None
    )


def test_foreground_timeout_cap_uses_ninety_percent_of_idle_threshold(monkeypatch):
    monkeypatch.setenv(RUNTIME_IDLE_TIMEOUT_SECONDS_ENV, "1200")

    assert get_runtime_idle_timeout_seconds() == 1200
    assert get_max_foreground_timeout_seconds() == 1080
    assert (
        foreground_timeout_rejection_for(
            command="sleep 1080",
            is_input=False,
            timeout=1080,
        )
        is None
    )

    rejection = foreground_timeout_rejection_for(
        command="sleep 1500",
        is_input=False,
        timeout=1500,
    )

    assert rejection is not None
    assert "timeout=1500s" in rejection
    assert "idle cleanup after 1200s" in rejection
    assert "currently 1080s" in rejection


def test_foreground_timeout_cap_ignores_inputs_empty_commands_and_null_timeout(
    monkeypatch,
):
    monkeypatch.setenv(RUNTIME_IDLE_TIMEOUT_SECONDS_ENV, "1200")

    assert (
        foreground_timeout_rejection_for(command="C-c", is_input=True, timeout=1500)
        is None
    )
    assert (
        foreground_timeout_rejection_for(command="", is_input=False, timeout=1500)
        is None
    )
    assert (
        foreground_timeout_rejection_for(
            command="sleep 1500",
            is_input=False,
            timeout=None,
        )
        is None
    )


def test_terminal_session_rejects_unsafe_foreground_timeout_before_execution(
    monkeypatch,
):
    monkeypatch.setenv(RUNTIME_IDLE_TIMEOUT_SECONDS_ENV, "1200")
    terminal = MagicMock()
    terminal.work_dir = "/workspace"
    terminal.username = None
    session = TerminalSession(terminal=terminal)
    session._initialized = True

    observation = session.execute(TerminalAction(command="sleep 1500", timeout=1500))

    assert observation.is_error is True
    assert observation.command == "sleep 1500"
    assert "currently 1080s" in observation.text
    terminal.read_screen.assert_not_called()
    terminal.send_keys.assert_not_called()
