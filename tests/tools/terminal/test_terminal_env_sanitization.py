"""Agent terminals must not expose credentials stripped by ``sanitized_env``."""

import tempfile

import pytest
from rich.text import Text

from openhands.tools.terminal.definition import TerminalAction, TerminalObservation
from openhands.tools.terminal.impl import TerminalExecutor
from openhands.tools.terminal.terminal import create_terminal_session


STRIPPED = {
    "OH_SESSION_API_KEYS_0": "session-key-for-test",
    "SESSION_API_KEY": "legacy-session-key-for-test",
    "OH_SECRET_KEY": "cipher-key-for-test",
}
KEPT = {"OH_WEBHOOKS_0_BASE_URL": "https://app.example.com/api/v1/webhooks"}
LIST_ENV_NAMES = "env | cut -d= -f1 | grep -E '^(OH|SESSION)_'"


@pytest.fixture(autouse=True)
def agent_server_env(monkeypatch):
    for name, value in {**STRIPPED, **KEPT}.items():
        monkeypatch.setenv(name, value)


def _assert_sanitized(obs: TerminalObservation) -> None:
    assert obs.metadata.exit_code == 0
    names = set(Text.from_ansi(obs.text).plain.split())
    assert names & set(STRIPPED) == set()
    assert set(KEPT) - names == set()


@pytest.mark.parametrize("terminal_type", ["tmux", "subprocess"])
def test_terminal_session_does_not_expose_sanitized_env_vars(terminal_type):
    with tempfile.TemporaryDirectory() as work_dir:
        session = create_terminal_session(
            work_dir=work_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            obs = session.execute(TerminalAction(command=LIST_ENV_NAMES))
        finally:
            session.close()

    _assert_sanitized(obs)


def test_pooled_terminal_does_not_expose_sanitized_env_vars():
    with tempfile.TemporaryDirectory() as work_dir:
        executor = TerminalExecutor(working_dir=work_dir, terminal_type="tmux")
        try:
            assert executor.is_pooled
            obs = executor(TerminalAction(command=LIST_ENV_NAMES))
        finally:
            executor.close()

    _assert_sanitized(obs)
