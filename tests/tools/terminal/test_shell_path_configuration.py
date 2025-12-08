"""Tests for shell path configuration."""

import os
import shutil
import tempfile
from unittest.mock import patch

import pytest

from openhands.tools.terminal.terminal import SubprocessTerminal
from openhands.tools.terminal.terminal.factory import create_terminal_session


def test_shell_path_explicit_parameter():
    """Test that explicit shell_path parameter is used."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Use the system bash
        bash_path = shutil.which("bash")
        if not bash_path:
            pytest.skip("bash not found in PATH")

        session = create_terminal_session(
            work_dir=temp_dir,
            terminal_type="subprocess",
            shell_path=bash_path,
        )

        assert isinstance(session.terminal, SubprocessTerminal)
        assert session.terminal.shell_path == bash_path
        session.close()


def test_shell_path_auto_detection():
    """Test shell path auto-detection with shutil.which."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Don't set shell_path or environment variable
        session = create_terminal_session(
            work_dir=temp_dir,
            terminal_type="subprocess",
        )

        # Should use auto-detected bash
        assert isinstance(session.terminal, SubprocessTerminal)
        assert session.terminal.shell_path is None  # Not set until initialize
        session.initialize()
        assert session.terminal.shell_path is not None
        session.close()


def test_shell_path_validation_not_exists():
    """Test that shell path validation fails for non-existent file."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir,
            terminal_type="subprocess",
            shell_path="/nonexistent/bash",
        )

        with pytest.raises(RuntimeError, match="Shell binary not found"):
            session.initialize()

        session.close()


def test_shell_path_validation_not_executable():
    """Test that shell path validation fails for non-executable file."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a non-executable file
        fake_bash = os.path.join(temp_dir, "fake_bash")
        with open(fake_bash, "w") as f:
            f.write("#!/bin/bash\n")
        # Don't make it executable

        session = create_terminal_session(
            work_dir=temp_dir,
            terminal_type="subprocess",
            shell_path=fake_bash,
        )

        with pytest.raises(RuntimeError, match="not executable"):
            session.initialize()

        session.close()


def test_shell_path_auto_detection_failure():
    """Test that auto-detection failure raises clear error."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Mock shutil.which to return None (bash not found)
        with patch("shutil.which", return_value=None):
            session = create_terminal_session(
                work_dir=temp_dir,
                terminal_type="subprocess",
            )

            with pytest.raises(RuntimeError, match="Could not find bash in PATH"):
                session.initialize()

            session.close()


def test_shell_path_with_tmux_terminal():
    """Test that shell_path is passed but doesn't affect tmux terminal."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bash_path = shutil.which("bash")
        if not bash_path:
            pytest.skip("bash not found in PATH")

        try:
            session = create_terminal_session(
                work_dir=temp_dir,
                terminal_type="tmux",
                shell_path=bash_path,
            )
            # TmuxTerminal doesn't use shell_path, so this should just be ignored
            session.initialize()
            session.close()
        except RuntimeError as e:
            if "Tmux is not available" in str(e):
                pytest.skip("Tmux not available on this system")
            raise


def test_shell_path_reset_preserves_config():
    """Test that terminal reset preserves the shell_path configuration."""
    from openhands.tools.terminal.impl import TerminalExecutor

    with tempfile.TemporaryDirectory() as temp_dir:
        bash_path = shutil.which("bash")
        if not bash_path:
            pytest.skip("bash not found in PATH")

        executor = TerminalExecutor(
            working_dir=temp_dir,
            terminal_type="subprocess",
            shell_path=bash_path,
        )

        # Verify shell_path is stored
        assert executor.shell_path == bash_path

        # Reset the terminal
        executor.reset()

        # Verify shell_path is preserved after reset
        assert executor.shell_path == bash_path

        executor.close()


def test_shell_path_precedence_explicit_over_auto():
    """Test that explicit shell_path takes precedence over auto-detection."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bash_path = shutil.which("bash")
        if not bash_path:
            pytest.skip("bash not found in PATH")

        # Test: Explicit parameter wins over auto-detect
        with patch("shutil.which", return_value="/other/bash"):
            session = create_terminal_session(
                work_dir=temp_dir,
                terminal_type="subprocess",
                shell_path=bash_path,
            )
            assert isinstance(session.terminal, SubprocessTerminal)
            assert session.terminal.shell_path == bash_path
            session.close()


def test_terminal_tool_shell_path_parameter():
    """Test that TerminalTool.create accepts and passes shell_path."""
    import uuid

    from pydantic import SecretStr

    from openhands.sdk.agent import Agent
    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.llm import LLM
    from openhands.sdk.workspace import LocalWorkspace
    from openhands.tools.terminal.definition import TerminalTool

    with tempfile.TemporaryDirectory() as temp_dir:
        bash_path = shutil.which("bash")
        if not bash_path:
            pytest.skip("bash not found in PATH")

        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        conv_state = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent,
            workspace=LocalWorkspace(working_dir=temp_dir),
        )

        tools = TerminalTool.create(
            conv_state=conv_state,
            terminal_type="subprocess",
            shell_path=bash_path,
        )

        terminal = tools[0]
        # Verify the executor has the shell_path
        from openhands.tools.terminal.impl import TerminalExecutor

        assert isinstance(terminal.executor, TerminalExecutor)
        assert terminal.executor.shell_path == bash_path

        terminal.executor.close()
