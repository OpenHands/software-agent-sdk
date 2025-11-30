"""Test ACPAgent functionality."""

from unittest.mock import Mock

import pytest

from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.tool.spec import Tool


def test_acp_agent_initialization():
    """Test that ACPAgent can be initialized without LLM."""
    agent = ACPAgent(
        acp_command=["echo", "test"],
    )
    assert agent.acp_command == ["echo", "test"]
    assert agent.acp_args == []
    assert agent.acp_cwd is None
    assert agent.llm is None


def test_acp_agent_with_args():
    """Test ACPAgent initialization with additional arguments."""
    agent = ACPAgent(
        acp_command=["python", "-m", "acp_server"],
        acp_args=["--verbose"],
        acp_cwd="/tmp/test",
    )
    assert agent.acp_command == ["python", "-m", "acp_server"]
    assert agent.acp_args == ["--verbose"]
    assert agent.acp_cwd == "/tmp/test"
    assert agent.llm is None


def test_acp_agent_rejects_tools():
    """Test that ACPAgent raises error when tools are provided."""
    import openhands.sdk.agent.acp_agent as acp_module
    from openhands.sdk.conversation.state import ConversationState

    # Temporarily enable ACP to test the tools check
    original_available = acp_module._ACP_AVAILABLE
    try:
        acp_module._ACP_AVAILABLE = True
        agent = ACPAgent(
            acp_command=["test"],
            tools=[Tool(name="test_tool")],
        )
        state = Mock(spec=ConversationState)

        with pytest.raises(
            NotImplementedError, match="ACPAgent does not yet support custom tools"
        ):
            agent.init_state(state, on_event=Mock())
    finally:
        acp_module._ACP_AVAILABLE = original_available


def test_acp_agent_rejects_mcp_config():
    """Test that ACPAgent raises error when MCP config is provided."""
    import openhands.sdk.agent.acp_agent as acp_module
    from openhands.sdk.conversation.state import ConversationState

    # Temporarily enable ACP to test the MCP config check
    original_available = acp_module._ACP_AVAILABLE
    try:
        acp_module._ACP_AVAILABLE = True
        agent = ACPAgent(
            acp_command=["test"],
            mcp_config={"test": "config"},
        )
        state = Mock(spec=ConversationState)

        with pytest.raises(
            NotImplementedError, match="ACPAgent does not yet support MCP"
        ):
            agent.init_state(state, on_event=Mock())
    finally:
        acp_module._ACP_AVAILABLE = original_available


def test_acp_agent_missing_sdk():
    """Test that ACPAgent raises ImportError when SDK is not available."""
    from openhands.sdk.conversation.state import ConversationState

    agent = ACPAgent(acp_command=["test"])
    state = Mock(spec=ConversationState)

    # Temporarily disable ACP availability to test error handling
    import openhands.sdk.agent.acp_agent as acp_module

    original_available = acp_module._ACP_AVAILABLE
    try:
        acp_module._ACP_AVAILABLE = False
        with pytest.raises(
            ImportError, match="agent-client-protocol package is required"
        ):
            agent.init_state(state, on_event=Mock())
    finally:
        acp_module._ACP_AVAILABLE = original_available
