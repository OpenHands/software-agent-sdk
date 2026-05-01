"""Tests for OpenHandsCloudWorkspace settings methods.

OpenHandsCloudWorkspace inherits get_llm(), get_secrets(), and get_mcp_config()
from RemoteWorkspace. These methods fetch configuration from the agent-server's
/api/settings endpoints after the sandbox is provisioned.

For detailed unit tests of the settings methods themselves, see:
tests/sdk/workspace/remote/test_remote_workspace.py

This file tests the integration with OpenHandsCloudWorkspace-specific behavior.
"""

from unittest.mock import MagicMock, patch

import pytest

from openhands.workspace.cloud.workspace import OpenHandsCloudWorkspace


SANDBOX_ID = "sb-test-123"
CLOUD_URL = "https://app.all-hands.dev"


@pytest.fixture
def mock_workspace():
    """Create a workspace instance with mocked sandbox lifecycle."""
    with patch.object(
        OpenHandsCloudWorkspace, "model_post_init", lambda self, ctx: None
    ):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url=CLOUD_URL,
            cloud_api_key="test-api-key",
            host="http://localhost:8000",
        )
    # Simulate a running sandbox
    workspace._sandbox_id = SANDBOX_ID
    return workspace


class TestSettingsInheritance:
    """Tests that OpenHandsCloudWorkspace correctly inherits settings methods."""

    def test_get_llm_uses_agent_server_endpoint(self, mock_workspace):
        """get_llm calls agent-server /api/settings endpoint."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "agent_settings": {
                "llm": {
                    "model": "anthropic/claude-sonnet-4-20250514",
                    "api_key": "sk-test-key-123",
                    "base_url": "https://litellm.example.com",
                }
            }
        }
        mock_client.get.return_value = mock_response
        mock_workspace._client = mock_client

        llm = mock_workspace.get_llm()

        mock_client.get.assert_called_once_with(
            "/api/settings", params={"expose_secrets": "true"}
        )
        assert llm.model == "anthropic/claude-sonnet-4-20250514"
        assert llm.base_url == "https://litellm.example.com"

    def test_get_secrets_uses_agent_server_endpoint(self, mock_workspace):
        """get_secrets calls agent-server /api/settings/secrets endpoint."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "secrets": [
                {"name": "GITHUB_TOKEN", "description": "GitHub token"},
            ]
        }
        mock_client.get.return_value = mock_response
        mock_workspace._client = mock_client

        secrets = mock_workspace.get_secrets()

        mock_client.get.assert_called_once_with("/api/settings/secrets")
        assert "GITHUB_TOKEN" in secrets
        # URL should point to agent-server (host), not cloud_api_url
        assert secrets["GITHUB_TOKEN"].url.startswith(mock_workspace.host)

    def test_get_mcp_config_uses_agent_server_endpoint(self, mock_workspace):
        """get_mcp_config calls agent-server /api/settings endpoint."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "agent_settings": {
                "mcp_config": {
                    "sse_servers": [
                        {"url": "https://sse.example.com/mcp", "api_key": "key"}
                    ],
                    "shttp_servers": [],
                    "stdio_servers": [],
                }
            }
        }
        mock_client.get.return_value = mock_response
        mock_workspace._client = mock_client

        mcp_config = mock_workspace.get_mcp_config()

        mock_client.get.assert_called_once_with("/api/settings")
        assert "mcpServers" in mcp_config
        assert "sse_0" in mcp_config["mcpServers"]


class TestMcpConfigCompatibility:
    """Test MCP config output is compatible with fastmcp.MCPConfig."""

    def test_get_mcp_config_is_mcpconfig_compatible(self, mock_workspace):
        """get_mcp_config returns dict that can be validated by fastmcp.MCPConfig."""
        from fastmcp.mcp_config import MCPConfig

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "agent_settings": {
                "mcp_config": {
                    "sse_servers": [
                        {"url": "https://sse.example.com/mcp", "api_key": "key123"},
                    ],
                    "shttp_servers": [
                        {"url": "https://shttp.example.com/mcp", "api_key": None},
                    ],
                    "stdio_servers": [
                        {
                            "name": "fetch",
                            "command": "uvx",
                            "args": ["mcp-server-fetch"],
                        },
                    ],
                }
            }
        }
        mock_client.get.return_value = mock_response
        mock_workspace._client = mock_client

        mcp_config_dict = mock_workspace.get_mcp_config()

        # Should be parseable by MCPConfig
        config = MCPConfig.model_validate(mcp_config_dict)
        assert len(config.mcpServers) == 3
        assert "sse_0" in config.mcpServers
        assert "shttp_0" in config.mcpServers
        assert "fetch" in config.mcpServers
