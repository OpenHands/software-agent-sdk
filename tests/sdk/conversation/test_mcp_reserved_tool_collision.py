from pathlib import Path
from unittest.mock import MagicMock

import mcp.types
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.tool import Tool
from openhands.tools.terminal import TerminalTool  # noqa: F401 - registers tool


def _fake_mcp_tool(name: str, desc: str = "test mcp tool") -> MCPToolDefinition:
    client = MagicMock(spec=MCPClient)
    raw = mcp.types.Tool(
        name=name,
        description=desc,
        inputSchema={"type": "object", "properties": {"x": {"type": "string"}}},
    )
    return MCPToolDefinition.create(mcp_tool=raw, mcp_client=client)[0]


def test_single_mcp_server_reserved_tool_name_does_not_crash_native_executor(
    tmp_path: Path,
):
    llm = LLM(model="gpt-4o", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(
        llm=llm,
        tools=[Tool(name="terminal")],
        mcp_config={
            "ext": {"command": "echo", "args": ["noop"]},
        },
    )
    conv = LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        visualizer=None,
        delete_on_close=False,
    )
    fake_client = MagicMock()
    fake_client.tools = [
        _fake_mcp_tool("terminal", "colliding MCP terminal"),
        _fake_mcp_tool("sibling_check", "valid sibling MCP tool"),
        _fake_mcp_tool("sibling_check", "duplicate declaration"),
    ]
    conv._mcp_tool_provider = MagicMock()
    conv._mcp_tool_provider.create_tools.return_value = fake_client

    conv._ensure_agent_ready()

    tools_map = conv.agent.tools_map
    assert "terminal" in tools_map
    assert not isinstance(tools_map["terminal"], MCPToolDefinition)
    assert "ext_terminal" in tools_map
    assert isinstance(tools_map["ext_terminal"], MCPToolDefinition)
    assert tools_map["ext_terminal"].executor.server_tool_name == "terminal"
    assert "sibling_check" in tools_map
    assert "ext_sibling_check" in tools_map
