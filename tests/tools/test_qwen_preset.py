"""Test Qwen preset."""

from pydantic import SecretStr

from openhands.sdk import Agent
from openhands.sdk.llm import LLM
from openhands.tools.nemotron import BashTool, StrReplaceTool
from openhands.tools.preset.qwen import get_qwen_agent, get_qwen_tools
from openhands.tools.task_tracker import TaskTrackerTool


def test_get_qwen_tools_basic():
    """Test that get_qwen_tools returns the correct tools."""
    tools = get_qwen_tools(enable_browser=False)

    # Should have bash, str_replace, and task_tracker
    assert len(tools) == 3

    tool_names = {tool.name for tool in tools}
    assert tool_names == {
        BashTool.name,
        StrReplaceTool.name,
        TaskTrackerTool.name,
    }


def test_get_qwen_tools_with_browser():
    """Test that get_qwen_tools includes browser tools when enabled."""
    tools = get_qwen_tools(enable_browser=True)

    # Should have bash, str_replace, task_tracker, and browser_tool_set
    assert len(tools) == 4

    tool_names = {tool.name for tool in tools}
    assert "browser_tool_set" in tool_names
    assert BashTool.name in tool_names
    assert StrReplaceTool.name in tool_names
    assert TaskTrackerTool.name in tool_names


def test_get_qwen_agent():
    """Test that get_qwen_agent creates an agent with the correct tools."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test")
    agent = get_qwen_agent(llm=llm, cli_mode=True)

    assert isinstance(agent, Agent)
    assert len(agent.tools) == 3

    tool_names = {tool.name for tool in agent.tools}
    assert tool_names == {
        BashTool.name,
        StrReplaceTool.name,
        TaskTrackerTool.name,
    }


def test_qwen_tools_use_anthropic_names():
    """Test that Qwen preset uses Anthropic-compatible tool names."""
    tools = get_qwen_tools(enable_browser=False)
    tool_names = {tool.name for tool in tools}

    # Verify it uses "bash" and "str_replace" (not "terminal" and "file_editor")
    assert "bash" in tool_names
    assert "str_replace" in tool_names
    assert "terminal" not in tool_names
    assert "file_editor" not in tool_names
