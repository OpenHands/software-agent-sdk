"""
Tests for proper cleanup of tool executors in conversations.

This test suite verifies that tool executors are properly cleaned up
when conversations are closed or destroyed.
"""

import tempfile
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, ClassVar, Literal
from unittest.mock import Mock

from openhands.sdk import Agent, Conversation
from openhands.sdk.tool import Tool, ToolExecutor, register_tool
from openhands.tools.terminal import TerminalExecutor, TerminalTool


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class _InjectedExecutorTerminalTool(TerminalTool):
    """TerminalTool variant that injects a test-provided executor at resolve time.

    ``TerminalTool.create`` needs ``conv_state`` (for the working dir), so this
    subclass builds the real tool from ``conv_state`` and then swaps in the
    executor captured on the class attribute. Each test sets ``injected_executor``
    before registering this subclass.
    """

    injected_executor: ClassVar[ToolExecutor | None] = None

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
        username: str | None = None,
        no_change_timeout_seconds: int | None = None,
        terminal_type: Literal["tmux", "subprocess", "powershell"] | None = None,
        shell_path: str | None = None,
        executor: ToolExecutor | None = None,
        *,
        env: Mapping[str, str] | None = None,
    ) -> Sequence[TerminalTool]:
        tools = TerminalTool.create(
            conv_state,
            username=username,
            no_change_timeout_seconds=no_change_timeout_seconds,
            terminal_type=terminal_type,
            shell_path=shell_path,
            executor=executor,
            env=env,
        )
        return [
            tool.model_copy(update={"executor": cls.injected_executor})
            for tool in tools
        ]


def test_conversation_close_calls_executor_close(mock_llm):
    """Test that Conversation.close() calls close() on all tool executors."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a TerminalExecutor with subprocess terminal to avoid tmux issues
        terminal_executor = TerminalExecutor(
            working_dir=temp_dir, terminal_type="subprocess"
        )
        terminal_executor.close = Mock()

        _InjectedExecutorTerminalTool.injected_executor = terminal_executor
        register_tool("test_terminal", _InjectedExecutorTerminalTool)

        # Create agent and conversation
        agent = Agent(
            llm=mock_llm,
            tools=[Tool(name="test_terminal")],
        )
        conversation = Conversation(
            agent=agent, workspace=temp_dir, delete_on_close=True
        )

        # Trigger lazy agent initialization to create tools
        conversation._ensure_agent_ready()

        # Close the conversation
        conversation.close()

        # Verify that the executor's close method was called
        terminal_executor.close.assert_called_once()


def test_conversation_close_calls_executor_close_without_delete(mock_llm):
    """Executors are closed even when delete_on_close=False."""
    with tempfile.TemporaryDirectory() as temp_dir:
        terminal_executor = TerminalExecutor(
            working_dir=temp_dir, terminal_type="subprocess"
        )
        terminal_executor.close = Mock()

        _InjectedExecutorTerminalTool.injected_executor = terminal_executor
        register_tool("test_terminal", _InjectedExecutorTerminalTool)

        agent = Agent(
            llm=mock_llm,
            tools=[Tool(name="test_terminal")],
        )
        conversation = Conversation(
            agent=agent, workspace=temp_dir, delete_on_close=False
        )
        conversation._ensure_agent_ready()
        conversation.close()

        terminal_executor.close.assert_called_once()


def test_conversation_del_calls_close(mock_llm):
    """Test that Conversation.__del__() calls close()."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a TerminalExecutor with subprocess terminal to avoid tmux issues
        terminal_executor = TerminalExecutor(
            working_dir=temp_dir, terminal_type="subprocess"
        )
        terminal_executor.close = Mock()

        _InjectedExecutorTerminalTool.injected_executor = terminal_executor
        register_tool("test_terminal", _InjectedExecutorTerminalTool)

        # Create agent and conversation
        agent = Agent(
            llm=mock_llm,
            tools=[Tool(name="test_terminal")],
        )
        conversation = Conversation(
            agent=agent, workspace=temp_dir, delete_on_close=True
        )

        # Trigger lazy agent initialization to create tools
        conversation._ensure_agent_ready()

        # Manually call __del__ to simulate garbage collection
        conversation.__del__()

        # Verify that the executor's close method was called
        terminal_executor.close.assert_called_once()


def test_conversation_close_handles_executor_exceptions(mock_llm):
    """Test that Conversation.close() handles exceptions from executor.close()."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a mock LLM to avoid actual API calls

        # Create a TerminalExecutor with subprocess terminal and make its close method
        # raise an exception
        terminal_executor = TerminalExecutor(
            working_dir=temp_dir, terminal_type="subprocess"
        )
        terminal_executor.close = Mock(side_effect=Exception("Test exception"))

        _InjectedExecutorTerminalTool.injected_executor = terminal_executor
        register_tool("test_terminal", _InjectedExecutorTerminalTool)

        # Create agent and conversation
        agent = Agent(
            llm=mock_llm,
            tools=[Tool(name="test_terminal")],
        )
        conversation = Conversation(agent=agent, workspace=temp_dir)

        # Close should not raise an exception even if executor.close() fails
        # We can see from the captured stderr that the warning is logged correctly
        conversation.close()  # This should not raise an exception


def test_conversation_close_skips_none_executors(mock_llm):
    """Test that Conversation.close() skips tools with None executors."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a mock LLM to avoid actual API calls

        # Create a tool with no executor
        _InjectedExecutorTerminalTool.injected_executor = None
        register_tool("test_terminal", _InjectedExecutorTerminalTool)

        # Create agent and conversation
        agent = Agent(
            llm=mock_llm,
            tools=[Tool(name="test_terminal")],
        )
        conversation = Conversation(agent=agent, workspace=temp_dir)

        # This should not raise an exception
        conversation.close()


def test_terminal_executor_close_calls_session_close():
    """Test that TerminalExecutor.close() calls session.close()."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a TerminalExecutor with subprocess terminal
        terminal_executor = TerminalExecutor(
            working_dir=temp_dir, terminal_type="subprocess"
        )

        # Mock the session's close method
        terminal_executor.session.close = Mock()

        # Call close on the executor
        terminal_executor.close()

        # Verify that session.close() was called
        terminal_executor.session.close.assert_called_once()


def test_terminal_executor_close_handles_missing_session():
    """Test that TerminalExecutor.close() handles missing session attribute."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a TerminalExecutor with subprocess terminal
        terminal_executor = TerminalExecutor(
            working_dir=temp_dir, terminal_type="subprocess"
        )

        # Clear the session to simulate a missing/uninitialized state
        terminal_executor._session = None

        # This should not raise an exception
        terminal_executor.close()
