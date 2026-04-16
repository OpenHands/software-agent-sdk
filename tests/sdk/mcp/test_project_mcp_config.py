"""Tests for project-level .mcp.json discovery and merge behavior."""

import json
import logging
from pathlib import Path

import pytest

from openhands.sdk import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.mcp.merge import merge_mcp_configs
from openhands.sdk.mcp.project_config import (
    _find_project_mcp_json,
    load_project_mcp_config,
)
from openhands.sdk.plugin import PluginSource
from openhands.sdk.testing import TestLLM


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

    found = _find_project_mcp_json(root)
    assert found == oh / ".mcp.json"


def test_find_project_mcp_json_falls_back_to_root(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / ".mcp.json").write_text(json.dumps(_minimal_mcp_file()))

    assert _find_project_mcp_json(root) == root / ".mcp.json"


def test_merge_mcp_configs_overlay_wins() -> None:
    base = {"mcpServers": {"a": {"command": "x"}, "b": {"command": "y"}}}
    overlay = {"mcpServers": {"a": {"command": "z"}}}
    merged = merge_mcp_configs(base, overlay)
    assert merged["mcpServers"]["a"]["command"] == "z"
    assert merged["mcpServers"]["b"]["command"] == "y"


@pytest.fixture
def mock_llm() -> TestLLM:
    return TestLLM.from_messages([])


@pytest.fixture
def basic_agent(mock_llm: TestLLM) -> Agent:
    return Agent(llm=mock_llm, tools=[])


def test_load_project_mcp_config_expands_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("OH_PROJECT_MCP_TEST", "expanded-cmd")
    cfg = {
        "mcpServers": {
            "s": {
                "command": "${OH_PROJECT_MCP_TEST}",
                "args": ["${MISSING:-default-arg}"],
            }
        }
    }
    (ws / ".mcp.json").write_text(json.dumps(cfg))

    loaded = load_project_mcp_config(ws)
    assert loaded is not None
    assert loaded["mcpServers"]["s"]["command"] == "expanded-cmd"
    assert loaded["mcpServers"]["s"]["args"] == ["default-arg"]


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


def test_load_project_mcp_config_invalid_json_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".mcp.json").write_text("not json")

    with caplog.at_level(logging.WARNING):
        assert load_project_mcp_config(ws) is None
    assert "Ignoring invalid project MCP config" in caplog.text
