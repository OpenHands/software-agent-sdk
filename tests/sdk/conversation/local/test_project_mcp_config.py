from __future__ import annotations

import json
from pathlib import Path

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.plugin import PluginSource
from openhands.sdk.testing import TestLLM


def _create_agent(mcp_config: dict | None = None) -> Agent:
    return Agent(
        llm=TestLLM.from_messages(
            [
                Message(
                    role="assistant",
                    content=[TextContent(text="ok")],
                )
            ],
            model="test-model",
        ),
        tools=[],
        include_default_tools=[],
        mcp_config=mcp_config or {},
    )


def _create_plugin(plugin_dir: Path, mcp_config: dict) -> Path:
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": plugin_dir.name,
                "version": "1.0.0",
                "description": f"Test plugin {plugin_dir.name}",
            }
        )
    )
    (plugin_dir / ".mcp.json").write_text(json.dumps(mcp_config))
    return plugin_dir


def test_project_mcp_config_loads_from_repo_root_for_subdirectory_workspace(
    tmp_path: Path,
):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"repo-root": {"command": "repo-root"}}})
    )
    workspace = tmp_path / "subdir"
    workspace.mkdir()

    conversation = LocalConversation(
        agent=_create_agent(),
        workspace=workspace,
        visualizer=None,
    )
    conversation._ensure_plugins_loaded()

    assert (
        conversation.agent.mcp_config["mcpServers"]["repo-root"]["command"]
        == "repo-root"
    )
    conversation.close()


def test_project_mcp_config_prefers_dot_openhands_file_over_repo_root(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"shared": {"command": "repo-root"}}})
    )
    openhands_dir = tmp_path / ".openhands"
    openhands_dir.mkdir()
    (openhands_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"shared": {"command": "preferred"}}})
    )

    conversation = LocalConversation(
        agent=_create_agent(),
        workspace=tmp_path,
        visualizer=None,
    )
    conversation._ensure_plugins_loaded()

    assert (
        conversation.agent.mcp_config["mcpServers"]["shared"]["command"] == "preferred"
    )
    conversation.close()


def test_project_mcp_config_expands_environment_variables(
    tmp_path: Path,
    monkeypatch,
):
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "env-server": {
                        "type": "http",
                        "url": "${MCP_URL:-https://default.example.com}/mcp",
                        "headers": {
                            "Authorization": "Bearer ${API_TOKEN}",
                        },
                    }
                }
            }
        )
    )
    monkeypatch.setenv("API_TOKEN", "secret-token")

    conversation = LocalConversation(
        agent=_create_agent(),
        workspace=tmp_path,
        visualizer=None,
    )
    conversation._ensure_plugins_loaded()

    config = conversation.agent.mcp_config["mcpServers"]["env-server"]
    assert config["url"] == "https://default.example.com/mcp"
    assert config["headers"]["Authorization"] == "Bearer secret-token"
    conversation.close()


def test_agent_mcp_config_overrides_project_mcp_config(tmp_path: Path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"shared": {"command": "project"}}})
    )

    conversation = LocalConversation(
        agent=_create_agent({"mcpServers": {"shared": {"command": "agent-override"}}}),
        workspace=tmp_path,
        visualizer=None,
    )
    conversation._ensure_plugins_loaded()

    assert (
        conversation.agent.mcp_config["mcpServers"]["shared"]["command"]
        == "agent-override"
    )
    conversation.close()


def test_plugin_mcp_config_overrides_project_and_agent_mcp_config(tmp_path: Path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared": {"command": "project"},
                    "project-only": {"command": "project-only"},
                }
            }
        )
    )
    plugin_dir = _create_plugin(
        tmp_path / "plugin",
        {"mcpServers": {"shared": {"command": "plugin-override"}}},
    )

    conversation = LocalConversation(
        agent=_create_agent({"mcpServers": {"shared": {"command": "agent-override"}}}),
        workspace=tmp_path,
        plugins=[PluginSource(source=str(plugin_dir))],
        visualizer=None,
    )
    conversation._ensure_plugins_loaded()

    assert (
        conversation.agent.mcp_config["mcpServers"]["shared"]["command"]
        == "plugin-override"
    )
    assert (
        conversation.agent.mcp_config["mcpServers"]["project-only"]["command"]
        == "project-only"
    )
    conversation.close()
