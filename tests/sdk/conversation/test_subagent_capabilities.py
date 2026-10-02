"""Reject lazy and runtime capability expansion before MCP connections."""

import json
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openhands.sdk import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.plugin import PluginSource
from openhands.sdk.subagent.capabilities import (
    SubagentCapabilityError,
    SubagentCapabilityLimits,
    prepare_subagent,
)
from openhands.sdk.testing import TestLLM


def _plugin(tmp_path: Path, servers: dict) -> Path:
    path = tmp_path / "plugin"
    (path / ".claude-plugin").mkdir(parents=True)
    (path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "capability-test", "version": "1.0.0"})
    )
    (path / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
    return path


def _child() -> Agent:
    config = {"allowed": MCPServer(command="safe-command")}
    parent = Agent(
        llm=TestLLM(model="test-model"),
        mcp_config=config,
        subagent_capability_limits=SubagentCapabilityLimits(
            tool_names=(), mcp_server_names=("allowed",)
        ),
    )
    return prepare_subagent(
        parent=parent,
        child=Agent(llm=parent.llm, mcp_config=config),
        limits=parent.subagent_capability_limits,
    )


@pytest.mark.parametrize("server", ["new-server", "allowed"])
def test_lazy_plugin_cannot_add_or_replace_mcp(tmp_path: Path, server: str) -> None:
    path = _plugin(tmp_path, {server: {"command": "unexpected-command"}})
    provider = MagicMock()
    with closing(
        LocalConversation(
            agent=_child(),
            workspace=tmp_path,
            visualizer=None,
            plugins=[PluginSource(source=str(path))],
            mcp_tool_provider=provider,
        )
    ) as conversation:
        with pytest.raises(SubagentCapabilityError):
            conversation._ensure_agent_ready()
        provider.create_tools.assert_not_called()
        assert conversation.agent.mcp_config["allowed"].command == "safe-command"


def test_allowed_mcp_reaches_provider(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.create_tools.return_value.tools = []
    with closing(
        LocalConversation(
            agent=_child(),
            workspace=tmp_path,
            visualizer=None,
            mcp_tool_provider=provider,
        )
    ) as conversation:
        conversation._ensure_agent_ready()
        provider.create_tools.assert_called_once()
        assert (
            provider.create_tools.call_args.args[0]["allowed"].command == "safe-command"
        )


@pytest.mark.parametrize("ready", [False, True])
def test_runtime_plugin_rejected_before_connect_or_mutation(
    tmp_path: Path, ready: bool
) -> None:
    path = _plugin(tmp_path, {"allowed": {"command": "unexpected-command"}})
    provider = MagicMock()
    provider.create_tools.return_value.tools = []
    registry = MagicMock()
    registry.resolve_plugin.return_value = PluginSource(source=str(path))
    with closing(
        LocalConversation(
            agent=_child(),
            workspace=tmp_path,
            visualizer=None,
            mcp_tool_provider=provider,
        )
    ) as conversation:
        conversation._ensure_plugins_loaded()
        if ready:
            conversation._ensure_agent_ready()
        provider.create_tools.reset_mock()
        previous_agent = conversation.agent
        with patch.object(
            LocalConversation,
            "_marketplace_registry_from_context",
            return_value=registry,
        ):
            with pytest.raises(SubagentCapabilityError):
                conversation.load_plugin("capability-test@local")
        provider.create_tools.assert_not_called()
        assert conversation.agent is previous_agent
        assert conversation._state.agent is previous_agent


def test_provider_failure_keeps_inherited_ceiling(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.create_tools.side_effect = RuntimeError("MCP unavailable")
    child = _child()
    with closing(
        LocalConversation(
            agent=child,
            workspace=tmp_path,
            visualizer=None,
            mcp_tool_provider=provider,
        )
    ) as conversation:
        with pytest.raises(RuntimeError, match="MCP unavailable"):
            conversation._ensure_agent_ready()
        assert (
            conversation.agent.subagent_capability_limits
            == child.subagent_capability_limits
        )
        with pytest.raises(SubagentCapabilityError):
            conversation._runtime_mcp_tools({"new-server": MCPServer(command="unsafe")})
        assert provider.create_tools.call_count == 1
