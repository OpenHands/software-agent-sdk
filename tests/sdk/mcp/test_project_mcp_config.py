"""Tests for project-level .mcp.json discovery and merge behavior."""

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent, LLM
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.mcp.project_config import find_project_mcp_json, try_load_project_mcp_config
from openhands.sdk.plugin import PluginSource, merge_mcp_configs


def _minimal_mcp_file() -> dict:
    return {"mcpServers": {"proj": {"command": "echo", "args": ["mcp"]}}}


def test_find_project_mcp_json_prefers_openhands_dir(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / ".mcp.json").write_text(json.dumps(_minimal_mcp_file()))
    oh = root / ".openhands"
    oh.mkdir()
    preferred = _minimal_mcp_file()
    preferred["mcpServers"]["proj"]["args"] = ["preferred"]
    (oh / ".mcp.json").write_text(json.dumps(preferred))

    found = find_project_mcp_json(root)
    assert found == oh / ".mcp.json"


def test_find_project_mcp_json_falls_back_to_root(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / ".mcp.json").write_text(json.dumps(_minimal_mcp_file()))

    assert find_project_mcp_json(root) == root / ".mcp.json"


def test_merge_mcp_configs_overlay_wins() -> None:
    base = {"mcpServers": {"a": {"command": "x"}, "b": {"command": "y"}}}
    overlay = {"mcpServers": {"a": {"command": "z"}}}
    merged = merge_mcp_configs(base, overlay)
    assert merged["mcpServers"]["a"]["command"] == "z"
    assert merged["mcpServers"]["b"]["command"] == "y"


@pytest.fixture
def mock_llm():
    return LLM(model="test/model", api_key=SecretStr("test-key"))


@pytest.fixture
def basic_agent(mock_llm):
    return Agent(llm=mock_llm, tools=[])


def test_trust_project_mcp_merges_under_user_config(
    tmp_path: Path, basic_agent: Agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".mcp.json").write_text(json.dumps(_minimal_mcp_file()))

    agent = basic_agent.model_copy(
        update={
            "mcp_config": {
                "mcpServers": {"proj": {"command": "user-wins", "args": []}},
            }
        }
    )

    monkeypatch.setattr(
        "openhands.sdk.agent.base.create_mcp_tools",
        lambda config, timeout: [],
    )

    conv = LocalConversation(
        agent=agent,
        workspace=ws,
        visualizer=None,
        trust_project_mcp=True,
    )
    conv._ensure_agent_ready()

    assert conv.agent.mcp_config["mcpServers"]["proj"]["command"] == "user-wins"
    conv.close()


def test_project_mcp_skipped_without_trust(tmp_path: Path, basic_agent: Agent) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".mcp.json").write_text(json.dumps(_minimal_mcp_file()))

    conv = LocalConversation(
        agent=basic_agent,
        workspace=ws,
        visualizer=None,
        trust_project_mcp=False,
    )
    conv._ensure_plugins_loaded()

    assert conv.agent.mcp_config == {}
    conv.close()


def test_project_mcp_layer_before_plugin(
    tmp_path: Path, basic_agent: Agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"shared": {"command": "from-project"}}})
    )

    plugin_dir = tmp_path / "plugin"
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "t",
                "version": "1.0.0",
                "description": "d",
            }
        )
    )
    (plugin_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"shared": {"command": "from-plugin"}}})
    )

    monkeypatch.setattr(
        "openhands.sdk.agent.base.create_mcp_tools",
        lambda config, timeout: [],
    )

    conv = LocalConversation(
        agent=basic_agent,
        workspace=ws,
        plugins=[PluginSource(source=str(plugin_dir))],
        visualizer=None,
        trust_project_mcp=True,
    )
    conv._ensure_agent_ready()

    assert conv.agent.mcp_config["mcpServers"]["shared"]["command"] == "from-plugin"
    conv.close()


def test_try_load_project_mcp_config_invalid_json_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".mcp.json").write_text("not json")

    import logging

    with caplog.at_level(logging.WARNING):
        assert try_load_project_mcp_config(ws) is None
    assert "Ignoring invalid project MCP config" in caplog.text
