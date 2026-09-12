"""Tests for how LocalConversation wires MCP servers into a running agent."""

import socket
from pathlib import Path
from typing import Any, cast

import mcp.types as mcp_types
import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config
from openhands.sdk.mcp.exceptions import MCPConnectionError
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.mcp.utils import MCPToolProvider


class EmptyMCPClient:
    def __init__(self) -> None:
        self.tools: list[MCPToolDefinition] = []
        self._tools_reconciled_callback: Any = None

    def sync_close(self) -> None:
        pass


class RecordingMCPToolProvider:
    """Records every attempt to open an MCP connection."""

    def __init__(self, client: EmptyMCPClient | None = None) -> None:
        self.calls: list[dict[str, MCPServer]] = []
        self.client = client if client is not None else EmptyMCPClient()

    def create_tools(
        self,
        mcp_config: dict[str, MCPServer],
        timeout: float = 30.0,
        *,
        on_tools_changed: Any = None,
        on_tools_reconciled: Any = None,
    ) -> MCPClient:
        self.calls.append(mcp_config)
        self.client._tools_reconciled_callback = on_tools_reconciled
        return cast(MCPClient, self.client)


def test_disabling_every_server_skips_the_mcp_connection(tmp_path: Path) -> None:
    """The agent still starts; it just has no MCP servers to reach."""
    provider = RecordingMCPToolProvider()
    agent = Agent(
        llm=LLM(model="test-model", api_key=SecretStr("test-key")),
        tools=[],
        mcp_config=coerce_mcp_config({"fetch": {"command": "uvx", "enabled": False}}),
    )
    conversation = LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        visualizer=None,
        mcp_tool_provider=provider,
    )

    conversation._ensure_agent_ready()

    assert provider.calls == []
    conversation.close()


def test_reconciliation_targets_replaced_agent(tmp_path: Path) -> None:
    client = EmptyMCPClient()
    initial = MCPToolDefinition.create(
        mcp_tool=mcp_types.Tool(
            name="initial",
            description="initial",
            inputSchema={"type": "object", "properties": {}},
        ),
        mcp_client=cast(MCPClient, client),
    )[0]
    client.tools = [initial]
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_config=coerce_mcp_config({"fake": {"command": "true"}}),
        ),
        workspace=str(tmp_path),
        visualizer=None,
        mcp_tool_provider=RecordingMCPToolProvider(client),
    )
    conversation._ensure_agent_ready()
    old_agent = conversation.agent
    conversation.agent = old_agent.model_copy()
    replacement = MCPToolDefinition.create(
        mcp_tool=mcp_types.Tool(
            name="replacement",
            description="replacement",
            inputSchema={"type": "object", "properties": {}},
        ),
        mcp_client=cast(MCPClient, client),
    )[0]

    client._tools_reconciled_callback(cast(MCPClient, client), [replacement])

    updated_mcp_tools = {
        name
        for name, tool in conversation.agent.tools_map.items()
        if isinstance(tool, MCPToolDefinition)
    }
    old_mcp_tools = {
        name
        for name, tool in old_agent.tools_map.items()
        if isinstance(tool, MCPToolDefinition)
    }
    assert updated_mcp_tools == {"replacement"}
    assert old_mcp_tools == {"initial"}
    conversation.close()


class LegacyMCPToolProvider:
    """A custom provider written against the pre-reconciliation protocol."""

    def create_tools(
        self,
        mcp_config: dict[str, MCPServer],
        timeout: float = 30.0,
        *,
        on_tools_changed: Any = None,
    ) -> MCPClient:
        return cast(MCPClient, EmptyMCPClient())


def test_legacy_provider_without_on_tools_reconciled_still_works(
    tmp_path: Path,
) -> None:
    """A custom MCPToolProvider that predates on_tools_reconciled must not
    break; it just won't receive full-snapshot reconciliation."""
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_config=coerce_mcp_config({"fake": {"command": "true"}}),
        ),
        workspace=str(tmp_path),
        visualizer=None,
        # Deliberately incompatible with the current MCPToolProvider
        # protocol shape; that's the scenario under test.
        mcp_tool_provider=cast(MCPToolProvider, LegacyMCPToolProvider()),
    )

    conversation._ensure_agent_ready()

    conversation.close()


class _KwargsMCPToolProvider:
    """A provider that accepts arbitrary keywords via **kwargs."""

    def create_tools(
        self, mcp_config: dict[str, MCPServer], timeout: float = 30.0, **kwargs: Any
    ) -> MCPClient:
        return cast(MCPClient, EmptyMCPClient())


def test_provider_supports_on_tools_reconciled() -> None:
    from openhands.sdk.mcp.utils import (
        DefaultMCPToolProvider,
        provider_supports_on_tools_reconciled,
    )

    assert provider_supports_on_tools_reconciled(DefaultMCPToolProvider())
    assert provider_supports_on_tools_reconciled(RecordingMCPToolProvider())
    assert provider_supports_on_tools_reconciled(_KwargsMCPToolProvider())
    assert not provider_supports_on_tools_reconciled(
        cast(MCPToolProvider, LegacyMCPToolProvider())
    )


def test_unreachable_mcp_server_allows_agent_to_become_ready(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Unreachable MCP server does not abort agent readiness."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        unused_port = s.getsockname()[1]

    agent = Agent(
        llm=LLM(model="test-model", api_key=SecretStr("test-key")),
        tools=[],
        mcp_config=coerce_mcp_config(
            {"offline": {"url": f"http://127.0.0.1:{unused_port}/mcp"}}
        ),
    )
    conversation = LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        visualizer=None,
    )

    with caplog.at_level("WARNING"):
        conversation._ensure_agent_ready()

    mcp_tools = [
        t
        for t in conversation.agent.tools_map.values()
        if isinstance(t, MCPToolDefinition)
    ]
    assert mcp_tools == []
    assert "offline" in caplog.text
    assert str(unused_port) in caplog.text
    conversation.close()


def test_unreachable_mcp_server_strict_mode_raises(tmp_path: Path) -> None:
    """When strict=True on MCPServer, unreachable server raises."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        unused_port = s.getsockname()[1]

    agent = Agent(
        llm=LLM(model="test-model", api_key=SecretStr("test-key")),
        tools=[],
        mcp_config=coerce_mcp_config(
            {
                "offline": {
                    "url": f"http://127.0.0.1:{unused_port}/mcp",
                    "strict": True,
                }
            }
        ),
    )
    conversation = LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        visualizer=None,
    )

    with pytest.raises(MCPConnectionError) as exc_info:
        conversation._ensure_agent_ready()

    assert exc_info.value.server_name == "offline"
    assert str(unused_port) in str(exc_info.value.url)
    conversation.close()
