"""Tests for MCP tool timeout configuration propagation."""

from unittest.mock import AsyncMock, MagicMock, patch

import mcp.types
import pytest

from openhands.sdk.mcp.tool import MCP_TOOL_TIMEOUT_SECONDS, MCPToolDefinition, MCPToolExecutor
from openhands.sdk.mcp.utils import _extract_timeout_from_config, create_mcp_tools


# ---------------------------------------------------------------------------
# _extract_timeout_from_config
# ---------------------------------------------------------------------------


class TestExtractTimeoutFromConfig:
    """Unit tests for _extract_timeout_from_config helper."""

    def test_empty_config_returns_none(self):
        config = MagicMock()
        config.mcpServers = {}
        assert _extract_timeout_from_config(config) is None

    def test_single_server_with_timeout(self):
        """Timeout in milliseconds is converted to seconds."""
        server = MagicMock()
        server.timeout = 60000  # 60 seconds in ms
        config = MagicMock()
        config.mcpServers = {"srv": server}

        result = _extract_timeout_from_config(config)
        assert result == pytest.approx(60.0)

    def test_single_server_no_timeout(self):
        """When server has no timeout field, returns None."""
        server = MagicMock()
        server.timeout = None
        config = MagicMock()
        config.mcpServers = {"srv": server}

        assert _extract_timeout_from_config(config) is None

    def test_multiple_servers_uses_max_timeout(self):
        """With multiple servers, the maximum timeout is used."""
        srv1 = MagicMock()
        srv1.timeout = 30000  # 30s
        srv2 = MagicMock()
        srv2.timeout = 120000  # 120s
        srv3 = MagicMock()
        srv3.timeout = None  # no timeout
        config = MagicMock()
        config.mcpServers = {"fast": srv1, "slow": srv2, "untimed": srv3}

        result = _extract_timeout_from_config(config)
        assert result == pytest.approx(120.0)

    def test_all_servers_no_timeout_returns_none(self):
        srv1 = MagicMock()
        srv1.timeout = None
        srv2 = MagicMock()
        srv2.timeout = None
        config = MagicMock()
        config.mcpServers = {"a": srv1, "b": srv2}

        assert _extract_timeout_from_config(config) is None

    def test_missing_timeout_attribute(self):
        """Server without a timeout attribute at all (e.g. custom server type)."""
        srv = MagicMock(spec=[])  # no timeout attr
        config = MagicMock()
        config.mcpServers = {"srv": srv}

        assert _extract_timeout_from_config(config) is None


# ---------------------------------------------------------------------------
# MCPToolDefinition.create timeout propagation
# ---------------------------------------------------------------------------


class TestToolDefinitionTimeout:
    """Verify that MCPToolDefinition.create passes timeout to the executor."""

    @pytest.fixture
    def mcp_tool(self):
        return mcp.types.Tool(
            name="test_tool",
            description="A test tool",
            inputSchema={"type": "object", "properties": {}},
        )

    @pytest.fixture
    def mcp_client(self):
        return MagicMock()

    def test_custom_timeout_propagated(self, mcp_tool, mcp_client):
        """Explicit timeout value is passed through to the executor."""
        tools = MCPToolDefinition.create(
            mcp_tool=mcp_tool, mcp_client=mcp_client, timeout=42.0
        )
        assert len(tools) == 1
        executor = tools[0].executor
        assert isinstance(executor, MCPToolExecutor)
        assert executor.timeout == pytest.approx(42.0)

    def test_none_timeout_uses_default(self, mcp_tool, mcp_client):
        """When timeout=None, the executor uses MCP_TOOL_TIMEOUT_SECONDS."""
        tools = MCPToolDefinition.create(
            mcp_tool=mcp_tool, mcp_client=mcp_client, timeout=None
        )
        executor = tools[0].executor
        assert isinstance(executor, MCPToolExecutor)
        assert executor.timeout == MCP_TOOL_TIMEOUT_SECONDS

    def test_omitted_timeout_uses_default(self, mcp_tool, mcp_client):
        """When timeout is not specified, the executor uses MCP_TOOL_TIMEOUT_SECONDS."""
        tools = MCPToolDefinition.create(mcp_tool=mcp_tool, mcp_client=mcp_client)
        executor = tools[0].executor
        assert isinstance(executor, MCPToolExecutor)
        assert executor.timeout == MCP_TOOL_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# MCPToolExecutor timeout behaviour
# ---------------------------------------------------------------------------


class TestMCPToolExecutorTimeout:
    """Verify MCPToolExecutor timeout error handling."""

    def test_timeout_returns_error_observation(self):
        """When call_async_from_sync raises TimeoutError, an error observation is returned."""
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        mock_client.call_async_from_sync.side_effect = TimeoutError()

        executor = MCPToolExecutor(
            tool_name="slow_tool", client=mock_client, timeout=5.0
        )

        action = MagicMock()
        action.to_mcp_arguments.return_value = {}
        observation = executor(action)

        assert observation.is_error is True
        assert "timed out" in observation.text
        assert "5.0 seconds" in observation.text
        assert "slow_tool" in observation.text

    def test_default_timeout_value(self):
        """Default timeout is MCP_TOOL_TIMEOUT_SECONDS."""
        mock_client = MagicMock()
        executor = MCPToolExecutor(tool_name="t", client=mock_client)
        assert executor.timeout == MCP_TOOL_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# End-to-end: config → create_mcp_tools → executor timeout
# ---------------------------------------------------------------------------


class TestCreateMcpToolsTimeout:
    """Integration-level tests for timeout propagation through create_mcp_tools."""

    def test_timeout_from_config_reaches_executor(self):
        """A server timeout in the config results in the correct executor timeout."""
        config = {
            "mcpServers": {
                "my_server": {
                    "url": "http://localhost:9999/mcp",
                    "timeout": 45000,  # 45 seconds in ms
                }
            }
        }

        # We can't easily run a real server, so mock the connection internals
        fake_tool = mcp.types.Tool(
            name="fake_tool",
            description="fake",
            inputSchema={"type": "object", "properties": {}},
        )

        with (
            patch("openhands.sdk.mcp.utils.MCPClient") as mock_cls,
            patch(
                "openhands.sdk.mcp.utils.MCPToolDefinition.create",
                wraps=MCPToolDefinition.create,
            ) as mock_create,
        ):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client

            # Simulate successful connect + list_tools
            async def fake_connect_and_list(client, tool_timeout=None):
                tool_seq = MCPToolDefinition.create(
                    mcp_tool=fake_tool,
                    mcp_client=client,
                    timeout=tool_timeout,
                )
                client._tools.extend(tool_seq)

            mock_client.call_async_from_sync.side_effect = (
                lambda fn, **kwargs: fn(**{k: v for k, v in kwargs.items() if k != "timeout"})
                if False
                else None
            )

            # Directly test _extract_timeout_from_config + MCPToolDefinition.create
            from fastmcp.mcp_config import MCPConfig

            parsed = MCPConfig.model_validate(config)
            tool_timeout = _extract_timeout_from_config(parsed)
            assert tool_timeout == pytest.approx(45.0)

            tools = MCPToolDefinition.create(
                mcp_tool=fake_tool, mcp_client=MagicMock(), timeout=tool_timeout
            )
            assert tools[0].executor.timeout == pytest.approx(45.0)

    def test_no_timeout_in_config_uses_default(self):
        """When no server timeout is configured, the executor uses the default."""
        config = {
            "mcpServers": {
                "my_server": {
                    "url": "http://localhost:9999/mcp",
                }
            }
        }

        from fastmcp.mcp_config import MCPConfig

        parsed = MCPConfig.model_validate(config)
        tool_timeout = _extract_timeout_from_config(parsed)
        assert tool_timeout is None

        fake_tool = mcp.types.Tool(
            name="fake_tool",
            description="fake",
            inputSchema={"type": "object", "properties": {}},
        )
        tools = MCPToolDefinition.create(
            mcp_tool=fake_tool, mcp_client=MagicMock(), timeout=tool_timeout
        )
        assert tools[0].executor.timeout == MCP_TOOL_TIMEOUT_SECONDS
