"""Tests for bash terminal reset functionality."""

import platform
import tempfile
import uuid

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.terminal import (
    TerminalAction,
    TerminalObservation,
    TerminalTool,
)


IS_WINDOWS = platform.system() == "Windows"


def _create_conv_state(working_dir: str) -> ConversationState:
    """Helper to create a ConversationState for testing."""

    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    return ConversationState.create(
        id=uuid.uuid4(), agent=agent, workspace=LocalWorkspace(working_dir=working_dir)
    )


def test_bash_reset_basic():
    """Test basic reset functionality."""
    with tempfile.TemporaryDirectory() as temp_dir:
        tools = TerminalTool.create(_create_conv_state(temp_dir))
        tool = tools[0]
        try:
            if IS_WINDOWS:
                # Execute a command to set an environment variable (PowerShell)
                action = TerminalAction(command="$env:TEST_VAR = 'hello'")
                result = tool(action)
                assert isinstance(result, TerminalObservation)
                assert result.metadata.exit_code == 0

                # Verify the variable is set
                action = TerminalAction(command="Write-Output $env:TEST_VAR")
                result = tool(action)
                assert isinstance(result, TerminalObservation)
                assert "hello" in result.text

                # Reset the terminal
                reset_action = TerminalAction(command="", reset=True)
                reset_result = tool(reset_action)
                assert isinstance(reset_result, TerminalObservation)
                assert "Terminal session has been reset" in reset_result.text
                assert reset_result.command == "[RESET]"

                # Verify the variable is no longer set after reset
                action = TerminalAction(command="Write-Output $env:TEST_VAR")
                result = tool(action)
                assert isinstance(result, TerminalObservation)
                # The variable should be empty after reset
                assert result.text.strip() == ""
            else:
                # Execute a command to set an environment variable (Bash)
                action = TerminalAction(command="export TEST_VAR=hello")
                result = tool(action)
                assert isinstance(result, TerminalObservation)
                assert result.metadata.exit_code == 0

                # Verify the variable is set
                action = TerminalAction(command="echo $TEST_VAR")
                result = tool(action)
                assert isinstance(result, TerminalObservation)
                assert "hello" in result.text

                # Reset the terminal
                reset_action = TerminalAction(command="", reset=True)
                reset_result = tool(reset_action)
                assert isinstance(reset_result, TerminalObservation)
                assert "Terminal session has been reset" in reset_result.text
                assert reset_result.command == "[RESET]"

                # Verify the variable is no longer set after reset
                action = TerminalAction(command="echo $TEST_VAR")
                result = tool(action)
                assert isinstance(result, TerminalObservation)
                # The variable should be empty after reset
                assert result.text.strip() == ""
        finally:
            assert tool.executor is not None
            tool.executor.close()


def test_bash_reset_with_command():
    """Test that reset executes the command after resetting."""
    with tempfile.TemporaryDirectory() as temp_dir:
        tools = TerminalTool.create(_create_conv_state(temp_dir))
        tool = tools[0]
        try:
            if IS_WINDOWS:
                # Set an environment variable (PowerShell)
                action = TerminalAction(command="$env:TEST_VAR = 'world'")
                result = tool(action)
                assert isinstance(result, TerminalObservation)
                assert result.metadata.exit_code == 0

                # Reset with a command (should reset then execute the command)
                reset_action = TerminalAction(
                    command="Write-Output 'hello from fresh terminal'", reset=True
                )
                reset_result = tool(reset_action)
                assert isinstance(reset_result, TerminalObservation)
                assert "Terminal session has been reset" in reset_result.text
                assert "hello from fresh terminal" in reset_result.text
                assert (
                    reset_result.command
                    == "[RESET] Write-Output 'hello from fresh terminal'"
                )

                # Verify the variable is no longer set (confirming reset worked)
                action = TerminalAction(command="Write-Output $env:TEST_VAR")
                result = tool(action)
                assert isinstance(result, TerminalObservation)
                assert result.text.strip() == ""
            else:
                # Set an environment variable (Bash)
                action = TerminalAction(command="export TEST_VAR=world")
                result = tool(action)
                assert isinstance(result, TerminalObservation)
                assert result.metadata.exit_code == 0

                # Reset with a command (should reset then execute the command)
                reset_action = TerminalAction(
                    command="echo 'hello from fresh terminal'", reset=True
                )
                reset_result = tool(reset_action)
                assert isinstance(reset_result, TerminalObservation)
                assert "Terminal session has been reset" in reset_result.text
                assert "hello from fresh terminal" in reset_result.text
                assert (
                    reset_result.command == "[RESET] echo 'hello from fresh terminal'"
                )

                # Verify the variable is no longer set (confirming reset worked)
                action = TerminalAction(command="echo $TEST_VAR")
                result = tool(action)
                assert isinstance(result, TerminalObservation)
                assert result.text.strip() == ""
        finally:
            assert tool.executor is not None
            tool.executor.close()


def test_bash_reset_working_directory():
    """Test that reset restores the original working directory."""
    from pathlib import Path

    with tempfile.TemporaryDirectory() as temp_dir:
        tools = TerminalTool.create(_create_conv_state(temp_dir))
        tool = tools[0]
        try:
            # Check initial working directory via metadata
            action = TerminalAction(command="echo test")
            result = tool(action)
            assert isinstance(result, TerminalObservation)
            assert result.metadata.working_dir is not None
            # Resolve paths to canonical form (handles Windows short paths)
            result_cwd = str(Path(result.metadata.working_dir).resolve())
            temp_dir_resolved = str(Path(temp_dir).resolve())
            assert temp_dir_resolved.lower() in result_cwd.lower()

            # Change directory
            if IS_WINDOWS:
                action = TerminalAction(command="cd $env:USERPROFILE")
            else:
                action = TerminalAction(command="cd /tmp")
            result = tool(action)
            assert isinstance(result, TerminalObservation)

            # Verify directory changed
            action = TerminalAction(command="echo test")
            result = tool(action)
            assert isinstance(result, TerminalObservation)
            assert result.metadata.working_dir is not None
            result_cwd = str(Path(result.metadata.working_dir).resolve())
            assert temp_dir_resolved.lower() not in result_cwd.lower()

            # Reset the terminal
            reset_action = TerminalAction(command="", reset=True)
            reset_result = tool(reset_action)
            assert isinstance(reset_result, TerminalObservation)
            assert "Terminal session has been reset" in reset_result.text

            # Verify working directory is back to original after reset
            action = TerminalAction(command="echo test")
            result = tool(action)
            assert isinstance(result, TerminalObservation)
            assert result.metadata.working_dir is not None
            result_cwd = str(Path(result.metadata.working_dir).resolve())
            assert temp_dir_resolved.lower() in result_cwd.lower()
        finally:
            assert tool.executor is not None
            tool.executor.close()


def test_bash_reset_multiple_times():
    """Test that reset can be called multiple times."""
    with tempfile.TemporaryDirectory() as temp_dir:
        tools = TerminalTool.create(_create_conv_state(temp_dir))
        tool = tools[0]
        try:
            # First reset
            reset_action = TerminalAction(command="", reset=True)
            reset_result = tool(reset_action)
            assert isinstance(reset_result, TerminalObservation)
            assert "Terminal session has been reset" in reset_result.text

            # Execute a command after first reset
            action = TerminalAction(command="echo 'after first reset'")
            result = tool(action)
            assert isinstance(result, TerminalObservation)
            assert "after first reset" in result.text

            # Second reset
            reset_action = TerminalAction(command="", reset=True)
            reset_result = tool(reset_action)
            assert isinstance(reset_result, TerminalObservation)
            assert "Terminal session has been reset" in reset_result.text

            # Execute a command after second reset
            action = TerminalAction(command="echo 'after second reset'")
            result = tool(action)
            assert isinstance(result, TerminalObservation)
            assert "after second reset" in result.text
        finally:
            assert tool.executor is not None
            tool.executor.close()


def test_bash_reset_with_timeout():
    """Test that reset works with timeout parameter."""
    with tempfile.TemporaryDirectory() as temp_dir:
        tools = TerminalTool.create(_create_conv_state(temp_dir))
        tool = tools[0]
        try:
            # Reset with timeout (should ignore timeout)
            reset_action = TerminalAction(command="", reset=True, timeout=5.0)
            reset_result = tool(reset_action)
            assert isinstance(reset_result, TerminalObservation)
            assert "Terminal session has been reset" in reset_result.text
            assert reset_result.command == "[RESET]"
        finally:
            assert tool.executor is not None
            tool.executor.close()


def test_bash_reset_with_is_input_validation():
    """Test that reset=True with is_input=True raises validation error."""
    with tempfile.TemporaryDirectory() as temp_dir:
        tools = TerminalTool.create(_create_conv_state(temp_dir))
        tool = tools[0]
        try:
            # Create action with invalid combination
            action = TerminalAction(command="", reset=True, is_input=True)

            # Should raise error when executed
            with pytest.raises(
                ValueError, match="Cannot use reset=True with is_input=True"
            ):
                tool(action)
        finally:
            assert tool.executor is not None
            tool.executor.close()


def test_bash_reset_only_with_empty_command():
    """Test reset with empty command (reset only)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        tools = TerminalTool.create(_create_conv_state(temp_dir))
        tool = tools[0]
        try:
            # Reset with empty command
            reset_action = TerminalAction(command="", reset=True)
            reset_result = tool(reset_action)
            assert isinstance(reset_result, TerminalObservation)
            assert "Terminal session has been reset" in reset_result.text
            assert reset_result.command == "[RESET]"
        finally:
            assert tool.executor is not None
            tool.executor.close()
