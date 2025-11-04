"""Tests for BashTool subclass."""

import tempfile
from uuid import uuid4

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.execute_bash import (
    BashTool,
    ExecuteBashAction,
    ExecuteBashObservation,
)


def _create_test_conv_state(temp_dir: str) -> ConversationState:
    """Helper to create a test conversation state."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    return ConversationState.create(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=temp_dir),
    )


def test_bash_tool_initialization():
    """Test that BashTool initializes correctly."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = BashTool.create(conv_state)
        tool = tools[0]

        # Check that the tool has the correct name and properties
        assert tool.name == "bash"
        assert tool.executor is not None
        assert tool.action_type == ExecuteBashAction


def test_bash_tool_with_username():
    """Test that BashTool initializes correctly with username."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = BashTool.create(conv_state, username="testuser")
        tool = tools[0]

        # Check that the tool has the correct name and properties
        assert tool.name == "bash"
        assert tool.executor is not None
        assert tool.action_type == ExecuteBashAction


def test_bash_tool_execution():
    """Test that BashTool can execute commands."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = BashTool.create(conv_state)
        tool = tools[0]

        # Create an action
        action = ExecuteBashAction(command="echo 'Hello, World!'")

        # Execute the action
        result = tool(action)

        # Check the result
        assert result is not None
        assert isinstance(result, ExecuteBashObservation)
        assert "Hello, World!" in result.output


def test_bash_tool_working_directory():
    """Test that BashTool respects the working directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = BashTool.create(conv_state)
        tool = tools[0]

        # Create an action to check current directory
        action = ExecuteBashAction(command="pwd")

        # Execute the action
        result = tool(action)

        # Check that the working directory is correct
        assert isinstance(result, ExecuteBashObservation)
        assert temp_dir in result.output


def test_bash_tool_to_openai_tool():
    """Test that BashTool can be converted to OpenAI tool format."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = BashTool.create(conv_state)
        tool = tools[0]

        # Convert to OpenAI tool format
        openai_tool = tool.to_openai_tool()

        # Check the format
        assert openai_tool["type"] == "function"
        assert openai_tool["function"]["name"] == "bash"
        assert "description" in openai_tool["function"]
        assert "parameters" in openai_tool["function"]
