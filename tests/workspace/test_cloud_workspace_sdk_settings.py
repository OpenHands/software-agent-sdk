"""Tests for OpenHandsCloudWorkspace.get_llm(), get_secrets(), and get_mcp_config() methods.

get_llm() returns a real LLM with the raw api_key from SaaS.
get_secrets() returns LookupSecret references — raw values only flow
SaaS→sandbox, never to the SDK client.
get_mcp_config() returns MCP server configuration in SDK Agent format.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk.secret import LookupSecret
from openhands.workspace.cloud.workspace import OpenHandsCloudWorkspace


SANDBOX_ID = "sb-test-123"
SESSION_KEY = "session-key-abc"
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
    workspace._session_api_key = SESSION_KEY
    return workspace


class TestGetLLM:
    """Tests for OpenHandsCloudWorkspace.get_llm()."""

    def test_get_llm_returns_usable_llm(self, mock_workspace):
        """get_llm fetches SaaS config and returns a usable LLM."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "llm_model": "anthropic/claude-sonnet-4-20250514",
            "llm_api_key": "sk-test-key-123",
            "llm_base_url": "https://litellm.example.com",
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ) as mock_req:
            llm = mock_workspace.get_llm()

        mock_req.assert_called_once_with(
            "GET",
            f"{CLOUD_URL}/api/v1/users/me",
            params={"expose_secrets": "true"},
            headers={"X-Session-API-Key": SESSION_KEY},
        )
        assert llm.model == "anthropic/claude-sonnet-4-20250514"
        # api_key is a real SecretStr (LLM validator converts str → SecretStr)
        assert isinstance(llm.api_key, SecretStr)
        assert llm.api_key.get_secret_value() == "sk-test-key-123"
        assert llm.base_url == "https://litellm.example.com"

    def test_get_llm_allows_overrides(self, mock_workspace):
        """User-provided kwargs override SaaS settings."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "llm_model": "anthropic/claude-sonnet-4-20250514",
            "llm_api_key": "sk-test-key",
            "llm_base_url": None,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            llm = mock_workspace.get_llm(model="gpt-4o", temperature=0.5)

        assert llm.model == "gpt-4o"
        assert llm.temperature == 0.5
        assert isinstance(llm.api_key, SecretStr)

    def test_get_llm_no_api_key_still_works(self, mock_workspace):
        """If no API key is configured, the LLM gets api_key=None."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "llm_model": "gpt-4o",
            "llm_api_key": None,
            "llm_base_url": None,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            llm = mock_workspace.get_llm()

        assert llm.model == "gpt-4o"
        assert llm.api_key is None

    def test_get_llm_raises_when_no_sandbox(self, mock_workspace):
        """get_llm raises RuntimeError when sandbox is not running."""
        mock_workspace._sandbox_id = None
        with pytest.raises(RuntimeError, match="Sandbox is not running"):
            mock_workspace.get_llm()


class TestGetSecrets:
    """Tests for OpenHandsCloudWorkspace.get_secrets()."""

    def test_get_all_secrets_returns_lookup_secrets(self, mock_workspace):
        """get_secrets returns LookupSecret instances, not raw values."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "secrets": [
                {"name": "GITHUB_TOKEN", "description": "GitHub token"},
                {"name": "MY_API_KEY", "description": None},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_settings_request", return_value=mock_response
        ) as mock_req:
            secrets = mock_workspace.get_secrets()

        mock_req.assert_called_once_with(
            "GET",
            f"{CLOUD_URL}/api/v1/sandboxes/{SANDBOX_ID}/settings/secrets",
        )

        assert len(secrets) == 2
        assert "GITHUB_TOKEN" in secrets
        assert "MY_API_KEY" in secrets

        gh_secret = secrets["GITHUB_TOKEN"]
        assert isinstance(gh_secret, LookupSecret)
        assert gh_secret.url == (
            f"{CLOUD_URL}/api/v1/sandboxes/{SANDBOX_ID}/settings/secrets/GITHUB_TOKEN"
        )
        assert gh_secret.headers == {"X-Session-API-Key": SESSION_KEY}
        assert gh_secret.description == "GitHub token"

    def test_get_secrets_filters_by_name(self, mock_workspace):
        """get_secrets(names=[...]) filters client-side."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "secrets": [
                {"name": "GITHUB_TOKEN", "description": "GitHub token"},
                {"name": "MY_API_KEY", "description": None},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_settings_request", return_value=mock_response
        ):
            secrets = mock_workspace.get_secrets(names=["GITHUB_TOKEN"])

        assert len(secrets) == 1
        assert "GITHUB_TOKEN" in secrets
        assert "MY_API_KEY" not in secrets

    def test_get_secrets_empty(self, mock_workspace):
        """Empty secrets list returns empty dict."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"secrets": []}
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_settings_request", return_value=mock_response
        ):
            secrets = mock_workspace.get_secrets()

        assert secrets == {}

    def test_get_secrets_raises_when_no_sandbox(self, mock_workspace):
        """get_secrets raises RuntimeError when sandbox is not running."""
        mock_workspace._sandbox_id = None
        with pytest.raises(RuntimeError, match="Sandbox is not running"):
            mock_workspace.get_secrets()


class TestGetMcpConfig:
    """Tests for OpenHandsCloudWorkspace.get_mcp_config()."""

    def test_get_mcp_config_returns_empty_when_no_config(self, mock_workspace):
        """get_mcp_config returns empty dict when no MCP config is set."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "llm_model": "gpt-4o",
            "mcp_config": None,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            mcp_config = mock_workspace.get_mcp_config()

        assert mcp_config == {}

    def test_get_mcp_config_transforms_sse_servers(self, mock_workspace):
        """get_mcp_config correctly transforms SSE servers."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "mcp_config": {
                "sse_servers": [
                    {"url": "https://sse.example.com/mcp", "api_key": "sse-key-123"},
                    {"url": "https://sse2.example.com/mcp", "api_key": None},
                ],
                "shttp_servers": [],
                "stdio_servers": [],
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ) as mock_req:
            mcp_config = mock_workspace.get_mcp_config()

        mock_req.assert_called_once_with(
            "GET",
            f"{CLOUD_URL}/api/v1/users/me",
            headers={"X-Session-API-Key": SESSION_KEY},
        )

        assert "mcpServers" in mcp_config
        servers = mcp_config["mcpServers"]
        assert len(servers) == 2

        # First SSE server with API key
        assert servers["sse_0"]["url"] == "https://sse.example.com/mcp"
        assert servers["sse_0"]["transport"] == "sse"
        assert servers["sse_0"]["headers"]["Authorization"] == "Bearer sse-key-123"

        # Second SSE server without API key
        assert servers["sse_1"]["url"] == "https://sse2.example.com/mcp"
        assert servers["sse_1"]["transport"] == "sse"
        assert "headers" not in servers["sse_1"]

    def test_get_mcp_config_transforms_shttp_servers(self, mock_workspace):
        """get_mcp_config correctly transforms SHTTP servers."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "mcp_config": {
                "sse_servers": [],
                "shttp_servers": [
                    {
                        "url": "https://shttp.example.com/mcp",
                        "api_key": "shttp-key",
                        "timeout": 120,
                    },
                ],
                "stdio_servers": [],
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            mcp_config = mock_workspace.get_mcp_config()

        servers = mcp_config["mcpServers"]
        assert len(servers) == 1

        assert servers["shttp_0"]["url"] == "https://shttp.example.com/mcp"
        assert servers["shttp_0"]["transport"] == "streamable-http"
        assert servers["shttp_0"]["headers"]["Authorization"] == "Bearer shttp-key"
        assert servers["shttp_0"]["timeout"] == 120

    def test_get_mcp_config_transforms_stdio_servers(self, mock_workspace):
        """get_mcp_config correctly transforms STDIO servers."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "mcp_config": {
                "sse_servers": [],
                "shttp_servers": [],
                "stdio_servers": [
                    {
                        "name": "my-stdio-server",
                        "command": "npx",
                        "args": ["-y", "mcp-server-fetch"],
                        "env": {"MY_VAR": "value"},
                    },
                ],
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            mcp_config = mock_workspace.get_mcp_config()

        servers = mcp_config["mcpServers"]
        assert len(servers) == 1

        # STDIO servers use their explicit name
        assert "my-stdio-server" in servers
        assert servers["my-stdio-server"]["command"] == "npx"
        assert servers["my-stdio-server"]["args"] == ["-y", "mcp-server-fetch"]
        assert servers["my-stdio-server"]["env"] == {"MY_VAR": "value"}

    def test_get_mcp_config_mixed_server_types(self, mock_workspace):
        """get_mcp_config correctly handles mixed server types."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "mcp_config": {
                "sse_servers": [
                    {"url": "https://sse.example.com/mcp", "api_key": None},
                ],
                "shttp_servers": [
                    {"url": "https://shttp.example.com/mcp", "api_key": None},
                ],
                "stdio_servers": [
                    {"name": "fetch", "command": "uvx", "args": ["mcp-server-fetch"]},
                ],
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            mcp_config = mock_workspace.get_mcp_config()

        servers = mcp_config["mcpServers"]
        assert len(servers) == 3
        assert "sse_0" in servers
        assert "shttp_0" in servers
        assert "fetch" in servers

    def test_get_mcp_config_raises_when_no_sandbox(self, mock_workspace):
        """get_mcp_config raises RuntimeError when sandbox is not running."""
        mock_workspace._sandbox_id = None
        with pytest.raises(RuntimeError, match="Sandbox is not running"):
            mock_workspace.get_mcp_config()

    def test_get_mcp_config_returns_empty_when_all_lists_empty(self, mock_workspace):
        """get_mcp_config returns empty dict when all server lists are empty."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "mcp_config": {
                "sse_servers": [],
                "shttp_servers": [],
                "stdio_servers": [],
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            mcp_config = mock_workspace.get_mcp_config()

        assert mcp_config == {}

    def test_get_mcp_config_is_mcpconfig_compatible(self, mock_workspace):
        """get_mcp_config returns dict that can be validated by fastmcp.MCPConfig."""
        from fastmcp.mcp_config import MCPConfig

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "mcp_config": {
                "sse_servers": [
                    {"url": "https://sse.example.com/mcp", "api_key": "key123"},
                ],
                "shttp_servers": [
                    {"url": "https://shttp.example.com/mcp", "api_key": None},
                ],
                "stdio_servers": [
                    {"name": "fetch", "command": "uvx", "args": ["mcp-server-fetch"]},
                ],
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace, "_send_api_request", return_value=mock_response
        ):
            mcp_config_dict = mock_workspace.get_mcp_config()

        # Should be parseable by MCPConfig
        config = MCPConfig.model_validate(mcp_config_dict)
        assert len(config.mcpServers) == 3
        assert "sse_0" in config.mcpServers
        assert "shttp_0" in config.mcpServers
        assert "fetch" in config.mcpServers


class TestRetry:
    """Tests for retry behaviour on get_llm and get_secrets."""

    def test_get_llm_retries_on_server_error(self, mock_workspace):
        """get_llm retries on 5xx and succeeds on the next attempt."""
        error_response = httpx.Response(
            status_code=502, request=httpx.Request("GET", "http://x")
        )
        ok_response = MagicMock()
        ok_response.json.return_value = {
            "llm_model": "gpt-4o",
            "llm_api_key": "sk-ok",
            "llm_base_url": None,
        }
        ok_response.raise_for_status = MagicMock()

        with patch.object(
            mock_workspace,
            "_send_api_request",
            side_effect=[
                httpx.HTTPStatusError(
                    "Bad Gateway",
                    request=error_response.request,
                    response=error_response,
                ),
                ok_response,
            ],
        ):
            llm = mock_workspace.get_llm()

        assert llm.model == "gpt-4o"

    def test_get_llm_no_retry_on_client_error(self, mock_workspace):
        """get_llm does NOT retry on 4xx errors."""
        error_response = httpx.Response(
            status_code=401, request=httpx.Request("GET", "http://x")
        )

        with patch.object(
            mock_workspace,
            "_send_api_request",
            side_effect=httpx.HTTPStatusError(
                "Unauthorized",
                request=error_response.request,
                response=error_response,
            ),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                mock_workspace.get_llm()

    def test_get_secrets_retries_on_server_error(self, mock_workspace):
        """_send_settings_request retries on 5xx for get_secrets."""
        ok_response = MagicMock()
        ok_response.json.return_value = {
            "secrets": [{"name": "TOK", "description": None}]
        }
        ok_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.request.side_effect = [
                httpx.Response(
                    status_code=503,
                    request=httpx.Request("GET", "http://x"),
                ),
                ok_response,
            ]
            # The first call raises on raise_for_status, second succeeds
            secrets = mock_workspace.get_secrets()

        assert "TOK" in secrets
