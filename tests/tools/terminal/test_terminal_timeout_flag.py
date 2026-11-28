from unittest.mock import MagicMock

from openhands.tools.terminal.terminal.terminal_session import TerminalSession


def _make_session() -> TerminalSession:
    backend = MagicMock()
    return TerminalSession(terminal=backend, no_change_timeout_seconds=3)


def test_hard_timeout_sets_observation_timeout_true():
    session = _make_session()
    obs = session._handle_hard_timeout_command(
        command="sleep 10",
        terminal_content="partial line\n",
        ps1_matches=[],
        timeout=2.0,
    )
    assert obs.timeout is True
    assert obs.exit_code == -1
    llm_text = "\n".join(c.text for c in obs.to_llm_content if hasattr(c, "text"))
    assert "The command timed out after 2.0 seconds." in llm_text


def test_nochange_timeout_sets_observation_timeout_true():
    session = _make_session()
    obs = session._handle_nochange_timeout_command(
        command="sleep 10",
        terminal_content="waiting...\n",
        ps1_matches=[],
    )
    assert obs.timeout is True
    assert obs.exit_code == -1
    llm_text = "\n".join(c.text for c in obs.to_llm_content if hasattr(c, "text"))
    assert "no new output" in llm_text
