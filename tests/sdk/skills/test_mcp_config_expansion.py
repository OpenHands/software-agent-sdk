"""Tests for MCP config variable expansion with secrets."""

import json
import os

from fastmcp.mcp_config import RemoteMCPServer, StdioMCPServer

from openhands.sdk.skills.utils import expand_mcp_variables, load_mcp_config


class TestExpandMcpVariables:
    """Tests for expand_mcp_variables function."""

    def test_expand_with_pydantic_mcp_server_objects(self):
        """Test that expand_mcp_variables handles Pydantic MCP server objects.

        This reproduces a bug where the config dict contains RemoteMCPServer or
        StdioMCPServer Pydantic model objects (not plain dicts), causing:
            TypeError: Object of type RemoteMCPServer is not JSON serializable

        This happens when mcp_config is copied via dict(agent.mcp_config) which
        creates a shallow copy preserving the Pydantic objects as values.
        """
        # This is what the config looks like after dict(agent.mcp_config)
        # when the agent has Pydantic MCP server objects
        config = {
            "mcpServers": {
                "Notion": RemoteMCPServer(
                    url="https://mcp.notion.com/mcp",
                    auth="oauth",
                ),
                "fetch": StdioMCPServer(
                    command="uvx",
                    args=["mcp-server-fetch"],
                ),
                "context-layer": RemoteMCPServer(
                    url="https://example.com/api/mcp",
                    transport="streamable-http",
                    headers={"Authorization": "Bearer ${API_TOKEN}"},
                ),
            }
        }
        secrets = {"API_TOKEN": "secret-token-123"}

        # This should NOT raise TypeError
        result = expand_mcp_variables(config, {}, get_secret=secrets.get)

        # Verify the variable was expanded
        assert result["mcpServers"]["context-layer"]["headers"]["Authorization"] == (
            "Bearer secret-token-123"
        )
        # Verify other values are preserved
        assert result["mcpServers"]["Notion"]["url"] == "https://mcp.notion.com/mcp"
        assert result["mcpServers"]["fetch"]["command"] == "uvx"

    def test_expand_basic_variables(self):
        """Test expanding basic variables from the variables dict."""
        config = {
            "mcpServers": {
                "test-server": {
                    "command": "${SKILL_ROOT}/scripts/server.py",
                    "args": ["--port", "8080"],
                }
            }
        }
        variables = {"SKILL_ROOT": "/path/to/skill"}

        result = expand_mcp_variables(config, variables)

        assert result["mcpServers"]["test-server"]["command"] == (
            "/path/to/skill/scripts/server.py"
        )

    def test_expand_environment_variables(self):
        """Test expanding variables from environment."""
        os.environ["TEST_MCP_VAR"] = "env-value-123"
        try:
            config = {
                "mcpServers": {
                    "test-server": {
                        "url": "https://example.com/${TEST_MCP_VAR}/api",
                    }
                }
            }
            result = expand_mcp_variables(config, {})

            assert result["mcpServers"]["test-server"]["url"] == (
                "https://example.com/env-value-123/api"
            )
        finally:
            del os.environ["TEST_MCP_VAR"]

    def test_expand_secrets(self):
        """Test expanding variables via get_secret callback."""
        config = {
            "mcpServers": {
                "my-server": {
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer ${MCP_SECRET_TOKEN}"},
                }
            }
        }
        secrets = {"MCP_SECRET_TOKEN": "my-secret-value"}

        result = expand_mcp_variables(config, {}, get_secret=secrets.get)

        assert result["mcpServers"]["my-server"]["headers"]["Authorization"] == (
            "Bearer my-secret-value"
        )

    def test_variable_resolution_order(self):
        """Test that variables dict takes precedence over secrets and env."""
        os.environ["SHARED_VAR"] = "env-value"
        try:
            config = {
                "mcpServers": {
                    "test-server": {
                        "value1": "${SHARED_VAR}",
                        "value2": "${SECRET_VAR}",
                        "value3": "${ENV_VAR}",
                    }
                }
            }
            variables = {"SHARED_VAR": "variables-value"}
            secrets = {"SHARED_VAR": "secrets-value", "SECRET_VAR": "secret-value"}

            result = expand_mcp_variables(config, variables, get_secret=secrets.get)

            # variables dict should win over secrets and env
            assert result["mcpServers"]["test-server"]["value1"] == "variables-value"
            # secrets should be used when not in variables
            assert result["mcpServers"]["test-server"]["value2"] == "secret-value"
            # env should be used for ENV_VAR (not in variables or secrets)
            assert result["mcpServers"]["test-server"]["value3"] == "${ENV_VAR}"
        finally:
            del os.environ["SHARED_VAR"]

    def test_secrets_take_precedence_over_env(self):
        """Test that secrets take precedence over environment variables."""
        os.environ["MCP_TOKEN"] = "env-token"
        try:
            config = {
                "mcpServers": {
                    "test-server": {
                        "headers": {"Authorization": "Bearer ${MCP_TOKEN}"},
                    }
                }
            }
            secrets = {"MCP_TOKEN": "secret-token"}

            result = expand_mcp_variables(config, {}, get_secret=secrets.get)

            # secrets should win over env
            assert result["mcpServers"]["test-server"]["headers"]["Authorization"] == (
                "Bearer secret-token"
            )
        finally:
            del os.environ["MCP_TOKEN"]

    def test_default_values(self):
        """Test that default values are used when variable is not found."""
        config = {
            "mcpServers": {
                "test-server": {
                    "url": "${API_URL:-https://default.example.com}",
                    "timeout": "${TIMEOUT:-30}",
                }
            }
        }

        result = expand_mcp_variables(config, {})

        assert (
            result["mcpServers"]["test-server"]["url"] == "https://default.example.com"
        )
        assert result["mcpServers"]["test-server"]["timeout"] == "30"

    def test_default_not_used_when_secret_exists(self):
        """Test that default is not used when secret provides the value."""
        config = {
            "mcpServers": {
                "test-server": {
                    "url": "${API_URL:-https://default.example.com}",
                }
            }
        }
        secrets = {"API_URL": "https://secret.example.com"}

        result = expand_mcp_variables(config, {}, get_secret=secrets.get)

        assert (
            result["mcpServers"]["test-server"]["url"] == "https://secret.example.com"
        )

    def test_unexpanded_variables_remain_unchanged(self):
        """Test that unresolved variables remain as-is."""
        config = {
            "mcpServers": {
                "test-server": {
                    "url": "https://example.com/${UNKNOWN_VAR}/api",
                }
            }
        }

        result = expand_mcp_variables(config, {})

        # Variable should remain unchanged since it's not found
        assert result["mcpServers"]["test-server"]["url"] == (
            "https://example.com/${UNKNOWN_VAR}/api"
        )

    def test_multiple_variables_in_same_string(self):
        """Test expanding multiple variables in the same string."""
        config = {
            "mcpServers": {
                "test-server": {
                    "url": "https://${HOST}:${PORT}/${PATH}",
                }
            }
        }
        variables = {"HOST": "localhost"}
        secrets = {"PORT": "8080", "PATH": "api/v1"}

        result = expand_mcp_variables(config, variables, get_secret=secrets.get)

        assert result["mcpServers"]["test-server"]["url"] == (
            "https://localhost:8080/api/v1"
        )

    def test_no_get_secret_callback(self):
        """Test with no get_secret callback (default behavior)."""
        config = {
            "mcpServers": {
                "test-server": {"url": "${SKILL_ROOT}/api"},
            }
        }
        variables = {"SKILL_ROOT": "/path"}

        # Should work without get_secret
        result = expand_mcp_variables(config, variables, get_secret=None)

        assert result["mcpServers"]["test-server"]["url"] == "/path/api"

    def test_expand_with_pydantic_mcp_server_objects(self):
        """Test that Pydantic MCP server objects are properly serialized.

        When MCP config contains Pydantic models (RemoteMCPServer, StdioMCPServer)
        instead of plain dicts, they should be converted to dicts automatically.
        This regression test covers the fix for:
        TypeError: Object of type RemoteMCPServer is not JSON serializable
        """
        from fastmcp.mcp_config import RemoteMCPServer, StdioMCPServer

        config = {
            "mcpServers": {
                "Notion": RemoteMCPServer(
                    url="https://mcp.notion.com/mcp",
                    headers={},
                    auth="oauth",
                ),
                "fetch": StdioMCPServer(
                    command="uvx",
                    args=["mcp-server-fetch"],
                ),
            }
        }

        # This should not raise TypeError
        result = expand_mcp_variables(config, {})

        # Verify the result is a plain dict with expected values
        assert result["mcpServers"]["Notion"]["url"] == "https://mcp.notion.com/mcp"
        assert result["mcpServers"]["fetch"]["command"] == "uvx"
        assert result["mcpServers"]["fetch"]["args"] == ["mcp-server-fetch"]


class TestLoadMcpConfigWithSecrets:
    """Tests for load_mcp_config function with secrets."""

    def test_load_mcp_config_with_secrets(self, tmp_path):
        """Test loading .mcp.json with secrets expansion."""
        mcp_json = tmp_path / ".mcp.json"
        config = {
            "mcpServers": {
                "my-server": {
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer ${API_SECRET}"},
                }
            }
        }
        mcp_json.write_text(json.dumps(config))

        secrets = {"API_SECRET": "my-secret-token"}

        result = load_mcp_config(mcp_json, skill_root=tmp_path, get_secret=secrets.get)

        assert result["mcpServers"]["my-server"]["headers"]["Authorization"] == (
            "Bearer my-secret-token"
        )

    def test_load_mcp_config_without_secrets(self, tmp_path):
        """Test loading .mcp.json without secrets (backward compatibility)."""
        mcp_json = tmp_path / ".mcp.json"
        config = {
            "mcpServers": {
                "my-server": {
                    "command": "${SKILL_ROOT}/server.py",
                    "args": [],
                }
            }
        }
        mcp_json.write_text(json.dumps(config))

        result = load_mcp_config(mcp_json, skill_root=tmp_path)

        assert result["mcpServers"]["my-server"]["command"] == f"{tmp_path}/server.py"

    def test_load_mcp_config_skill_root_takes_precedence(self, tmp_path):
        """Test that SKILL_ROOT from skill_root param takes precedence over secrets."""
        mcp_json = tmp_path / ".mcp.json"
        config = {
            "mcpServers": {
                "my-server": {
                    "command": "${SKILL_ROOT}/server.py",
                }
            }
        }
        mcp_json.write_text(json.dumps(config))

        # Even if secrets has SKILL_ROOT, the param should win
        secrets = {"SKILL_ROOT": "/wrong/path"}

        result = load_mcp_config(mcp_json, skill_root=tmp_path, get_secret=secrets.get)

        assert result["mcpServers"]["my-server"]["command"] == f"{tmp_path}/server.py"

    def test_load_mcp_config_combined_variables_and_secrets(self, tmp_path):
        """Test loading config that uses both skill_root and secrets."""
        mcp_json = tmp_path / ".mcp.json"
        config = {
            "mcpServers": {
                "my-server": {
                    "command": "${SKILL_ROOT}/server.py",
                    "env": {
                        "API_KEY": "${API_KEY}",
                        "DB_URL": "${DATABASE_URL:-sqlite://default.db}",
                    },
                }
            }
        }
        mcp_json.write_text(json.dumps(config))

        secrets = {"API_KEY": "secret-key-123"}

        result = load_mcp_config(mcp_json, skill_root=tmp_path, get_secret=secrets.get)

        assert result["mcpServers"]["my-server"]["command"] == f"{tmp_path}/server.py"
        assert result["mcpServers"]["my-server"]["env"]["API_KEY"] == "secret-key-123"
        assert (
            result["mcpServers"]["my-server"]["env"]["DB_URL"] == "sqlite://default.db"
        )
