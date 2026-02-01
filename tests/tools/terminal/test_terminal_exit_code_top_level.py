import os

import pytest

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import create_terminal_session


@pytest.mark.parametrize("terminal_type", ["tmux", "subprocess"])
def test_exit_code_top_level_completed(terminal_type):
    session = create_terminal_session(work_dir=os.getcwd(), terminal_type=terminal_type)
    session.initialize()
    try:
        obs = session.execute(TerminalAction(command="echo top-level"))
        assert obs.metadata.exit_code == 0
        assert obs.exit_code == 0
        assert obs.exit_code == obs.metadata.exit_code
    finally:
        session.close()


@pytest.mark.parametrize("terminal_type", ["tmux", "subprocess"])
def test_exit_code_top_level_soft_timeout(terminal_type):
    session = create_terminal_session(
        work_dir=os.getcwd(), no_change_timeout_seconds=1, terminal_type=terminal_type
    )
    session.initialize()
    try:
        # Command produces no output and should trigger no-change timeout
        obs = session.execute(TerminalAction(command="sleep 2"))
        assert obs.metadata.exit_code == -1
        assert obs.exit_code == -1
        assert obs.exit_code == obs.metadata.exit_code
    finally:
        session.close()


@pytest.mark.parametrize("terminal_type", ["tmux", "subprocess"])
def test_exit_code_top_level_hard_timeout(terminal_type):
    session = create_terminal_session(work_dir=os.getcwd(), terminal_type=terminal_type)
    session.initialize()
    try:
        # Hard timeout should set exit_code to -1 as per schema docs
        obs = session.execute(TerminalAction(command="sleep 10", timeout=1.0))
        assert obs.metadata.exit_code == -1
        assert obs.exit_code == -1
        assert obs.exit_code == obs.metadata.exit_code
    finally:
        session.close()
