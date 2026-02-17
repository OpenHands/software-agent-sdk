"""Tests for Claude Code-style delegation tool set.

These tests verify that the ClaudeDelegationToolSet creates the correct
tools with shared state, matching the BrowserToolSet pattern.
"""

import tempfile
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.tool import ToolDefinition
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.claude import ClaudeDelegationToolSet
from openhands.tools.claude.definition import (
    TaskAction,
    TaskObservation,
    TaskOutputAction,
    TaskOutputObservation,
    TaskOutputTool,
    TaskStopAction,
    TaskStopObservation,
    TaskStopTool,
    TaskTool,
)
from openhands.tools.claude.impl import (
    DelegationManager,
    TaskExecutor,
    TaskOutputExecutor,
    TaskStopExecutor,
)


def _create_test_conv_state(temp_dir: str) -> ConversationState:
    """Helper to create a test conversation state."""
    llm = LLM(
        model="gpt-4o-mini",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
    )
    agent = Agent(llm=llm, tools=[])
    return ConversationState.create(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=temp_dir),
    )


def test_toolset_create_returns_three_tools():
    """ClaudeDelegationToolSet.create() should return exactly 3 tools."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)

        assert isinstance(tools, list)
        assert len(tools) == 3

        for tool in tools:
            assert isinstance(tool, ToolDefinition)


def test_toolset_creates_correct_tool_types():
    """The three tools should be TaskTool, TaskOutputTool, and TaskStopTool."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)

        tool_names = [tool.name for tool in tools]
        assert "task" in tool_names
        assert "task_output" in tool_names
        assert "task_stop" in tool_names


def test_toolset_tools_have_correct_types():
    """Each tool should be the correct ToolDefinition subclass."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)

        tool_by_name = {tool.name: tool for tool in tools}

        assert isinstance(tool_by_name["task"], TaskTool)
        assert isinstance(tool_by_name["task_output"], TaskOutputTool)
        assert isinstance(tool_by_name["task_stop"], TaskStopTool)


def test_toolset_tools_share_manager():
    """All three tools' executors should share the same ClaudeDelegationManager."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)

        executors = [tool.executor for tool in tools]
        assert all(e is not None for e in executors)

        # Extract the manager from each executor
        managers = []
        for executor in executors:
            assert hasattr(executor, "_manager")
            managers.append(getattr(executor, "_manager"))

        # All should reference the same manager instance
        assert managers[0] is managers[1]
        assert managers[1] is managers[2]
        assert isinstance(managers[0], DelegationManager)


def test_toolset_multiple_creates_have_separate_managers():
    """Multiple calls to create() should produce separate manager instances."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools1 = ClaudeDelegationToolSet.create(conv_state=conv_state)
        tools2 = ClaudeDelegationToolSet.create(conv_state=conv_state)

        executor1 = tools1[0].executor
        executor2 = tools2[0].executor
        assert executor1 is not None
        assert executor2 is not None
        assert isinstance(executor1, TaskExecutor)
        assert isinstance(executor2, TaskExecutor)

        assert executor1._manager is not executor2._manager


def test_toolset_tools_are_properly_configured():
    """Each tool should have description, action/observation types, and executor."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)

        for tool in tools:
            assert tool.description is not None
            assert tool.action_type is not None
            assert tool.observation_type is not None
            assert tool.executor is not None


def test_task_tool_has_correct_schema():
    """TaskTool should have the correct action and observation types."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)

        task_tool = next(t for t in tools if t.name == "task")
        assert task_tool.action_type is TaskAction
        assert task_tool.observation_type is TaskObservation


def test_task_output_tool_has_correct_schema():
    """TaskOutputTool should have the correct action and observation types."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)

        task_output_tool = next(t for t in tools if t.name == "task_output")
        assert task_output_tool.action_type is TaskOutputAction
        assert task_output_tool.observation_type is TaskOutputObservation


def test_task_stop_tool_has_correct_schema():
    """TaskStopTool should have the correct action and observation types."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)

        task_stop_tool = next(t for t in tools if t.name == "task_stop")
        assert task_stop_tool.action_type is TaskStopAction
        assert task_stop_tool.observation_type is TaskStopObservation


def test_toolset_tools_generate_valid_mcp_schemas():
    """All tools should generate valid MCP schemas."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)

        for tool in tools:
            mcp_tool = tool.to_mcp_tool()

            assert "name" in mcp_tool
            assert "description" in mcp_tool
            assert "inputSchema" in mcp_tool
            assert mcp_tool["name"] == tool.name

            input_schema = mcp_tool["inputSchema"]
            assert input_schema["type"] == "object"
            assert "properties" in input_schema


@pytest.mark.skip
def test_toolset_task_tool_description_includes_workspace():
    """TaskTool description should include the workspace path."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)

        task_tool = next(t for t in tools if t.name == "task")
        assert temp_dir in task_tool.description


def test_toolset_inheritance():
    """ClaudeDelegationToolSet should inherit from ToolDefinition."""
    assert issubclass(ClaudeDelegationToolSet, ToolDefinition)

    # The individual tools should NOT be instances of the ToolSet
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)
        for tool in tools:
            assert not isinstance(tool, ClaudeDelegationToolSet)
            assert isinstance(tool, ToolDefinition)


def test_toolset_name():
    """ClaudeDelegationToolSet should have the correct auto-derived name."""
    assert ClaudeDelegationToolSet.name == "claude_delegation_tool_set"


def test_tool_names():
    """Individual tools should have correct auto-derived names."""
    assert TaskTool.name == "task"
    assert TaskOutputTool.name == "task_output"
    assert TaskStopTool.name == "task_stop"


def test_task_tool_executor_type():
    """TaskTool should use TaskExecutor."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)

        task_tool = next(t for t in tools if t.name == "task")
        assert isinstance(task_tool.executor, TaskExecutor)


def test_task_output_tool_executor_type():
    """TaskOutputTool should use TaskOutputExecutor."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)

        tool = next(t for t in tools if t.name == "task_output")
        assert isinstance(tool.executor, TaskOutputExecutor)


def test_task_stop_tool_executor_type():
    """TaskStopTool should use TaskStopExecutor."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = ClaudeDelegationToolSet.create(conv_state=conv_state)

        tool = next(t for t in tools if t.name == "task_stop")
        assert isinstance(tool.executor, TaskStopExecutor)


def test_toolset_registered_in_registry():
    """ClaudeDelegationToolSet should be automatically registered."""
    from openhands.sdk.tool.registry import _REG

    assert "claude_delegation_tool_set" in _REG


def test_existing_delegate_tool_not_affected():
    """The existing DelegateTool should still be registered and working."""
    from openhands.sdk.tool.registry import _REG
    from openhands.tools.delegate import DelegateTool

    assert DelegateTool.name == "delegate"
    assert "delegate" in _REG
