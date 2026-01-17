"""Tests for Agent MCP config merging from plugin via AgentContext."""

from pathlib import Path
from unittest.mock import patch

from openhands.sdk import LLM, Conversation
from openhands.sdk.agent import Agent
from openhands.sdk.context.agent_context import AgentContext


def create_test_plugin_with_mcp(tmp_path: Path, mcp_config: dict) -> Path:
    """Create a test plugin with MCP config."""
    import json

    plugin_dir = tmp_path / "mcp-plugin"
    plugin_dir.mkdir(parents=True)

    # Create manifest
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir()
    manifest_file = manifest_dir / "plugin.json"
    manifest_file.write_text('{"name": "mcp-plugin", "version": "1.0.0"}')

    # Create MCP config
    mcp_json = plugin_dir / ".mcp.json"
    mcp_json.write_text(json.dumps(mcp_config))

    return plugin_dir


def test_agent_merges_plugin_mcp_config(tmp_path: Path):
    """Test that Agent merges plugin MCP config during initialization."""
    plugin_mcp = {
        "mcpServers": {
            "plugin-server": {
                "command": "echo",
                "args": ["plugin"],
            }
        }
    }
    plugin_dir = create_test_plugin_with_mcp(tmp_path, plugin_mcp)

    # Create AgentContext with plugin
    agent_context = AgentContext(plugin_source=str(plugin_dir))

    # Verify plugin MCP config is loaded
    assert agent_context.plugin_mcp_config is not None
    assert "mcpServers" in agent_context.plugin_mcp_config

    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], agent_context=agent_context)

    # Mock create_mcp_tools to capture what config was passed
    captured_config = {}

    def mock_create_mcp_tools(config, timeout):
        captured_config["config"] = config
        return []

    with patch("openhands.sdk.agent.base.create_mcp_tools", mock_create_mcp_tools):
        Conversation(agent=agent, visualizer=None)

    # Verify the merged config was passed to create_mcp_tools
    assert "config" in captured_config
    assert "mcpServers" in captured_config["config"]
    assert "plugin-server" in captured_config["config"]["mcpServers"]


def test_agent_plugin_mcp_config_overrides_base(tmp_path: Path):
    """Test that plugin MCP config takes precedence over base config.

    Note: This is a shallow merge at the top level, so if both configs have
    'mcpServers', the plugin's mcpServers completely replaces the base's.
    """
    plugin_mcp = {
        "mcpServers": {
            "plugin-server": {
                "command": "plugin-command",
                "args": ["plugin-args"],
            }
        }
    }
    plugin_dir = create_test_plugin_with_mcp(tmp_path, plugin_mcp)

    agent_context = AgentContext(plugin_source=str(plugin_dir))

    # Create agent with base MCP config - plugin's mcpServers will override entirely
    base_mcp = {
        "mcpServers": {
            "base-server": {
                "command": "base-command",
                "args": ["base-args"],
            },
        },
        "otherKey": {"preserved": True},  # Keys not in plugin config are preserved
    }

    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], agent_context=agent_context, mcp_config=base_mcp)

    captured_config = {}

    def mock_create_mcp_tools(config, timeout):
        captured_config["config"] = config
        return []

    with patch("openhands.sdk.agent.base.create_mcp_tools", mock_create_mcp_tools):
        Conversation(agent=agent, visualizer=None)

    # Verify the merged config
    assert "config" in captured_config
    config = captured_config["config"]

    # Plugin's mcpServers completely replaces base's mcpServers (shallow merge)
    assert "plugin-server" in config["mcpServers"]
    assert "base-server" not in config["mcpServers"]

    # Other keys from base are preserved
    assert config["otherKey"]["preserved"] is True


def test_agent_no_plugin_mcp_config():
    """Test that agent works without plugin MCP config."""
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], agent_context=AgentContext())

    captured_config = {}

    def mock_create_mcp_tools(config, timeout):
        captured_config["config"] = config
        return []

    with patch("openhands.sdk.agent.base.create_mcp_tools", mock_create_mcp_tools):
        Conversation(agent=agent, visualizer=None)

    # No MCP config, so create_mcp_tools shouldn't be called
    assert "config" not in captured_config


def test_agent_base_mcp_config_only():
    """Test that base MCP config works without plugin."""
    base_mcp = {
        "mcpServers": {
            "base-server": {
                "command": "base-command",
            }
        }
    }

    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], mcp_config=base_mcp)

    captured_config = {}

    def mock_create_mcp_tools(config, timeout):
        captured_config["config"] = config
        return []

    with patch("openhands.sdk.agent.base.create_mcp_tools", mock_create_mcp_tools):
        Conversation(agent=agent, visualizer=None)

    # Base config should be used
    assert "config" in captured_config
    assert captured_config["config"] == base_mcp
