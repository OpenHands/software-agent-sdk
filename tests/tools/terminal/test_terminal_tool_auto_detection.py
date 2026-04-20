"""Tests for TerminalTool auto-detection functionality."""

import tempfile
import uuid
from unittest.mock import patch

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.terminal import TerminalTool
from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.impl import TerminalExecutor
from openhands.tools.terminal.terminal import (
    SubprocessTerminal,
    TerminalSession,
)


def _create_conv_state(working_dir: str) -> ConversationState:
    """Helper to create a ConversationState for testing."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    return ConversationState.create(
        id=uuid.uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=working_dir),
    )


def test_default_auto_detection():
    """Test that TerminalTool auto-detects the appropriate session type."""
    with tempfile.TemporaryDirectory() as temp_dir:
        tools = TerminalTool.create(_create_conv_state(temp_dir))
        tool = tools[0]

        # TerminalTool always has an executor
        assert tool.executor is not None
        executor = tool.executor
        assert isinstance(executor, TerminalExecutor)

        # In pool mode there is no single session attribute;
        # in single-session mode there is.
        if executor.is_pooled:
            assert executor._pool is not None
            assert executor._pool.max_panes >= 1
        else:
            assert isinstance(executor.session, TerminalSession)
            terminal_type = type(executor.session.terminal).__name__
            assert terminal_type in ["TmuxTerminal", "SubprocessTerminal"]

        # Test that it works
        action = TerminalAction(command="echo 'Auto-detection test'")
        obs = executor(action)
        assert "Auto-detection test" in obs.text


def test_forced_terminal_types():
    """Test forcing specific session types."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Test forced subprocess session
        tools = TerminalTool.create(
            _create_conv_state(temp_dir), terminal_type="subprocess"
        )
        tool = tools[0]
        assert tool.executor is not None
        executor = tool.executor
        assert isinstance(executor, TerminalExecutor)
        assert isinstance(executor.session, TerminalSession)
        assert isinstance(executor.session.terminal, SubprocessTerminal)

        # Test basic functionality
        action = TerminalAction(command="echo 'Subprocess test'")
        obs = tool.executor(action)
        assert obs.metadata.exit_code == 0


@patch("platform.system")
def test_unix_auto_detection(mock_system):
    """Test auto-detection behavior on Unix-like systems."""
    mock_system.return_value = "Linux"

    with tempfile.TemporaryDirectory() as temp_dir:
        # Mock tmux as available → pool mode
        with patch(
            "openhands.tools.terminal.terminal.factory._is_tmux_available",
            return_value=True,
        ):
            tools = TerminalTool.create(_create_conv_state(temp_dir))
            tool = tools[0]
            assert tool.executor is not None
            executor = tool.executor
            assert isinstance(executor, TerminalExecutor)
            # Pool mode: no single session, pool is active
            assert executor.is_pooled

        # Mock tmux as unavailable → single-session / subprocess mode
        with (
            patch(
                "openhands.tools.terminal.terminal.factory._is_tmux_available",
                return_value=False,
            ),
            patch(
                "openhands.tools.terminal.impl._is_tmux_available",
                return_value=False,
            ),
        ):
            tools = TerminalTool.create(_create_conv_state(temp_dir))
            tool = tools[0]
            assert tool.executor is not None
            executor = tool.executor
            assert isinstance(executor, TerminalExecutor)
            assert isinstance(executor.session, TerminalSession)
            assert isinstance(executor.session.terminal, SubprocessTerminal)


def test_session_parameters():
    """Test that session parameters are properly passed."""
    with tempfile.TemporaryDirectory() as temp_dir:
        tools = TerminalTool.create(
            _create_conv_state(temp_dir),
            username="testuser",
            no_change_timeout_seconds=60,
            terminal_type="subprocess",
        )
        tool = tools[0]

        assert tool.executor is not None
        executor = tool.executor
        assert isinstance(executor, TerminalExecutor)
        session = executor.session
        assert session.work_dir == temp_dir
        assert session.username == "testuser"
        assert session.no_change_timeout_seconds == 60


def test_backward_compatibility():
    """Test that the simplified API still works."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # This should work just like before
        tools = TerminalTool.create(_create_conv_state(temp_dir))
        tool = tools[0]

        assert tool.executor is not None
        action = TerminalAction(command="echo 'Backward compatibility test'")
        obs = tool.executor(action)
        assert "Backward compatibility test" in obs.text
        assert obs.metadata.exit_code == 0


def test_tool_metadata():
    """Test that tool metadata is preserved."""
    with tempfile.TemporaryDirectory() as temp_dir:
        tools = TerminalTool.create(_create_conv_state(temp_dir))
        tool = tools[0]

        assert tool.name == "terminal"
        assert tool.description is not None
        assert tool.action_type == TerminalAction
        assert hasattr(tool, "annotations")


def test_session_lifecycle():
    """Test session lifecycle management."""
    with tempfile.TemporaryDirectory() as temp_dir:
        tools = TerminalTool.create(
            _create_conv_state(temp_dir), terminal_type="subprocess"
        )
        tool = tools[0]

        # Session should be initialized
        assert tool.executor is not None
        executor = tool.executor
        assert isinstance(executor, TerminalExecutor)
        assert executor.session._initialized

        # Should be able to execute commands
        action = TerminalAction(command="echo 'Lifecycle test'")
        obs = executor(action)
        assert obs.metadata.exit_code == 0

        # Manual cleanup should work
        executor.session.close()
        assert executor.session._closed
