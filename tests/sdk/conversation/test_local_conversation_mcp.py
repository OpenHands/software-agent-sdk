"""Tests for how LocalConversation wires MCP servers into a running agent."""

from pathlib import Path
from typing import Any, cast

import mcp.types as mcp_types
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config
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

    assert set(conversation.agent.tools_map) == {"replacement"}
    assert set(old_agent.tools_map) == {"initial"}
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
